"""Gmail alerts pushed by Pub/Sub: check Google's signed token, then record the alert.

The route exists only when the gateway is built with a `MailAlerts` adapter. It never
fetches mail and never reads the alert's email address. The mail worker does the fetch.
"""

import asyncio
import base64
import binascii
from dataclasses import dataclass
from typing import Any, Protocol

import jwt

from markos_runtime.api.application import ApiFault
from markos_runtime.contracts.base import loads_strict
from markos_runtime.mail.store import MailStore
from markos_runtime.mail.worker import MAX_ALERT_BYTES, alert_history
from markos_runtime.store.database import StorageError

ALERT_PATH = "/api/v1/mail/alerts"
# Not yet confirmed against Google's current documentation. M2-E checks these.
GOOGLE_ISSUERS = ("https://accounts.google.com", "accounts.google.com")
GOOGLE_KEYS_URL = "https://www.googleapis.com/oauth2/v3/certs"
MAX_TOKEN_CHARS = 4096
LEEWAY_SECONDS = 30
URLSAFE = str.maketrans("-_", "+/")


class SigningKeys(Protocol):
    async def key(self, kid: str) -> Any: ...


class GoogleKeys:
    """Google's published signing keys. PyJWT caches them and refetches for an unknown kid."""

    def __init__(self, url: str = GOOGLE_KEYS_URL) -> None:
        self._client = jwt.PyJWKClient(url, cache_keys=True, lifespan=3600, timeout=5)

    async def key(self, kid: str) -> Any:
        return (await asyncio.to_thread(self._client.get_signing_key, kid)).key


@dataclass(frozen=True)
class PushIdentity:
    """The push subscription's token audience and service account email."""

    audience: str
    account: str


def push_history(body: bytes) -> int | None:
    """The history ID in a Pub/Sub push body, or None if malformed."""
    try:
        envelope = loads_strict(body, 65536)
    except (ValueError, UnicodeError, RecursionError):
        return None
    message = envelope.get("message") if isinstance(envelope, dict) else None
    data = message.get("data") if isinstance(message, dict) else None
    if not isinstance(data, str) or len(data) > 2 * MAX_ALERT_BYTES:
        return None
    try:
        # Accept standard and URL-safe base64. Which one Google sends is not yet confirmed.
        raw = base64.b64decode(data.translate(URLSAFE), validate=True)
    except (binascii.Error, ValueError):
        return None
    return alert_history(raw)


class MailAlerts:
    def __init__(self, identity: PushIdentity, keys: SigningKeys, store: MailStore) -> None:
        if not identity.audience or not identity.account or not isinstance(store, MailStore):
            raise ValueError("explicit push identity and mail store required")
        self.identity, self.keys, self.store = identity, keys, store
        self.accepted = 0
        self.dropped = 0

    async def verify(self, authorization: str | None) -> None:
        scheme, _, token = (authorization or "").partition(" ")
        if scheme != "Bearer" or not token or len(token) > MAX_TOKEN_CHARS:
            raise ApiFault("unauthenticated", 401)
        try:
            header = jwt.get_unverified_header(token)
            kid = header.get("kid")
            if header.get("alg") != "RS256" or not isinstance(kid, str):
                raise jwt.InvalidTokenError
            claims = jwt.decode(
                token,
                await self.keys.key(kid),
                algorithms=["RS256"],
                audience=self.identity.audience,
                issuer=GOOGLE_ISSUERS,
                leeway=LEEWAY_SECONDS,
                options={"require": ["exp", "iat", "iss", "aud", "email"]},
            )
        except jwt.PyJWKClientConnectionError:
            # Google's keys are out of reach. Pub/Sub retries a 503.
            raise ApiFault("unavailable", 503) from None
        except jwt.PyJWTError:
            raise ApiFault("unauthenticated", 401) from None
        if claims.get("email") != self.identity.account or claims.get("email_verified") is not True:
            raise ApiFault("unauthenticated", 401)

    async def handle(self, request: Any, data: bytes) -> Any:
        from fastapi.responses import Response  # type: ignore[import-not-found]

        if request.method != "POST":
            raise ApiFault("not-found", 404)
        if request.query_params:
            raise ApiFault("invalid", 400)
        await self.verify(request.headers.get("authorization"))
        history = push_history(data)
        if history is None:
            # A 2xx stops Pub/Sub from retrying an alert that can never parse.
            self.dropped += 1
            return Response(status_code=204)
        try:
            await self.store.note_alert(history)
        except StorageError:
            # A disconnected mailbox drops the alert. Any other failure returns 503 for a retry.
            if await self.store.mailbox() is not None:
                raise
            self.dropped += 1
            return Response(status_code=204)
        self.accepted += 1
        return Response(status_code=204)
