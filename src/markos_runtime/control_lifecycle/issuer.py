"""Separate synthetic issuer: PostgreSQL state, bounded resource-bound client, no real IdP."""

import asyncio
import hmac
import http.client
import json
import os
import ssl
import stat
import tempfile
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Self, cast
from urllib.parse import urlsplit
from uuid import UUID, uuid4

import jwt  # type: ignore[import-not-found]
import psycopg
import psycopg.sql
from cryptography.hazmat.primitives import serialization  # type: ignore[import-not-found]
from cryptography.hazmat.primitives.asymmetric import ec  # type: ignore[import-not-found]
from psycopg.conninfo import conninfo_to_dict
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from psycopg_pool import AsyncConnectionPool, PoolTimeout

from markos_runtime.api.identity import (
    AUDIENCE,
    ISSUER,
    TOKEN_PATTERN,
    FixtureIssuer,
    IdentityDenied,
    IdentityUnavailable,
    SecretCodec,
    challenge,
    opaque,
    strict_json,
)
from markos_runtime.api.schema import EXPECTED_MIGRATIONS
from markos_runtime.contracts.base import loopback_https
from markos_runtime.http_bounds import bounded_connection
from markos_runtime.store.database import _SafeConnection


def private_json(path: Path, limit: int = 1048576) -> dict[str, Any]:
    try:
        if path.is_symlink() or not path.is_absolute():
            raise ValueError
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(descriptor, "rb") as source:
            info = os.fstat(source.fileno())
            if (
                not stat.S_ISREG(info.st_mode)
                or info.st_uid != os.getuid()
                or info.st_mode & 0o077
                or info.st_size > limit
            ):
                raise ValueError
            return strict_json(source.read(limit + 1), limit)
    except (OSError, ValueError):
        raise IdentityUnavailable("private fixture state unavailable") from None


