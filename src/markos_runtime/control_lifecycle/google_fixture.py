"""Synthetic Google for the fixture process: OAuth, the Gmail API and Pub/Sub push signing keys.

It serves Google's paths under one loopback origin, so the real clients in `mail/google.py` run
unchanged against it. All state is in memory and every mailbox, code and token is invented.
A separate admin token lets the process test deliver mail and get a signed push token.
"""

import base64
import hashlib
import hmac
import json
import secrets
import time
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import parse_qs, urlencode, urlsplit

from markos_runtime.api.identity import strict_json

SCOPE = "https://www.googleapis.com/auth/gmail.readonly"
ADDRESS = "owner@example.invalid"
GMAIL = "/gmail/v1/users/me"
MAX_FORM_BYTES = 8192
MAX_CONTROL_BYTES = 512 * 1024
MAX_MESSAGES = 200
PAGE_SIZE = 100


@dataclass
class FixtureMessage:
    message_id: str
    history_id: int
    received_ms: int
    labels: list[str]
    pdf: bytes | None


@dataclass
class FixtureMailbox:
    history_id: int = 1000
    oldest: int = 1000
    messages: dict[str, FixtureMessage] = field(default_factory=dict)
    changes: list[tuple[int, str]] = field(default_factory=list)
    codes: dict[str, tuple[str, str]] = field(default_factory=dict)
    refresh: set[str] = field(default_factory=set)
    access: set[str] = field(default_factory=set)
    watched: list[list[str]] = field(default_factory=list)
    stopped: int = 0
    revoked: int = 0

    def deliver(self, labels: list[str], pdf: bytes | None) -> str:
        if len(self.messages) >= MAX_MESSAGES:
            raise ValueError("fixture mailbox full")
        self.history_id += 1
        message_id = f"{self.history_id:016x}"
        self.messages[message_id] = FixtureMessage(
            message_id, self.history_id, int(time.time() * 1000), labels, pdf
        )
        self.changes.append((self.history_id, message_id))
        return message_id


