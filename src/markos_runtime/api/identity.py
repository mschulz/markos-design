"""Signed-token verification and explicitly synthetic PKCE issuer; never a production IdP."""

import asyncio
import base64
import hashlib
import hmac
import json
import re
import secrets
import time
from dataclasses import dataclass
from typing import Any, cast
from uuid import UUID, uuid4

import jwt  # type: ignore[import-not-found]
from cryptography.fernet import Fernet, InvalidToken  # type: ignore[import-not-found]
from cryptography.hazmat.primitives import hashes  # type: ignore[import-not-found]
from cryptography.hazmat.primitives.asymmetric import ec  # type: ignore[import-not-found]
from cryptography.hazmat.primitives.kdf.hkdf import HKDF  # type: ignore[import-not-found]
from psycopg.types.json import Jsonb

from markos_runtime.control.store import ControlStore

ISSUER = "https://auth.markos.test"
AUDIENCE = "markos-control-test"
TOKEN_PATTERN = re.compile(r"^[A-Za-z0-9_-]{43}$")


class IdentityDenied(ValueError):
    pass


class IdentityUnavailable(ValueError):
    pass


def opaque() -> str:
    return secrets.token_urlsafe(32)


def challenge(verifier: str) -> str:
    return (
        base64.urlsafe_b64encode(hashlib.sha256(verifier.encode("ascii")).digest())
        .decode()
        .rstrip("=")
    )


def strict_json(body: bytes, limit: int) -> dict[str, Any]:
    def unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for k, v in pairs:
            if k in result:
                raise ValueError("duplicate")
            result[k] = v
        return result

    def constant(value: str) -> object:
        raise ValueError("nonfinite")

    try:
        if len(body) > limit:
            raise ValueError("size")
        result = json.loads(body.decode("utf-8"), object_pairs_hook=unique, parse_constant=constant)
        if not isinstance(result, dict):
            raise ValueError("object")
        return cast(dict[str, Any], result)
    except (ValueError, UnicodeError, RecursionError):
        raise IdentityDenied("invalid identity document") from None


class SecretCodec:
    def __init__(self, key: bytes) -> None:
        if len(key) != 32:
            raise ValueError("explicit 32-byte session key required")
        self._mac = HKDF(
            algorithm=hashes.SHA256(), length=32, salt=None, info=b"markos-api-hmac-v1"
        ).derive(key)
        encryption = HKDF(
            algorithm=hashes.SHA256(), length=32, salt=None, info=b"markos-api-encryption-v1"
        ).derive(key)
        self._cipher = Fernet(base64.urlsafe_b64encode(encryption))

    def lookup(self, purpose: str, value: str) -> str:
        return hmac.new(self._mac, (purpose + "\0" + value).encode(), hashlib.sha256).hexdigest()

    def seal(self, value: str) -> str:
        if len(value.encode()) > 8192:
            raise IdentityDenied("identity payload limit")
        result = self._cipher.encrypt(value.encode()).decode()
        assert isinstance(result, str)
        return result

    def open(self, value: str) -> str:
        try:
            result = self._cipher.decrypt(value.encode()).decode()
            if len(result.encode()) > 8192:
                raise ValueError
            assert isinstance(result, str)
            return result
        except (InvalidToken, ValueError, UnicodeError):
            raise IdentityDenied("identity material unavailable") from None


@dataclass(frozen=True)
class VerifiedIdentity:
    subject: str
    provider_session_id: UUID
    nonce: str
    expires_at: int