def save_private(path: Path, value: dict[str, Any]) -> None:
    parent = path.parent
    info = parent.stat()
    if (
        not path.is_absolute()
        or parent.is_symlink()
        or info.st_uid != os.getuid()
        or info.st_mode & 0o077
        or path.is_symlink()
    ):
        raise IdentityUnavailable("private fixture folder required")
    raw = json.dumps(value, allow_nan=False, separators=(",", ":")).encode()
    if len(raw) > 1048576:
        raise IdentityUnavailable("fixture capacity unavailable")
    temporary = None
    try:
        fd, temporary = tempfile.mkstemp(prefix=".fixture-", dir=parent)
        with os.fdopen(fd, "wb") as output:
            os.fchmod(output.fileno(), 0o600)
            output.write(raw)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
        temporary = None
        directory = os.open(parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    except OSError:
        raise IdentityUnavailable("fixture state commit unavailable") from None
    finally:
        if temporary:
            Path(temporary).unlink(missing_ok=True)


class IssuerStore:
    """The issuer's private PostgreSQL state, reached only through `markos_issuer` functions."""

    def __init__(self, pool: AsyncConnectionPool[_SafeConnection]) -> None:
        self._pool = pool

    @classmethod
    @asynccontextmanager
    async def connect(cls, dsn: str) -> AsyncIterator[Self]:
        try:
            info = conninfo_to_dict(dsn)
            if not all(info.get(k) for k in ("host", "dbname", "user")):
                raise IdentityUnavailable("explicit issuer database target required")
        except psycopg.Error:
            raise IdentityUnavailable("invalid issuer database target") from None
        pool: AsyncConnectionPool[_SafeConnection] = AsyncConnectionPool(
            dsn,
            connection_class=_SafeConnection,
            min_size=0,
            max_size=2,
            timeout=5,
            open=False,
            reconnect_timeout=0,
            kwargs={"autocommit": True, "row_factory": dict_row, "connect_timeout": 5},
        )
        try:
            await pool.open()
            async with pool.connection() as connection:
                row = await (
                    await connection.execute(
                        "SELECT r.rolsuper OR r.rolbypassrls OR r.rolcreaterole OR "
                        "pg_has_role(current_user,n.nspowner,'MEMBER') AS unsafe "
                        "FROM pg_roles r CROSS JOIN pg_namespace n "
                        "WHERE r.rolname=session_user AND n.nspname='markos_issuer'"
                    )
                ).fetchone()
                if row is None or row["unsafe"]:
                    raise IdentityUnavailable("non-admin issuer identity required")
            yield cls(pool)
        except (psycopg.Error, PoolTimeout):
            raise IdentityUnavailable("issuer database unavailable") from None
        finally:
            await pool.close()

    async def call(self, function: str, *args: object) -> Any:
        # Function names are fixed call sites in this module, never request input.
        try:
            async with self._pool.connection() as conn, conn.transaction():
                await conn.execute("SET LOCAL statement_timeout='5s'")
                await conn.execute("SET LOCAL lock_timeout='1s'")
                query = psycopg.sql.SQL("SELECT markos_issuer.{}({}) AS value").format(
                    psycopg.sql.Identifier(function),
                    psycopg.sql.SQL(",").join(psycopg.sql.Placeholder() for _ in args),
                )
                row = await (await conn.execute(query, args)).fetchone()
                assert row is not None
                return row["value"]
        except (psycopg.Error, PoolTimeout):
            raise IdentityUnavailable("issuer state unavailable") from None


class StoredIssuer:
    """Synthetic issuer whose keys, codes, sessions and refresh tokens live in PostgreSQL.

    Fixture accounts come from the private seed file on every start and are never stored.
    Production has no seed file: Google sign-in is then the only way in.
    """

    def __init__(
        self, store: IssuerStore, codec: SecretCodec, seed_file: Path | None, google: Any = None
    ) -> None:
        # Google sign-in is off unless an explicit GoogleSignIn adapter is given.
        self.store, self.codec, self.google = store, codec, google
        self._accounts: dict[str, str] = {}
        if seed_file is None:
            if google is None:
                raise IdentityUnavailable("Google sign-in required without a fixture seed")
            return
        value = private_json(seed_file, 65536)
        if set(value) != {"schema_version", "accounts"} or value["schema_version"] != 1:
            raise IdentityUnavailable("explicit fixture seed required")
        accounts = value["accounts"]
        if not isinstance(accounts, list) or len(accounts) > 100:
            raise IdentityUnavailable("bounded fixture accounts required")
        for account in accounts:
            if (
                not isinstance(account, dict)
                or set(account) != {"subject", "credential"}
                or not isinstance(account["subject"], str)
                or not 1 <= len(account["subject"]) <= 256
                or not isinstance(account["credential"], str)
                or not TOKEN_PATTERN.fullmatch(account["credential"])
            ):
                raise IdentityUnavailable("fixed fixture seed required")
            self._accounts[account["credential"]] = account["subject"]

    @staticmethod
    def _new_key() -> tuple[str, ec.EllipticCurvePrivateKey]:
        return uuid4().hex, ec.generate_private_key(ec.SECP256R1())

    def _seal(self, key: ec.EllipticCurvePrivateKey) -> str:
        pem = key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        ).decode()
        return self.codec.seal(pem)

    async def start(self) -> None:
        if not await self.store.call("issuer_ready", Jsonb(EXPECTED_MIGRATIONS)):
            raise IdentityUnavailable("issuer schema not ready")
        if await self.store.call("issuer_key") is None:
            kid, key = self._new_key()
            await self.store.call("issuer_install_key", kid, self._seal(key), False)

    async def _key(self) -> tuple[str, ec.EllipticCurvePrivateKey]:
        row = await self.store.call("issuer_key")
        if row is None:
            raise IdentityUnavailable("issuer key unavailable")
        try:
            pem = self.codec.open(row["key_cipher"])
        except IdentityDenied:
            raise IdentityUnavailable("issuer key unreadable") from None
        key = serialization.load_pem_private_key(pem.encode(), password=None)
        if not isinstance(key, ec.EllipticCurvePrivateKey) or not isinstance(
            key.curve, ec.SECP256R1
        ):
            raise IdentityUnavailable("fixed issuer key required")
        return str(row["kid"]), key

    async def _tokens(self, row: dict[str, Any], refresh: str) -> tuple[str, str]:
        kid, key = await self._key()
        now = int(row["now"])
        claims = {
            "iss": ISSUER,
            "aud": AUDIENCE,
            "sub": row["subject"],
            "sid": str(UUID(str(row["session_id"]))),
            "nonce": row["nonce"],
            "token_kind": "human-access",
            "iat": now,
            "nbf": now,
            "exp": now + 300,
        }
        token = jwt.encode(claims, key, algorithm="ES256", headers={"kid": kid, "typ": "JWT"})
        return token, refresh

    async def keys(self) -> bytes:
        kid, key = await self._key()
        jwk = cast(dict[str, Any], json.loads(jwt.algorithms.ECAlgorithm.to_jwk(key.public_key())))
        jwk.update(kid=kid, alg="ES256", use="sig")
        return json.dumps({"keys": [jwk]}).encode()

    async def authorize(self, credential: str, pkce_challenge: str, nonce: str) -> str:
        subject = self._accounts.get(credential)
        if (
            subject is None
            or not TOKEN_PATTERN.fullmatch(pkce_challenge)
            or not TOKEN_PATTERN.fullmatch(nonce)
        ):
            raise IdentityDenied("fixture authorization denied")
        code = opaque()
        try:
            await self.store.call(
                "issuer_authorize",
                self.codec.lookup("issuer-code", code),
                subject,
                pkce_challenge,
                nonce,
            )
        except IdentityUnavailable:
            raise IdentityUnavailable("issuer authorization unavailable") from None
        return code

    async def exchange(self, code: str, verifier: str) -> tuple[str, str]:
        if not TOKEN_PATTERN.fullmatch(code) or not TOKEN_PATTERN.fullmatch(verifier):
            raise IdentityDenied("fixture exchange denied")
        refresh = opaque()
        row = await self.store.call(
            "issuer_exchange",
            self.codec.lookup("issuer-code", code),
            challenge(verifier),
            self.codec.lookup("issuer-refresh", refresh),
        )
        if row is None:
            raise IdentityDenied("fixture exchange denied")
        return await self._tokens(row, refresh)

    async def refresh(self, token: str) -> tuple[str, str]:
        if not TOKEN_PATTERN.fullmatch(token):
            raise IdentityDenied("fixture refresh denied")
        refresh = opaque()
        row = await self.store.call(
            "issuer_refresh",
            self.codec.lookup("issuer-refresh", token),
            self.codec.lookup("issuer-refresh", refresh),
        )
        if row is None:
            raise IdentityDenied("fixture refresh denied")
        return await self._tokens(row, refresh)

    async def session_active(self, subject: str, session_id: UUID) -> bool:
        return bool(await self.store.call("issuer_session_active", subject, session_id))

    async def revoke(self, session_id: UUID) -> None:
        await self.store.call("issuer_revoke", session_id)

    async def rotate_key(self) -> None:
        kid, key = self._new_key()
        await self.store.call("issuer_install_key", kid, self._seal(key), True)

    async def google_begin(self, pkce_challenge: str, nonce: str) -> dict[str, str]:
        """Starts a Google sign-in bound to the gateway's PKCE challenge and nonce."""
        if (
            self.google is None
            or not TOKEN_PATTERN.fullmatch(pkce_challenge)
            or not TOKEN_PATTERN.fullmatch(nonce)
        ):
            raise IdentityDenied("Google sign-in denied")
        state, google_nonce, verifier = opaque(), opaque(), opaque()
        await self.store.call(
            "issuer_google_begin",
            self.codec.lookup("issuer-google-state", state),
            self.codec.lookup("issuer-google-nonce", google_nonce),
            self.codec.seal(verifier),
            pkce_challenge,
            nonce,
        )
        url = self.google.authorization_url(state, challenge(verifier), google_nonce)
        return {"url": url, "state": state}

    async def google_finish(self, state: str, code: str) -> str:
        """Finishes a Google sign-in. Returns a one-use MarkOS code for the gateway's flow."""
        if self.google is None or not TOKEN_PATTERN.fullmatch(state):
            raise IdentityDenied("Google sign-in denied")
        row = await self.store.call(
            "issuer_google_take", self.codec.lookup("issuer-google-state", state)
        )
        if row is None:
            raise IdentityDenied("Google sign-in denied")
        subject, nonce = await self.google.sign_in(code, self.codec.open(row["verifier_cipher"]))
        if not hmac.compare_digest(
            self.codec.lookup("issuer-google-nonce", nonce), row["nonce_hash"]
        ):
            raise IdentityDenied("Google sign-in denied")
        markos_code = opaque()
        await self.store.call(
            "issuer_authorize",
            self.codec.lookup("issuer-code", markos_code),
            subject,
            row["challenge"],
            row["nonce"],
        )
        return markos_code