def _wire(message: FixtureMessage) -> dict[str, Any]:
    """A message as Gmail sends it under MESSAGE_FIELDS: no body data, no file names."""
    parts: list[dict[str, Any]] = [{"partId": "0", "mimeType": "text/plain", "body": {"size": 12}}]
    if message.pdf is not None:
        parts.append(
            {
                "partId": "1",
                "mimeType": "application/pdf",
                "body": {"attachmentId": "ANGjdJ_fixture", "size": len(message.pdf)},
            }
        )
    return {
        "id": message.message_id,
        "threadId": message.message_id,
        "historyId": str(message.history_id),
        "internalDate": str(message.received_ms),
        "labelIds": message.labels,
        "payload": {"partId": "", "mimeType": "multipart/mixed", "parts": parts},
    }


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def google_app(
    origin: str,
    client_id: str,
    client_secret: str,
    redirect_uri: str,
    audience: str,
    account: str,
    admin_token: str,
) -> Any:
    import jwt  # type: ignore[import-not-found]
    from cryptography.hazmat.primitives.asymmetric.rsa import (  # type: ignore[import-not-found,unused-ignore]
        generate_private_key,
    )
    from fastapi import FastAPI, Request  # type: ignore[import-not-found]
    from fastapi.responses import (  # type: ignore[import-not-found]
        JSONResponse,
        RedirectResponse,
        Response,
    )

    box = FixtureMailbox()
    signing = generate_private_key(public_exponent=65537, key_size=2048)
    kid = secrets.token_hex(8)
    jwk = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(signing.public_key()))
    jwk.update(kid=kid, alg="RS256", use="sig")
    host = urlsplit(origin).netloc
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)

    def error(status: int, reason: str) -> Any:
        return JSONResponse({"error": reason}, status_code=status)

    async def body(request: Request, limit: int) -> bytes:
        data = bytearray()
        async for chunk in request.stream():
            data.extend(chunk)
            if len(data) > limit:
                raise ValueError("request too large")
        return bytes(data)

    async def form(request: Request) -> dict[str, str]:
        pairs = parse_qs((await body(request, MAX_FORM_BYTES)).decode(), strict_parsing=True)
        if any(len(v) != 1 for v in pairs.values()):
            raise ValueError("repeated form field")
        return {k: v[0] for k, v in pairs.items()}

    def client(values: dict[str, str]) -> bool:
        return hmac.compare_digest(values.get("client_id", ""), client_id) and hmac.compare_digest(
            values.get("client_secret", ""), client_secret
        )

    def issue_access() -> str:
        token = "ya29.fixture-" + secrets.token_urlsafe(24)
        box.access.add(token)
        return token

    async def guard(request: Request, call_next: Any) -> Any:
        if request.url.scheme != "https" or request.headers.get("host") != host:
            return error(421, "misdirected")
        try:
            return await call_next(request)
        except ValueError:
            return error(400, "invalid_request")

    async def authorize(request: Request) -> Any:
        params = dict(request.query_params)
        if (
            params.get("client_id") != client_id
            or params.get("redirect_uri") != redirect_uri
            or params.get("response_type") != "code"
            or params.get("scope") != SCOPE
            or params.get("code_challenge_method") != "S256"
            or not params.get("code_challenge")
            or not params.get("state")
        ):
            return error(400, "invalid_request")
        # The fixture owner always approves. A real owner sees Google's consent screen here.
        code = "4/fixture-" + secrets.token_urlsafe(24)
        box.codes[code] = (params["code_challenge"], redirect_uri)
        query = urlencode({"state": params["state"], "code": code, "scope": SCOPE})
        return RedirectResponse(redirect_uri + "?" + query, status_code=303)

    async def token(request: Request) -> Any:
        values = await form(request)
        if not client(values):
            return error(401, "invalid_client")
        if values.get("grant_type") == "authorization_code":
            expected = box.codes.pop(values.get("code", ""), None)
            verifier = values.get("code_verifier", "").encode()
            got = _b64url(hashlib.sha256(verifier).digest())
            if (
                expected is None
                or not hmac.compare_digest(got, expected[0])
                or values.get("redirect_uri") != expected[1]
            ):
                return error(400, "invalid_grant")
            refresh = "1//fixture-" + secrets.token_urlsafe(24)
            box.refresh.add(refresh)
            return JSONResponse(
                {"refresh_token": refresh, "access_token": issue_access(), "scope": SCOPE}
            )
        if values.get("grant_type") == "refresh_token":
            if values.get("refresh_token") not in box.refresh:
                return error(400, "invalid_grant")
            return JSONResponse({"access_token": issue_access(), "expires_in": 3599})
        return error(400, "unsupported_grant_type")

    async def revoke(request: Request) -> Any:
        values = await form(request)
        if values.get("token") not in box.refresh:
            return error(400, "invalid_token")
        box.refresh.discard(values["token"])
        box.revoked += 1
        return Response(status_code=200)

    async def certs() -> Any:
        return JSONResponse({"keys": [jwk]})

    def bearer(request: Request) -> bool:
        value = request.headers.get("authorization", "").removeprefix("Bearer ")
        return value in box.access

    async def gmail(request: Request, rest: str) -> Any:
        if not bearer(request):
            return error(401, "unauthenticated")
        query = dict(request.query_params)
        parts, method = rest.split("/"), request.method
        if parts == ["profile"] and method == "GET":
            return JSONResponse({"historyId": str(box.history_id), "emailAddress": ADDRESS})
        if parts == ["history"] and method == "GET":
            start = int(query["startHistoryId"])
            if start < box.oldest:
                return error(404, "notFound")
            found = [m for hid, m in box.changes if hid > start]
            offset = int(query.get("pageToken", "0"))
            chunk = found[offset : offset + PAGE_SIZE]
            page: dict[str, Any] = {
                "historyId": str(box.history_id),
                "history": [
                    {
                        "id": m,
                        "messagesAdded": [
                            {"message": {"id": m, "labelIds": box.messages[m].labels}}
                        ],
                    }
                    for m in chunk
                    if m in box.messages
                ],
            }
            if offset + PAGE_SIZE < len(found):
                page["nextPageToken"] = str(offset + PAGE_SIZE)
            return JSONResponse(page)
        if parts == ["messages"] and method == "GET":
            after = int(query["q"].removeprefix("after:")) * 1000
            label = query["labelIds"]
            ids = [
                m.message_id
                for m in sorted(box.messages.values(), key=lambda m: -m.received_ms)
                if label in m.labels and m.received_ms > after
            ]
            return JSONResponse({"messages": [{"id": m} for m in ids[:PAGE_SIZE]]})
        if len(parts) == 2 and parts[0] == "messages" and method == "GET":
            message = box.messages.get(parts[1])
            return JSONResponse(_wire(message)) if message else error(404, "notFound")
        if len(parts) == 4 and parts[0] == "messages" and parts[2] == "attachments":
            message = box.messages.get(parts[1])
            if message is None or message.pdf is None or parts[3] != "ANGjdJ_fixture":
                return error(404, "notFound")
            return JSONResponse({"data": _b64url(message.pdf)})
        if parts == ["watch"] and method == "POST":
            request_body = json.loads(await body(request, MAX_FORM_BYTES))
            if request_body.get("labelFilterBehavior") != "INCLUDE":
                return error(400, "invalid_request")
            box.watched.append(list(request_body["labelIds"]))
            expires = int((time.time() + 7 * 86400) * 1000)
            return JSONResponse({"historyId": str(box.history_id), "expiration": str(expires)})
        if parts == ["stop"] and method == "POST":
            box.stopped += 1
            return Response(status_code=204)
        return error(404, "notFound")

    async def control(request: Request) -> Any:
        auth = request.headers.get("authorization", "")
        if not hmac.compare_digest(auth.encode(), ("Bearer " + admin_token).encode()):
            return error(401, "denied")
        value = strict_json(await body(request, MAX_CONTROL_BYTES), MAX_CONTROL_BYTES)
        operation = value.get("operation")
        if operation == "deliver" and set(value) <= {"operation", "labels", "pdf"}:
            labels = value.get("labels")
            pdf = value.get("pdf")
            if not isinstance(labels, list) or not all(isinstance(x, str) for x in labels):
                raise ValueError("labels required")
            data = base64.b64decode(pdf, validate=True) if isinstance(pdf, str) else None
            return JSONResponse({"message_id": box.deliver(labels, data)})
        if operation == "push" and set(value) == {"operation"}:
            now = int(time.time())
            claims = {
                "iss": "https://accounts.google.com",
                "aud": audience,
                "email": account,
                "email_verified": True,
                "iat": now,
                "exp": now + 3600,
            }
            signed = jwt.encode(claims, signing, algorithm="RS256", headers={"kid": kid})
            alert = json.dumps({"emailAddress": ADDRESS, "historyId": box.history_id})
            envelope = {
                "message": {"data": base64.b64encode(alert.encode()).decode(), "messageId": "1"},
                "subscription": "projects/markos-fixture/subscriptions/gmail-push",
            }
            return JSONResponse({"token": signed, "body": envelope})
        if operation == "state" and set(value) == {"operation"}:
            return JSONResponse(
                {
                    "grants": len(box.refresh),
                    "revoked": box.revoked,
                    "watched": box.watched,
                    "stopped": box.stopped,
                    "messages": len(box.messages),
                }
            )
        if operation == "health" and set(value) == {"operation"}:
            return JSONResponse({"ready": True})
        return error(400, "invalid_request")

    app.middleware("http")(guard)
    app.add_api_route("/o/oauth2/v2/auth", authorize, methods=["GET"])
    app.add_api_route("/token", token, methods=["POST"])
    app.add_api_route("/revoke", revoke, methods=["POST"])
    app.add_api_route("/oauth2/v3/certs", certs, methods=["GET"])
    app.add_api_route(GMAIL + "/{rest:path}", gmail, methods=["GET", "POST"])
    app.add_api_route("/fixture-control", control, methods=["POST"])
    return app
