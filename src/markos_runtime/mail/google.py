"""The real Google clients: OAuth consent and the Gmail API, over HTTPS with httpx2.

Nothing here runs until M2-E3 wires it from config and the owner passes the M2-E gate. Tests use
an in-process fake Google and never call Google.

Errors never hold a token, a code, a mail body or a response body: only the operation and status.
Message reads use a partial-response selector, so Gmail never sends body data or file names.
"""

import base64
import binascii
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from typing import Any
from urllib.parse import quote, urlencode, urlsplit

import httpx2  # type: ignore[import-not-found,unused-ignore]

from markos_runtime.knowledge.pdf import MAX_PDF_BYTES
from markos_runtime.mail.gmail import (
    Attachment,
    Change,
    ConsentDenied,
    Grant,
    HistoryGone,
    HistoryPage,
    ListPage,
    MessageGone,
    MessageMeta,
    SealedToken,
    Watch,
)
from markos_runtime.mail.sealing import TokenKey

AUTHORIZE_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
REVOKE_URL = "https://oauth2.googleapis.com/revoke"
GMAIL_URL = "https://gmail.googleapis.com/gmail/v1/users/me"
SCOPE = "https://www.googleapis.com/auth/gmail.readonly"

MAX_JSON_BYTES = 1024 * 1024
# Base64url of the largest PDF the import accepts, plus room for the JSON around it.
MAX_ATTACHMENT_RESPONSE = MAX_PDF_BYTES * 4 // 3 + 4096
PAGE_SIZE = 100

_PART = "partId,mimeType,body(attachmentId,size)"
_PARTS = _PART
for _ in range(4):
    _PARTS = f"{_PART},parts({_PARTS})"
# Five levels of MIME parts. Attachments nested deeper than that are not seen.
MESSAGE_FIELDS = f"id,threadId,historyId,internalDate,labelIds,payload({_PARTS})"


class GoogleError(Exception):
    """Google refused or did not answer. The message names only the operation and status."""


class GrantRevoked(GoogleError):
    """The refresh token no longer works: revoked, expired or deleted at Google."""


@dataclass(frozen=True)
class OAuthClient:
    client_id: str
    client_secret: str = field(repr=False)


@dataclass(frozen=True)
class GoogleEndpoints:
    """Where the clients send requests. Only the synthetic fixture process changes these."""

    authorize: str = AUTHORIZE_URL
    token: str = TOKEN_URL
    revoke: str = REVOKE_URL
    gmail: str = GMAIL_URL

    @classmethod
    def at(cls, origin: str) -> "GoogleEndpoints":
        """The same paths under one origin, as the fixture Google serves them."""
        return cls(
            *(
                origin + urlsplit(url).path
                for url in (AUTHORIZE_URL, TOKEN_URL, REVOKE_URL, GMAIL_URL)
            )
        )


GOOGLE = GoogleEndpoints()


def google_http(verify: Any = True) -> Any:
    """The HTTPS client for Google: short timeouts, no redirects, few connections."""
    return httpx2.AsyncClient(
        timeout=httpx2.Timeout(15.0, connect=5.0),
        limits=httpx2.Limits(max_connections=4),
        follow_redirects=False,
        verify=verify,
    )


async def _call(
    http: Any,
    operation: str,
    method: str,
    url: str,
    *,
    limit: int = MAX_JSON_BYTES,
    **kwargs: Any,
) -> tuple[int, dict[str, Any]]:
    """One bounded request. Returns the status and the JSON object, or {} for no JSON."""
    try:
        async with http.stream(method, url, **kwargs) as response:
            status = int(response.status_code)
            body = bytearray()
            async for chunk in response.aiter_bytes():
                body += chunk
                if len(body) > limit:
                    raise GoogleError(f"{operation}: response too large")
    except httpx2.HTTPError:
        raise GoogleError(f"{operation}: unreachable") from None
    try:
        value = json.loads(body) if body else {}
    except (ValueError, UnicodeDecodeError):
        value = None
    if not isinstance(value, dict):
        if 200 <= status < 300:
            raise GoogleError(f"{operation}: malformed response")
        value = {}
    return status, value


def _int(value: Any, operation: str) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, str)):
        raise GoogleError(f"{operation}: malformed response")
    try:
        return int(value)
    except ValueError:
        raise GoogleError(f"{operation}: malformed response") from None


def _str(value: Any, operation: str) -> str:
    if not isinstance(value, str) or not value:
        raise GoogleError(f"{operation}: malformed response")
    return value


def _labels(value: Any) -> tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    return tuple(v for v in value if isinstance(v, str))


def _path(value: str) -> str:
    return quote(value, safe="")