class FixtureIssuer:
    """Ephemeral, explicit fixture accounts. No network, home discovery or real provider support."""

    def __init__(self) -> None:
        self._key = ec.generate_private_key(ec.SECP256R1())
        self.kid = uuid4().hex
        self._accounts: dict[str, str] = {}
        self._codes: dict[str, tuple[str, str, str, UUID, float]] = {}
        self._sessions: dict[UUID, tuple[str, bool]] = {}
        self._refresh: dict[str, tuple[str, UUID, str]] = {}

    def add_account(self, subject: str, credential: str) -> None:
        if len(self._accounts) >= 100 or not TOKEN_PATTERN.fullmatch(credential):
            raise ValueError("bounded fixture accounts required")
        self._accounts[credential] = subject

    def authorize(self, credential: str, pkce_challenge: str, nonce: str) -> str:
        subject = self._accounts.get(credential)
        if (
            subject is None
            or not TOKEN_PATTERN.fullmatch(pkce_challenge)
            or not TOKEN_PATTERN.fullmatch(nonce)
        ):
            raise IdentityDenied("fixture authorization denied")
        self._codes = {k: v for k, v in self._codes.items() if v[4] > time.monotonic()}
        if len(self._codes) >= 100:
            raise IdentityUnavailable("fixture authorization full")
        code = opaque()
        self._codes[code] = (subject, pkce_challenge, nonce, uuid4(), time.monotonic() + 60)
        return code

    async def authorize_async(self, credential: str, pkce_challenge: str, nonce: str) -> str:
        return self.authorize(credential, pkce_challenge, nonce)

    async def exchange(self, code: str, verifier: str) -> tuple[str, str]:
        entry = self._codes.pop(code, None)
        if (
            entry is None
            or entry[4] <= time.monotonic()
            or not TOKEN_PATTERN.fullmatch(verifier)
            or not hmac.compare_digest(entry[1], challenge(verifier))
        ):
            raise IdentityDenied("fixture exchange denied")
        subject, _, nonce, sid, _ = entry
        if len(self._sessions) >= 100:
            raise IdentityUnavailable("fixture session capacity unavailable")
        self._sessions[sid] = (subject, True)
        return self._tokens(subject, sid, nonce)

    def _tokens(self, subject: str, sid: UUID, nonce: str) -> tuple[str, str]:
        now = int(time.time())
        claims = {
            "iss": ISSUER,
            "aud": AUDIENCE,
            "sub": subject,
            "sid": str(sid),
            "nonce": nonce,
            "token_kind": "human-access",
            "iat": now,
            "nbf": now,
            "exp": now + 300,
        }
        token = jwt.encode(
            claims, self._key, algorithm="ES256", headers={"kid": self.kid, "typ": "JWT"}
        )
        refresh = opaque()
        self._refresh[refresh] = (subject, sid, nonce)
        return token, refresh

    async def keys(self) -> bytes:
        key = cast(
            dict[str, Any], json.loads(jwt.algorithms.ECAlgorithm.to_jwk(self._key.public_key()))
        )
        key.update(kid=self.kid, alg="ES256", use="sig")
        return json.dumps({"keys": [key]}).encode()

    async def session_active(self, subject: str, session_id: UUID) -> bool:
        """Fixture-only current authority using non-secret subject/session metadata."""
        return self._sessions.get(session_id) == (subject, True)

    async def valid_session(self, identity: VerifiedIdentity) -> bool:
        return await self.session_active(identity.subject, identity.provider_session_id)

    async def refresh(self, token: str) -> tuple[str, str]:
        entry = self._refresh.pop(token, None)
        if entry is None or self._sessions.get(entry[1]) != (entry[0], True):
            raise IdentityDenied("fixture refresh denied")
        return self._tokens(*entry)

    def revoke(self, sid: UUID) -> None:
        old = self._sessions.get(sid)
        if old is not None:
            self._sessions.pop(sid)
            self._refresh = {k: v for k, v in self._refresh.items() if v[1] != sid}

    def rotate_key(self) -> None:
        self._key = ec.generate_private_key(ec.SECP256R1())
        self.kid = uuid4().hex


