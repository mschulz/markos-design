"""Owner opt-in for Gmail from the web app: OAuth consent with PKCE, labels and disconnect.

The routes exist only when the gateway is built with a `MailConnect` adapter. An administrator
starts the consent. The flow state, including the PKCE verifier, travels in a sealed, short-lived
cookie bound to the signed-in session. The callback seals the refresh token for the mail worker
before it reaches the database, so the gateway never stores a token it can open.
"""

import hmac
import json
import re
import secrets
import time
from dataclasses import asdict, dataclass, field
from typing import Any, Protocol, cast
from urllib.parse import parse_qs, urlencode, urlsplit
from uuid import UUID

from pydantic import Field, field_validator

from markos_runtime.api.application import ApiFault
from markos_runtime.api.identity import TOKEN_PATTERN, IdentityDenied, challenge, opaque
from markos_runtime.contracts.base import Domain
from markos_runtime.contracts.control import ControlValue
from markos_runtime.control.store import ControlConflict, ControlDenied
from markos_runtime.mail.optin import MailboxView, MailOptIn
from markos_runtime.mail.sealing import SealError, seal

MAIL_PATH = "/api/v1/mail"
CALLBACK_PATH = "/api/v1/mail/callback"
MAIL_FLOW_COOKIE = "__Host-markos-mail-flow"
FLOW_SECONDS = 600
LABEL = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


class ConsentDenied(Exception):
    """The provider refused the code. The message never holds the code or a token."""


@dataclass(frozen=True)
class Grant:
    refresh_token: str = field(repr=False)
    address: str = field(repr=False)


class GmailConsent(Protocol):
    def authorization_url(self, state: str, code_challenge: str, redirect_uri: str) -> str: ...

    async def exchange(self, code: str, verifier: str, redirect_uri: str) -> Grant: ...

    async def revoke(self, refresh_token: str) -> None: ...


class MailLabels(ControlValue):
    labels: list[str] = Field(min_length=1, max_length=20)

    @field_validator("labels")
    @classmethod
    def allowed(cls, value: list[str]) -> list[str]:
        if len(set(value)) != len(value) or not all(LABEL.fullmatch(label) for label in value):
            raise ValueError("invalid label")
        return value


class MailRequest(ControlValue):
    pass


class MailBillLabel(ControlValue):
    label: str | None

    @field_validator("label")
    @classmethod
    def allowed(cls, value: str | None) -> str | None:
        if value is not None and not LABEL.fullmatch(value):
            raise ValueError("invalid label")
        return value


def present(view: MailboxView) -> dict[str, Any]:
    expires = view.watch_expires_at
    return {
        "state": view.state,
        "labels": None if view.labels is None else list(view.labels),
        "watch_expires_at": None if expires is None else expires.isoformat(),
        "bill_label": view.bill_label,
        "bills": asdict(view.bills),
    }