class GoogleConsent:
    """Implements GmailConsent from `api/mail_connect.py` against Google's OAuth endpoints."""

    def __init__(self, oauth: OAuthClient, http: Any, endpoints: GoogleEndpoints = GOOGLE) -> None:
        self._oauth = oauth
        self._http = http
        self.endpoints = endpoints

    def authorization_url(self, state: str, code_challenge: str, redirect_uri: str) -> str:
        params = {
            "client_id": self._oauth.client_id,
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "scope": SCOPE,
            "access_type": "offline",
            "prompt": "consent",
            "state": state,
            "code_challenge": code_challenge,
            "code_challenge_method": "S256",
        }
        return self.endpoints.authorize + "?" + urlencode(params)

    async def exchange(self, code: str, verifier: str, redirect_uri: str) -> Grant:
        status, body = await _call(
            self._http,
            "token exchange",
            "POST",
            self.endpoints.token,
            data={
                "grant_type": "authorization_code",
                "code": code,
                "code_verifier": verifier,
                "redirect_uri": redirect_uri,
                "client_id": self._oauth.client_id,
                "client_secret": self._oauth.client_secret,
            },
        )
        if 400 <= status < 500:
            raise ConsentDenied("Google refused the code")
        if status != 200:
            raise GoogleError(f"token exchange: HTTP {status}")
        refresh = body.get("refresh_token")
        access = body.get("access_token")
        scopes = str(body.get("scope", "")).split()
        if not isinstance(refresh, str) or not refresh:
            raise ConsentDenied("Google sent no refresh token")
        if SCOPE not in scopes or not isinstance(access, str) or not access:
            # The owner unticked Gmail access on the consent screen. Drop what was granted.
            await self.revoke(refresh)
            raise ConsentDenied("Gmail read access was not granted")
        status, profile = await _call(
            self._http,
            "profile",
            "GET",
            self.endpoints.gmail + "/profile",
            params={"fields": "emailAddress"},
            headers={"Authorization": f"Bearer {access}"},
        )
        if status != 200:
            await self.revoke(refresh)
            raise GoogleError(f"profile: HTTP {status}")
        return Grant(refresh, _str(profile.get("emailAddress"), "profile"))

    async def revoke(self, refresh_token: str) -> None:
        """Succeeds when Google revokes the token or already holds it as invalid."""
        status, _ = await _call(
            self._http, "revoke", "POST", self.endpoints.revoke, data={"token": refresh_token}
        )
        if status != 200 and status != 400:
            raise GoogleError(f"revoke: HTTP {status}")


class GoogleGmailAccess:
    """Implements GmailAccess: opens the sealed token with the worker's key for each claim."""

    def __init__(
        self,
        oauth: OAuthClient,
        key: TokenKey,
        topic: str,
        http: Any,
        endpoints: GoogleEndpoints = GOOGLE,
    ) -> None:
        if not topic.startswith("projects/") or "/topics/" not in topic:
            raise ValueError("topic must be projects/<project>/topics/<topic>")
        self._consent = GoogleConsent(oauth, http, endpoints)
        self._oauth = oauth
        self._key = key
        self._topic = topic
        self._http = http

    def client(self, token: SealedToken | None) -> "GoogleGmail":
        refresh = None if token is None else self._key.open(token)
        return GoogleGmail(self._oauth, self._consent, self._topic, self._http, refresh)