def slow(operation: str) -> float:
    """Seconds an issuer request may take. Finishing a Google sign-in calls Google twice."""
    return 10.0 if operation == "google-finish" else 2.5


class RemoteFixtureIssuer(FixtureIssuer):
    def __init__(
        self, endpoint: str, ca_file: Path, credential: str, *, production: bool = False
    ) -> None:
        # The fixture issuer is on loopback. In production it is `issuer` on the private
        # Compose network, and TLS checks that name.
        host = "issuer" if production else "127.0.0.1"
        try:
            port = loopback_https(
                endpoint, paths=frozenset({"/fixture"}), hosts=frozenset({host}), exact=False
            )
            if urlsplit(endpoint).netloc != f"{host}:{port}":
                raise ValueError
        except ValueError:
            raise IdentityUnavailable("explicit synthetic issuer endpoint required") from None
        if not TOKEN_PATTERN.fullmatch(credential):
            raise IdentityUnavailable("explicit synthetic issuer endpoint required")
        self.endpoint, self.host, self.port, self.credential = endpoint, host, port, credential
        self.tls = ssl.create_default_context(cafile=str(ca_file))

    def request(self, operation: str, body: dict[str, Any]) -> Any:
        if operation not in {
            "keys",
            "authorize",
            "exchange",
            "refresh",
            "session-active",
            "health",
            "revoke",
            "rotate",
            "google-begin",
            "google-finish",
        }:
            raise IdentityDenied("unsupported fixture operation")
        raw = json.dumps(body, allow_nan=False).encode()
        if len(raw) > 65536:
            raise IdentityDenied("bounded fixture request required")
        limit = slow(operation)
        conn = http.client.HTTPSConnection(
            self.host, self.port, context=self.tls, timeout=limit - 0.5
        )
        try:
            with bounded_connection(conn, time.monotonic() + limit):
                conn.request(
                    "POST",
                    "/fixture/" + operation,
                    body=raw,
                    headers={
                        "Authorization": "Bearer " + self.credential,
                        "Content-Type": "application/json",
                        "X-Markos-Resource": self.endpoint,
                    },
                )
                response = conn.getresponse()
                payload = response.read(65537)
                if response.status == 401:
                    raise IdentityDenied("fixture authorization denied")
                if response.status != 200:
                    raise IdentityUnavailable("fixture issuer unavailable")
                return strict_json(payload, 65536)["value"]
        except IdentityDenied:
            raise
        except (OSError, ValueError, http.client.HTTPException, KeyError):
            raise IdentityUnavailable("fixture issuer unavailable") from None
        finally:
            conn.close()

    async def remote(self, operation: str, body: dict[str, Any]) -> Any:
        async with asyncio.timeout(slow(operation) + 0.5):
            return await asyncio.to_thread(self.request, operation, body)

    def authorize(self, credential: str, pkce_challenge: str, nonce: str) -> str:
        return str(
            self.request(
                "authorize", {"credential": credential, "challenge": pkce_challenge, "nonce": nonce}
            )
        )

    async def authorize_async(self, credential: str, pkce_challenge: str, nonce: str) -> str:
        return str(
            await self.remote(
                "authorize", {"credential": credential, "challenge": pkce_challenge, "nonce": nonce}
            )
        )

    async def keys(self) -> bytes:
        return json.dumps(await self.remote("keys", {})).encode()

    async def exchange(self, code: str, verifier: str) -> tuple[str, str]:
        value = await self.remote("exchange", {"code": code, "verifier": verifier})
        return str(value[0]), str(value[1])

    async def refresh(self, token: str) -> tuple[str, str]:
        value = await self.remote("refresh", {"token": token})
        return str(value[0]), str(value[1])

    async def session_active(self, subject: str, session_id: UUID) -> bool:
        return (
            await self.remote("session-active", {"subject": subject, "session_id": str(session_id)})
        ) is True

    async def google_begin(self, pkce_challenge: str, nonce: str) -> dict[str, str]:
        value = await self.remote("google-begin", {"challenge": pkce_challenge, "nonce": nonce})
        if not isinstance(value, dict) or set(value) != {"url", "state"}:
            raise IdentityUnavailable("fixture issuer unavailable")
        return {"url": str(value["url"]), "state": str(value["state"])}

    async def google_finish(self, state: str, code: str) -> str:
        return str(await self.remote("google-finish", {"state": state, "code": code}))