class MailConnect:
    def __init__(self, consent: GmailConsent, worker_public_key: bytes) -> None:
        if len(worker_public_key) != 32:
            raise ValueError("explicit 32-byte mail worker public key required")
        self.consent, self.public = consent, worker_public_key

    async def _administrator(self, api: Any, ctx: Any, owner: UUID, domain: str) -> None:
        me = await api.store._call("api_me", ctx.session_id, ctx.lookup_hash)
        if not any(
            m["owner_id"] == str(owner) and m["domain"] == domain and "administrator" in m["roles"]
            for m in me["memberships"]
        ):
            raise ApiFault("forbidden", 403)

    async def handle(self, api: Any, request: Any, body: bytes) -> Any:
        from fastapi.responses import JSONResponse  # type: ignore[import-not-found]

        path, method = request.url.path, request.method
        if path == CALLBACK_PATH and method == "GET":
            return await self.callback(api, request)
        params = api.query(request, {"owner_id", "domain"})
        owner, domain = api.scope(params)
        ctx, binding, raw = await api.authenticate(request)
        mail = MailOptIn(api.store)
        if path == MAIL_PATH and method == "GET":
            return JSONResponse(present(await mail.read(ctx, owner, domain)))
        if method != "POST" or path not in {
            MAIL_PATH + "/connect",
            MAIL_PATH + "/labels",
            MAIL_PATH + "/disconnect",
            MAIL_PATH + "/bill-label",
        }:
            raise ApiFault("not-found", 404)
        await api.write(request, ctx, binding, raw)
        if path.endswith("/disconnect"):
            MailRequest.from_wire(body)
            return JSONResponse(present(await mail.disconnect(ctx, owner, domain)))
        if path.endswith("/bill-label"):
            label = MailBillLabel.from_wire(body).label
            return JSONResponse(present(await mail.set_bill_label(ctx, owner, domain, label)))
        labels = MailLabels.from_wire(body).labels
        if path.endswith("/labels"):
            return JSONResponse(present(await mail.set_labels(ctx, owner, domain, labels)))
        if binding["kind"] != "browser":
            raise ApiFault("forbidden", 403)
        await self._administrator(api, ctx, owner, domain)
        return self.begin(api, ctx, owner, domain, labels)

    def begin(self, api: Any, ctx: Any, owner: UUID, domain: str, labels: list[str]) -> Any:
        from fastapi.responses import JSONResponse  # type: ignore[import-not-found]

        state, verifier = opaque(), opaque()
        flow = {
            "state": state,
            "verifier": verifier,
            "owner_id": str(owner),
            "domain": domain,
            "labels": labels,
            "session_id": str(ctx.session_id),
            "expires": int(time.time()) + FLOW_SECONDS,
        }
        url = self.consent.authorization_url(
            state, challenge(verifier), api.config.api_origin + CALLBACK_PATH
        )
        response = JSONResponse({"authorization_url": url})
        response.set_cookie(
            MAIL_FLOW_COOKIE,
            api.codec.seal(json.dumps(flow)),
            secure=True,
            httponly=True,
            samesite="lax",
            path="/",
            max_age=FLOW_SECONDS,
        )
        return response

    async def callback(self, api: Any, request: Any) -> Any:
        """Google's redirect. Every outcome returns to the mail page with a short result."""
        from fastapi.responses import RedirectResponse  # type: ignore[import-not-found]

        result = await self._finish(api, request)
        response = RedirectResponse("/app/mail#" + result, status_code=303)
        response.delete_cookie(
            MAIL_FLOW_COOKIE, secure=True, httponly=True, samesite="lax", path="/"
        )
        return response

    async def _finish(self, api: Any, request: Any) -> str:
        pairs = request.query_params.multi_items()
        params = dict(pairs)
        if len(pairs) != len(params) or set(params) - {"state", "code", "error", "scope"}:
            return "failed"
        ctx, binding, _ = await api.authenticate(request)
        try:
            flow = json.loads(api.codec.open(api.cookies(request).get(MAIL_FLOW_COOKIE, "")))
        except (IdentityDenied, ValueError):
            return "expired"
        if (
            not isinstance(flow, dict)
            or binding["kind"] != "browser"
            or flow.get("session_id") != str(ctx.session_id)
            or not isinstance(flow.get("expires"), int)
            or flow["expires"] <= time.time()
            or not hmac.compare_digest(str(params.get("state", "")), str(flow.get("state", "")))
        ):
            return "expired"
        if "error" in params or "code" not in params:
            return "declined"
        owner, domain = UUID(flow["owner_id"]), cast(Domain, flow["domain"])
        redirect = api.config.api_origin + CALLBACK_PATH
        try:
            grant = await self.consent.exchange(params["code"], flow["verifier"], redirect)
        except ConsentDenied:
            return "failed"
        try:
            token = seal(self.public, owner, domain, grant.refresh_token)
            await MailOptIn(api.store).connect(
                ctx, owner, domain, grant.address, flow["labels"], token
            )
        except (ControlConflict, ControlDenied, SealError) as error:
            await self.consent.revoke(grant.refresh_token)
            return "conflict" if isinstance(error, ControlConflict) else "failed"
        except Exception:
            await self.consent.revoke(grant.refresh_token)
            raise
        return "connected"


class FixtureGmailConsent:
    """In-memory consent for tests. No network, and no real Google account."""

    AUTHORIZE = "https://accounts.google.test/o/oauth2/v2/auth"

    def __init__(self, address: str = "owner@example.invalid") -> None:
        self.address = address
        self._codes: dict[str, tuple[str, str, float]] = {}
        self.issued: list[str] = []
        self.revoked: list[str] = []

    def authorization_url(self, state: str, code_challenge: str, redirect_uri: str) -> str:
        params = {
            "client_id": "markos-fixture.apps.googleusercontent.test",
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "scope": "https://www.googleapis.com/auth/gmail.readonly",
            "access_type": "offline",
            "prompt": "consent",
            "state": state,
            "code_challenge": code_challenge,
            "code_challenge_method": "S256",
        }
        return self.AUTHORIZE + "?" + urlencode(params)

    def approve(self, url: str) -> tuple[str, str]:
        """The owner presses Allow. Returns the state and code Google would send back."""
        params = {k: v[0] for k, v in parse_qs(urlsplit(url).query).items()}
        code = secrets.token_urlsafe(24)
        self._codes[code] = (params["code_challenge"], params["redirect_uri"], time.time() + 60)
        return params["state"], code

    async def exchange(self, code: str, verifier: str, redirect_uri: str) -> Grant:
        entry = self._codes.pop(code, None)
        if (
            entry is None
            or entry[2] <= time.time()
            or entry[1] != redirect_uri
            or not TOKEN_PATTERN.fullmatch(verifier)
            or not hmac.compare_digest(entry[0], challenge(verifier))
        ):
            raise ConsentDenied("fixture exchange denied")
        token = "1//fixture-" + secrets.token_urlsafe(24)
        self.issued.append(token)
        return Grant(token, self.address)

    async def revoke(self, refresh_token: str) -> None:
        self.revoked.append(refresh_token)