class IdentityVerifier:
    def __init__(self, store: ControlStore, issuer: FixtureIssuer) -> None:
        self.store, self.issuer = store, issuer

    async def provider_call(self, operation: str, *args: object) -> Any:
        lease = await self.store._call("api_auth_claim")
        if lease is None:
            raise IdentityUnavailable("identity capacity unavailable")
        try:
            async with asyncio.timeout(5):
                if operation == "keys":
                    return await self.issuer.keys()
                if operation == "exchange":
                    return await self.issuer.exchange(cast(str, args[0]), cast(str, args[1]))
                if operation == "refresh":
                    return await self.issuer.refresh(cast(str, args[0]))
                if operation == "session":
                    return await self.issuer.valid_session(cast(VerifiedIdentity, args[0]))
                raise IdentityDenied("identity operation denied")
        except TimeoutError:
            raise IdentityUnavailable("identity deadline exceeded") from None
        finally:
            await self.store._call("api_auth_release", UUID(str(lease)))

    @staticmethod
    def validate_keys(body: bytes) -> dict[str, Any]:
        data = strict_json(body, 65536)
        keys = data.get("keys")
        if set(data) != {"keys"} or not isinstance(keys, list) or not 1 <= len(keys) <= 4:
            raise IdentityDenied("invalid key set")
        seen = set()
        for key in keys:
            if (
                not isinstance(key, dict)
                or set(key) != {"kty", "crv", "x", "y", "kid", "alg", "use"}
                or key.get("kty") != "EC"
                or key.get("crv") != "P-256"
                or key.get("alg") != "ES256"
                or key.get("use") != "sig"
                or not isinstance(key.get("kid"), str)
                or not re.fullmatch(r"[A-Za-z0-9._-]{1,64}", key["kid"])
                or key["kid"] in seen
            ):
                raise IdentityDenied("invalid public key")
            if not all(
                isinstance(key.get(k), str) and TOKEN_PATTERN.fullmatch(key[k]) for k in ("x", "y")
            ):
                raise IdentityDenied("invalid public key")
            jwt.PyJWK.from_dict(key, algorithm="ES256")
            seen.add(key["kid"])
        return data

    async def keys(self, kid: str) -> dict[str, Any]:
        cached = await self.store._call("api_keys")
        if cached is not None and cached["fresh"] and cached["payload"] is not None:
            data = self.validate_keys(json.dumps(cached["payload"]).encode())
            if any(k["kid"] == kid for k in data["keys"]):
                return data
        attempt = await self.store._call("api_keys_claim")
        if attempt is None:
            if cached is not None and cached["fresh"]:
                raise IdentityDenied("unapproved signing key")
            raise IdentityUnavailable("identity keys unavailable")
        body = cast(bytes, await self.provider_call("keys"))
        try:
            data = self.validate_keys(body)
        except Exception:
            raise IdentityDenied("invalid identity keys") from None
        if not await self.store._call("api_keys_save", UUID(str(attempt)), Jsonb(data)):
            raise IdentityUnavailable("identity keys unavailable")
        return data

    async def verify(self, token: str, nonce: str) -> VerifiedIdentity:
        try:
            if not isinstance(token, str) or len(token.encode()) > 8192:
                raise ValueError
            parts = token.split(".")
            if len(parts) != 3 or any(not re.fullmatch(r"[A-Za-z0-9_-]+", part) for part in parts):
                raise ValueError
            header = strict_json(
                base64.urlsafe_b64decode(parts[0] + "=" * (-len(parts[0]) % 4)), 2048
            )
            raw = strict_json(base64.urlsafe_b64decode(parts[1] + "=" * (-len(parts[1]) % 4)), 8192)
            if (
                set(header) != {"alg", "typ", "kid"}
                or header["alg"] != "ES256"
                or header["typ"] != "JWT"
                or not isinstance(header["kid"], str)
                or not re.fullmatch(r"[A-Za-z0-9._-]{1,64}", header["kid"])
            ):
                raise ValueError
            required = {"iss", "aud", "sub", "sid", "nonce", "token_kind", "iat", "nbf", "exp"}
            if (
                set(raw) != required
                or any(type(raw[k]) is not int for k in ("iat", "nbf", "exp"))
                or raw["exp"] <= raw["iat"]
                or raw["exp"] - raw["iat"] > 300
                or raw["token_kind"] != "human-access"
                or not isinstance(raw["sub"], str)
                or not 1 <= len(raw["sub"]) <= 256
                or raw["nonce"] != nonce
            ):
                raise ValueError
            sid = UUID(raw["sid"])
            if not sid.int:
                raise ValueError
            keys = await self.keys(header["kid"])
            key = next((k for k in keys["keys"] if k["kid"] == header["kid"]), None)
            if key is None:
                raise ValueError
            jwt.decode(
                token,
                jwt.PyJWK.from_dict(key, algorithm="ES256").key,
                algorithms=["ES256"],
                issuer=ISSUER,
                audience=AUDIENCE,
                options={"require": sorted(required), "strict_aud": True},
            )
            verified = VerifiedIdentity(raw["sub"], sid, nonce, raw["exp"])
            if not await self.provider_call("session", verified):
                raise ValueError
            return verified
        except (IdentityUnavailable, IdentityDenied):
            raise
        except Exception:
            raise IdentityDenied("identity verification failed") from None