def issuer_app(
    issuer: StoredIssuer, endpoint: str, api_token: str, worker_token: str, admin_token: str
) -> Any:
    from fastapi import FastAPI, Request  # type: ignore[import-not-found]
    from fastapi.responses import JSONResponse  # type: ignore[import-not-found]

    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)

    async def dispatch(request: Request) -> Any:
        try:
            async with asyncio.timeout(slow(request.path_params["operation"]) + 0.5):
                if (
                    request.url.scheme != "https"
                    or request.headers.get("x-markos-resource") != endpoint
                    or request.headers.get("origin")
                    or request.headers.get("host") != urlsplit(endpoint).netloc
                    or len(request.headers.getlist("authorization")) != 1
                    or len(request.headers.getlist("x-markos-resource")) != 1
                ):
                    raise IdentityDenied("fixture resource denied")
                raw = bytearray()
                async for chunk in request.stream():
                    if len(raw) + len(chunk) > 65536:
                        raise IdentityDenied("fixture request bound")
                    raw.extend(chunk)
                body = strict_json(bytes(raw), 65536)
                op = request.path_params["operation"]
                auth = request.headers.get("authorization", "")
                scope = (
                    "api"
                    if hmac.compare_digest(auth, "Bearer " + api_token)
                    else "worker"
                    if hmac.compare_digest(auth, "Bearer " + worker_token)
                    else "admin"
                    if hmac.compare_digest(auth, "Bearer " + admin_token)
                    else None
                )
                allowed = {
                    "api": {
                        "authorize",
                        "exchange",
                        "refresh",
                        "keys",
                        "health",
                        "session-active",
                        "google-begin",
                        "google-finish",
                    },
                    "worker": {"health", "session-active"},
                    "admin": {"revoke", "rotate", "health"},
                }
                if scope is None or op not in allowed[scope]:
                    raise IdentityDenied("fixture authorization denied")
                fields = {
                    "health": set(),
                    "keys": set(),
                    "authorize": {"credential", "challenge", "nonce"},
                    "exchange": {"code", "verifier"},
                    "refresh": {"token"},
                    "session-active": {"subject", "session_id"},
                    "revoke": {"session_id"},
                    "rotate": set(),
                    "google-begin": {"challenge", "nonce"},
                    "google-finish": {"state", "code"},
                }
                if set(body) != fields[op] or not all(isinstance(v, str) for v in body.values()):
                    raise IdentityDenied("closed fixture request")
                value: Any
                if op == "health":
                    value = {"ready": True, "profile": "persisted-synthetic-issuer-v1"}
                elif op == "keys":
                    value = json.loads(await issuer.keys())
                elif op == "authorize":
                    value = await issuer.authorize(
                        body["credential"], body["challenge"], body["nonce"]
                    )
                elif op == "exchange":
                    value = await issuer.exchange(body["code"], body["verifier"])
                elif op == "refresh":
                    value = await issuer.refresh(body["token"])
                elif op == "session-active":
                    value = await issuer.session_active(body["subject"], UUID(body["session_id"]))
                elif op == "google-begin":
                    value = await issuer.google_begin(body["challenge"], body["nonce"])
                elif op == "google-finish":
                    value = await issuer.google_finish(body["state"], body["code"])
                elif op == "revoke":
                    await issuer.revoke(UUID(body["session_id"]))
                    value = True
                else:
                    await issuer.rotate_key()
                    value = True
                return JSONResponse({"value": value}, headers={"Cache-Control": "no-store"})
        except IdentityDenied:
            return JSONResponse({"error": "denied"}, status_code=401)
        except Exception:
            return JSONResponse({"error": "unavailable"}, status_code=503)

    app.add_api_route("/fixture/{operation}", dispatch, methods=["POST"])
    return app