class GoogleGmail:
    """Implements GmailClient for one mailbox. Gets a short-lived access token when needed."""

    def __init__(
        self,
        oauth: OAuthClient,
        consent: GoogleConsent,
        topic: str,
        http: Any,
        refresh_token: str | None,
    ) -> None:
        self._oauth = oauth
        self._consent = consent
        self._topic = topic
        self._http = http
        self._refresh = refresh_token
        self._access: str | None = None

    def __repr__(self) -> str:
        return "GoogleGmail()"

    async def _token(self) -> str:
        if self._access is not None:
            return self._access
        if self._refresh is None:
            raise GrantRevoked("no stored token")
        status, body = await _call(
            self._http,
            "token refresh",
            "POST",
            self._consent.endpoints.token,
            data={
                "grant_type": "refresh_token",
                "refresh_token": self._refresh,
                "client_id": self._oauth.client_id,
                "client_secret": self._oauth.client_secret,
            },
        )
        if status == 400 and body.get("error") == "invalid_grant":
            raise GrantRevoked("token refresh: invalid_grant")
        if status != 200:
            raise GoogleError(f"token refresh: HTTP {status}")
        self._access = _str(body.get("access_token"), "token refresh")
        return self._access

    async def _gmail(
        self,
        operation: str,
        method: str,
        path: str,
        *,
        params: Mapping[str, Any] | None = None,
        payload: Mapping[str, Any] | None = None,
        limit: int = MAX_JSON_BYTES,
        missing: type[Exception] | None = None,
    ) -> dict[str, Any]:
        for attempt in (1, 2):
            token = await self._token()
            status, body = await _call(
                self._http,
                operation,
                method,
                self._consent.endpoints.gmail + path,
                limit=limit,
                params=params,
                json=payload,
                headers={"Authorization": f"Bearer {token}"},
            )
            if status == 401 and attempt == 1:
                # The access token expired early. Get a new one once.
                self._access = None
                continue
            if status == 404 and missing is not None:
                raise missing
            if not 200 <= status < 300:
                raise GoogleError(f"{operation}: HTTP {status}")
            return body
        raise GoogleError(f"{operation}: HTTP 401")

    async def current_history_id(self) -> int:
        body = await self._gmail("profile", "GET", "/profile", params={"fields": "historyId"})
        return _int(body.get("historyId"), "profile")

    async def history(self, start: int, page: str | None) -> HistoryPage:
        params: dict[str, Any] = {"startHistoryId": str(start), "maxResults": PAGE_SIZE}
        if page is not None:
            params["pageToken"] = page
        body = await self._gmail("history", "GET", "/history", params=params, missing=HistoryGone)
        changes: list[Change] = []
        records = body.get("history", [])
        for record in records if isinstance(records, list) else ():
            if not isinstance(record, dict):
                continue
            for kind in ("messagesAdded", "labelsAdded", "labelsRemoved"):
                entries = record.get(kind, [])
                for entry in entries if isinstance(entries, list) else ():
                    message = entry.get("message") if isinstance(entry, dict) else None
                    if isinstance(message, dict) and isinstance(message.get("id"), str):
                        changes.append(Change(message["id"], _labels(message.get("labelIds"))))
        next_page = body.get("nextPageToken")
        return HistoryPage(
            tuple(changes),
            _int(body.get("historyId"), "history"),
            next_page if isinstance(next_page, str) and next_page else None,
        )

    async def list_messages(self, label_id: str, after: date, page: str | None) -> ListPage:
        since = int(datetime.combine(after, datetime.min.time(), UTC).timestamp())
        params: dict[str, Any] = {
            "labelIds": label_id,
            "q": f"after:{since}",
            "maxResults": PAGE_SIZE,
            "fields": "messages/id,nextPageToken",
        }
        if page is not None:
            params["pageToken"] = page
        body = await self._gmail("list", "GET", "/messages", params=params)
        found = body.get("messages", [])
        ids = tuple(
            m["id"]
            for m in (found if isinstance(found, list) else ())
            if isinstance(m, dict) and isinstance(m.get("id"), str)
        )
        next_page = body.get("nextPageToken")
        return ListPage(ids, next_page if isinstance(next_page, str) and next_page else None)

    async def message(self, message_id: str) -> MessageMeta:
        body = await self._gmail(
            "message",
            "GET",
            f"/messages/{_path(message_id)}",
            params={"format": "full", "fields": MESSAGE_FIELDS},
            missing=MessageGone,
        )
        received = datetime.fromtimestamp(_int(body.get("internalDate"), "message") / 1000, UTC)
        return MessageMeta(
            _str(body.get("id"), "message"),
            _str(body.get("threadId"), "message"),
            _int(body.get("historyId"), "message"),
            received,
            _labels(body.get("labelIds")),
            tuple(_attachments(body.get("payload"), 0)),
        )

    async def attachment(self, message_id: str, attachment_id: str) -> bytes:
        body = await self._gmail(
            "attachment",
            "GET",
            f"/messages/{_path(message_id)}/attachments/{_path(attachment_id)}",
            params={"fields": "data"},
            limit=MAX_ATTACHMENT_RESPONSE,
            missing=MessageGone,
        )
        data = _str(body.get("data"), "attachment")
        try:
            return base64.urlsafe_b64decode(data + "=" * (-len(data) % 4))
        except (binascii.Error, ValueError):
            raise GoogleError("attachment: malformed response") from None

    async def watch(self, label_ids: Sequence[str]) -> Watch:
        body = await self._gmail(
            "watch",
            "POST",
            "/watch",
            payload={
                "topicName": self._topic,
                "labelIds": list(label_ids),
                "labelFilterBehavior": "INCLUDE",
            },
        )
        expires = datetime.fromtimestamp(_int(body.get("expiration"), "watch") / 1000, UTC)
        return Watch(_int(body.get("historyId"), "watch"), expires)

    async def stop(self) -> None:
        """A revoked token cannot stop the watch. Gmail ends it within 7 days anyway."""
        try:
            await self._gmail("stop", "POST", "/stop")
        except GrantRevoked:
            return

    async def revoke(self) -> None:
        if self._refresh is not None:
            await self._consent.revoke(self._refresh)


def _attachments(part: Any, depth: int) -> list[Attachment]:
    if not isinstance(part, dict) or depth > 4:
        return []
    found: list[Attachment] = []
    body = part.get("body")
    if isinstance(body, dict) and isinstance(body.get("attachmentId"), str):
        size = body.get("size")
        found.append(
            Attachment(
                str(part.get("partId", "")),
                body["attachmentId"],
                str(part.get("mimeType", "")),
                size if isinstance(size, int) and not isinstance(size, bool) else 0,
            )
        )
    children = part.get("parts", [])
    for child in children if isinstance(children, list) else ():
        found += _attachments(child, depth + 1)
    return found
