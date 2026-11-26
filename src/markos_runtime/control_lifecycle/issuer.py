"""Separate persisted synthetic issuer and bounded resource-bound client; no real IdP."""

import asyncio
import hmac
import http.client
import json
import os
import ssl
import stat
import tempfile
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit
from uuid import UUID

from cryptography.hazmat.primitives import serialization  # type: ignore[import-not-found]
from cryptography.hazmat.primitives.asymmetric import ec  # type: ignore[import-not-found]

from markos_runtime.api.identity import (
    TOKEN_PATTERN,
    FixtureIssuer,
    IdentityDenied,
    IdentityUnavailable,
    strict_json,
)
from markos_runtime.http_bounds import bounded_connection


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


class DurableFixtureIssuer(FixtureIssuer):
    def __init__(self, state_file: Path, seed_file: Path) -> None:
        super().__init__()
        self.state_file = state_file
        if state_file.exists():
            value = private_json(state_file)
            if (
                set(value)
                != {"schema_version", "key", "kid", "accounts", "codes", "sessions", "refresh"}
                or value["schema_version"] != 1
            ):
                raise IdentityUnavailable("invalid fixture snapshot")
            key = serialization.load_pem_private_key(value["key"].encode(), password=None)
            if not isinstance(key, ec.EllipticCurvePrivateKey) or not isinstance(
                key.curve, ec.SECP256R1
            ):
                raise IdentityUnavailable("fixed fixture key required")
            self._key = key
            self.kid = value["kid"]
            self._accounts = dict(value["accounts"])
            self._codes = {
                k: (v[0], v[1], v[2], UUID(v[3]), time.monotonic() + max(0, v[4] - time.time()))
                for k, v in value["codes"].items()
            }
            self._sessions = {UUID(k): (v[0], v[1]) for k, v in value["sessions"].items()}
            self._refresh = {k: (v[0], UUID(v[1]), v[2]) for k, v in value["refresh"].items()}
            if any(
                len(x) > 100 for x in (self._accounts, self._codes, self._sessions, self._refresh)
            ):
                raise IdentityUnavailable("fixture capacity exceeded")
        else:
            value = private_json(seed_file, 65536)
            if set(value) != {"schema_version", "accounts"} or value["schema_version"] != 1:
                raise IdentityUnavailable("explicit fixture seed required")
            for account in value["accounts"]:
                if set(account) != {"subject", "credential"}:
                    raise IdentityUnavailable("fixed fixture seed required")
                self.add_account(account["subject"], account["credential"])
            self.commit()

    def commit(self) -> None:
        pem = self._key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        ).decode()
        save_private(
            self.state_file,
            {
                "schema_version": 1,
                "key": pem,
                "kid": self.kid,
                "accounts": self._accounts,
                "codes": {
                    k: [v[0], v[1], v[2], str(v[3]), time.time() + max(0, v[4] - time.monotonic())]
                    for k, v in self._codes.items()
                },
                "sessions": {str(k): list(v) for k, v in self._sessions.items()},
                "refresh": {k: [v[0], str(v[1]), v[2]] for k, v in self._refresh.items()},
            },
        )


class RemoteFixtureIssuer(FixtureIssuer):
    def __init__(self, endpoint: str, ca_file: Path, credential: str) -> None:
        u = urlsplit(endpoint)
        if (
            u.scheme != "https"
            or u.hostname != "127.0.0.1"
            or not u.port
            or not 1024 <= u.port <= 65535
            or u.path != "/fixture"
            or u.query
            or u.fragment
            or u.username
            or u.password
            or u.netloc != f"127.0.0.1:{u.port}"
            or not TOKEN_PATTERN.fullmatch(credential)
        ):
            raise IdentityUnavailable("explicit synthetic issuer endpoint required")
        self.endpoint, self.port, self.credential = endpoint, u.port, credential
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
        }:
            raise IdentityDenied("unsupported fixture operation")
        raw = json.dumps(body, allow_nan=False).encode()
        if len(raw) > 65536:
            raise IdentityDenied("bounded fixture request required")
        conn = http.client.HTTPSConnection("127.0.0.1", self.port, context=self.tls, timeout=2)
        try:
            with bounded_connection(conn, time.monotonic() + 2.5):
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
        async with asyncio.timeout(3):
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


def issuer_app(
    issuer: DurableFixtureIssuer, endpoint: str, api_token: str, worker_token: str, admin_token: str
) -> Any:
    from fastapi import FastAPI, Request  # type: ignore[import-not-found]
    from fastapi.responses import JSONResponse  # type: ignore[import-not-found]

    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    lock = asyncio.Lock()
    failed = False

    async def dispatch(request: Request) -> Any:
        nonlocal failed
        try:
            async with asyncio.timeout(3):
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
                    "api": {"authorize", "exchange", "refresh", "keys", "health", "session-active"},
                    "worker": {"health", "session-active"},
                    "admin": {"revoke", "rotate", "health"},
                }
                if scope is None or op not in allowed[scope] or failed:
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
                }
                if set(body) != fields[op]:
                    raise IdentityDenied("closed fixture request")
                async with lock:
                    try:
                        value: Any
                        if op == "health":
                            value = {"ready": True, "profile": "persisted-synthetic-issuer-v1"}
                        elif op == "keys":
                            value = json.loads(await issuer.keys())
                        elif op == "authorize":
                            value = issuer.authorize(
                                body["credential"], body["challenge"], body["nonce"]
                            )
                        elif op == "exchange":
                            value = await issuer.exchange(body["code"], body["verifier"])
                        elif op == "refresh":
                            value = await issuer.refresh(body["token"])
                        elif op == "session-active":
                            value = await issuer.session_active(
                                body["subject"], UUID(body["session_id"])
                            )
                        elif op == "revoke":
                            issuer.revoke(UUID(body["session_id"]))
                            value = True
                        else:
                            issuer.rotate_key()
                            value = True
                    finally:
                        if op not in {"health", "keys", "session-active"}:
                            try:
                                issuer.commit()
                            except Exception:
                                failed = True
                                raise
                return JSONResponse({"value": value}, headers={"Cache-Control": "no-store"})
        except IdentityDenied:
            return JSONResponse({"error": "denied"}, status_code=401)
        except Exception:
            return JSONResponse({"error": "unavailable"}, status_code=503)

    app.add_api_route("/fixture/{operation}", dispatch, methods=["POST"])
    return app
