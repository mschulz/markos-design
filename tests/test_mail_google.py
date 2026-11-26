"""The real Google clients against an in-process fake Google. No request leaves the process."""

import asyncio
import base64
import json
from collections.abc import Callable, Coroutine
from datetime import UTC, date, datetime
from typing import Any
from urllib.parse import parse_qs, urlsplit
from uuid import uuid4

import pytest

httpx2 = pytest.importorskip("httpx2")
pytest.importorskip("cryptography")

from markos_runtime.mail.gmail import (  # noqa: E402
    Attachment,
    ConsentDenied,
    HistoryGone,
    MessageGone,
    MessageMeta,
)
from markos_runtime.mail.google import (  # noqa: E402
    MESSAGE_FIELDS,
    SCOPE,
    GoogleConsent,
    GoogleError,
    GoogleGmail,
    GoogleGmailAccess,
    GrantRevoked,
    OAuthClient,
)
from markos_runtime.mail.ingest import Mailbox, SyncResult, sync  # noqa: E402
from markos_runtime.mail.sealing import TokenKey, seal  # noqa: E402
from markos_runtime.mail.worker import WorkClaim, run_once  # noqa: E402
from tests.gmail_support import PDF, FakeGmail, MemoryRecords, MemoryWork  # noqa: E402

BILLS = "Label_bills"
TOPIC = "projects/markos-test/topics/gmail"
REDIRECT = "https://markos.example.invalid/api/v1/mail/callback"
OAUTH = OAuthClient("client-id.apps.googleusercontent.test", "client-secret-value")
NESTED = Attachment("1.2", "ANGjdJ_nested", "application/pdf", 4096)


def run[T](work: Coroutine[Any, Any, T]) -> T:
    return asyncio.run(work)


class FakeGoogle:
    """Answers Google's OAuth and Gmail REST calls from a FakeGmail. Records every request."""

    def __init__(self, gmail: FakeGmail) -> None:
        self.gmail = gmail
        self.requests: list[Any] = []
        self.codes: dict[str, str] = {}
        self.refresh: set[str] = set()
        self.access: set[str] = set()
        self.revoked: list[str] = []
        self.scope = SCOPE
        self.fail: dict[str, int] = {}
        self.expire_access_once = False
        self.oversize = False
        self.watch_bodies: list[dict[str, Any]] = []

    def transport(self) -> Any:
        return httpx2.MockTransport(self.handle)

    def http(self) -> Any:
        return httpx2.AsyncClient(transport=self.transport())

    def issue(self, verifier: str) -> str:
        code = f"4/code-{len(self.codes)}"
        self.codes[code] = verifier
        return code

    def grant(self) -> str:
        token = f"1//refresh-{len(self.refresh)}"
        self.refresh.add(token)
        return token

    async def handle(self, request: Any) -> Any:
        self.requests.append(request)
        url = urlsplit(str(request.url))
        query = {k: v[0] for k, v in parse_qs(url.query).items()}
        name = url.path.rsplit("/", 1)[-1]
        if name in self.fail:
            return httpx2.Response(self.fail[name], json={"error": "failed"})
        if url.netloc == "oauth2.googleapis.com":
            form = {k: v[0] for k, v in parse_qs(request.content.decode()).items()}
            return self.token(form) if url.path == "/token" else self.revoke(form)
        assert url.netloc == "gmail.googleapis.com"
        bearer = request.headers.get("authorization", "")
        if self.expire_access_once:
            self.expire_access_once = False
            return httpx2.Response(401, json={"error": "expired"})
        if bearer.removeprefix("Bearer ") not in self.access:
            return httpx2.Response(401, json={"error": "unauthenticated"})
        return await self.gmail_call(request.method, url.path, query, request)

    def token(self, form: dict[str, str]) -> Any:
        assert form["client_id"] == OAUTH.client_id
        assert form["client_secret"] == OAUTH.client_secret
        if form["grant_type"] == "authorization_code":
            if self.codes.pop(form["code"], None) != form["code_verifier"]:
                return httpx2.Response(400, json={"error": "invalid_grant"})
            assert form["redirect_uri"] == REDIRECT
            access = f"ya29.access-{len(self.access)}"
            self.access.add(access)
            return httpx2.Response(
                200,
                json={"refresh_token": self.grant(), "access_token": access, "scope": self.scope},
            )
        if form["refresh_token"] not in self.refresh:
            return httpx2.Response(400, json={"error": "invalid_grant"})
        access = f"ya29.access-{len(self.access)}"
        self.access.add(access)
        return httpx2.Response(200, json={"access_token": access, "expires_in": 3599})

    def revoke(self, form: dict[str, str]) -> Any:
        if form["token"] not in self.refresh:
            return httpx2.Response(400, json={"error": "invalid_token"})
        self.refresh.discard(form["token"])
        self.revoked.append(form["token"])
        return httpx2.Response(200)

    async def gmail_call(self, method: str, path: str, query: dict[str, str], request: Any) -> Any:
        gmail = self.gmail
        parts = path.removeprefix("/gmail/v1/users/me/").split("/")
        if parts == ["profile"]:
            return httpx2.Response(
                200,
                json={"historyId": str(gmail.history_id), "emailAddress": "owner@example.invalid"},
            )
        if parts == ["history"]:
            try:
                page = await gmail.history(int(query["startHistoryId"]), query.get("pageToken"))
            except HistoryGone:
                return httpx2.Response(404, json={"error": "notFound"})
            body: dict[str, Any] = {
                "historyId": str(page.history_id),
                "history": [
                    {"id": "1", "messagesAdded": [{"message": {"id": c.message_id}}]}
                    if not c.label_ids
                    else {"id": "1", "labelsAdded": [{"message": _wire_change(c)}]}
                    for c in page.changes
                ],
            }
            if page.next_page:
                body["nextPageToken"] = page.next_page
            return httpx2.Response(200, json=body)
        if parts == ["messages"]:
            assert query["fields"] == "messages/id,nextPageToken"
            after = datetime.fromtimestamp(int(query["q"].removeprefix("after:")), UTC).date()
            listing = await gmail.list_messages(query["labelIds"], after, query.get("pageToken"))
            body = {"messages": [{"id": m} for m in listing.message_ids]}
            if listing.next_page:
                body["nextPageToken"] = listing.next_page
            return httpx2.Response(200, json=body)
        if len(parts) == 2 and parts[0] == "messages":
            assert query == {"format": "full", "fields": MESSAGE_FIELDS}
            try:
                meta = await gmail.message(parts[1])
            except MessageGone:
                return httpx2.Response(404, json={"error": "notFound"})
            return httpx2.Response(200, json=_wire_message(meta))
        if len(parts) == 4 and parts[2] == "attachments":
            assert query == {"fields": "data"}
            try:
                data = await gmail.attachment(parts[1], parts[3])
            except MessageGone:
                return httpx2.Response(404, json={"error": "notFound"})
            if self.oversize:
                data = b"%PDF" + b"x" * (3 * 1024 * 1024)
            wire = base64.urlsafe_b64encode(data).rstrip(b"=").decode()
            return httpx2.Response(200, json={"data": wire})
        if parts == ["watch"] and method == "POST":
            self.watch_bodies.append(json.loads(request.content))
            watch = await gmail.watch(self.watch_bodies[-1]["labelIds"])
            ms = int(watch.expires_at.timestamp() * 1000)
            return httpx2.Response(
                200, json={"historyId": str(watch.history_id), "expiration": str(ms)}
            )
        if parts == ["stop"] and method == "POST":
            await gmail.stop()
            return httpx2.Response(204)
        return httpx2.Response(404, json={"error": "unknown route"})


def _wire_change(change: Any) -> dict[str, Any]:
    return {"id": change.message_id, "labelIds": list(change.label_ids)}


def _wire_message(meta: MessageMeta) -> dict[str, Any]:
    """A multipart message as Gmail sends it under MESSAGE_FIELDS: no body data, no file names."""

    def part(a: Attachment) -> dict[str, Any]:
        return {
            "partId": a.part_id,
            "mimeType": a.mime_type,
            "body": {"attachmentId": a.attachment_id, "size": a.size},
        }

    top = [a for a in meta.attachments if "." not in a.part_id]
    nested = [a for a in meta.attachments if "." in a.part_id]
    children: list[dict[str, Any]] = [
        {"partId": "0", "mimeType": "text/plain", "body": {"size": 12}},
        *(part(a) for a in top),
    ]
    if nested:
        children.append(
            {
                "partId": "1",
                "mimeType": "multipart/mixed",
                "body": {"size": 0},
                "parts": [part(a) for a in nested],
            }
        )
    return {
        "id": meta.message_id,
        "threadId": meta.thread_id,
        "historyId": str(meta.history_id),
        "internalDate": str(int(meta.received_at.timestamp() * 1000)),
        "labelIds": list(meta.label_ids),
        "payload": {"partId": "", "mimeType": "multipart/mixed", "parts": children},
    }


def worker_access(google: FakeGoogle) -> tuple[GoogleGmailAccess, Any]:
    key = TokenKey(bytes(range(32)))
    sealed = seal(key.public, uuid4(), "household", google.grant())
    return GoogleGmailAccess(OAUTH, key, TOPIC, google.http()), sealed


def with_client[T](google: FakeGoogle, body: Callable[[GoogleGmail], Coroutine[Any, Any, T]]) -> T:
    access, sealed = worker_access(google)
    return run(body(access.client(sealed)))


def test_authorization_url_asks_only_for_gmail_read_with_pkce() -> None:
    consent = GoogleConsent(OAUTH, None)
    url = urlsplit(consent.authorization_url("state-1", "challenge-1", REDIRECT))
    params = {k: v for k, v in parse_qs(url.query).items()}

    assert url.scheme == "https" and url.netloc == "accounts.google.com"
    assert params == {
        "client_id": [OAUTH.client_id],
        "redirect_uri": [REDIRECT],
        "response_type": ["code"],
        "scope": [SCOPE],
        "access_type": ["offline"],
        "prompt": ["consent"],
        "state": ["state-1"],
        "code_challenge": ["challenge-1"],
        "code_challenge_method": ["S256"],
    }
    assert OAUTH.client_secret not in repr(OAUTH)


def test_exchange_returns_the_refresh_token_and_address() -> None:
    google = FakeGoogle(FakeGmail())
    consent = GoogleConsent(OAUTH, google.http())
    code = google.issue("verifier-1")

    grant = run(consent.exchange(code, "verifier-1", REDIRECT))

    assert grant.refresh_token in google.refresh
    assert grant.address == "owner@example.invalid"
    assert grant.refresh_token not in repr(grant)
    # A replayed code or a wrong verifier is refused.
    with pytest.raises(ConsentDenied):
        run(consent.exchange(code, "verifier-1", REDIRECT))
    with pytest.raises(ConsentDenied):
        run(consent.exchange(google.issue("verifier-2"), "verifier-x", REDIRECT))


def test_exchange_without_gmail_scope_revokes_the_grant() -> None:
    google = FakeGoogle(FakeGmail())
    google.scope = "openid"
    consent = GoogleConsent(OAUTH, google.http())

    with pytest.raises(ConsentDenied):
        run(consent.exchange(google.issue("v"), "v", REDIRECT))

    assert len(google.revoked) == 1 and not google.refresh


def test_revoke_succeeds_when_already_revoked_and_fails_on_server_error() -> None:
    google = FakeGoogle(FakeGmail())
    consent = GoogleConsent(OAUTH, google.http())
    token = google.grant()

    run(consent.revoke(token))
    run(consent.revoke(token))
    assert google.revoked == [token]

    google.fail["revoke"] = 503
    with pytest.raises(GoogleError) as error:
        run(consent.revoke(google.grant()))
    assert "1//" not in str(error.value)


def test_worker_sync_through_the_real_client() -> None:
    gmail = FakeGmail()
    google = FakeGoogle(gmail)
    bills = [gmail.deliver([BILLS, "INBOX"], attachments=[PDF, NESTED]) for _ in range(3)]
    private = gmail.deliver(["INBOX"])
    gmail.deliver([BILLS], received=date(2019, 6, 1))
    records = MemoryRecords(Mailbox((BILLS,), None))

    async def first(client: GoogleGmail) -> SyncResult:
        return await sync(client, records)

    assert with_client(google, first) == SyncResult("first-import", 3)
    assert set(records.stored) == set(bills)
    assert records.stored[bills[0]].attachments == (PDF, NESTED)
    assert records.stored[bills[0]].received_at == gmail.messages[bills[0]].received_at
    assert gmail.fetched[private] == 0

    later = gmail.deliver([BILLS])
    assert with_client(google, first) == SyncResult("incremental", 1)
    gmail.forget_history()
    missed = gmail.deliver([BILLS])
    gmail.forget_history()
    assert with_client(google, first) == SyncResult("history-lost", 1)
    assert set(records.stored) == {*bills, later, missed}


def test_message_reads_never_ask_for_body_data_or_file_names() -> None:
    assert "data" not in MESSAGE_FIELDS.replace("metadata", "")
    assert "filename" not in MESSAGE_FIELDS and "headers" not in MESSAGE_FIELDS


def test_attachment_download_is_decoded_and_bounded() -> None:
    gmail = FakeGmail()
    google = FakeGoogle(gmail)
    message = gmail.deliver([BILLS], attachments=[PDF], bodies=[b"%PDF-1.7 synthetic"])

    async def fetch(client: GoogleGmail) -> bytes:
        return await client.attachment(message, PDF.attachment_id)

    assert with_client(google, fetch) == b"%PDF-1.7 synthetic"
    google.oversize = True
    with pytest.raises(GoogleError, match="too large"):
        with_client(google, fetch)
    gmail.delete(message)
    google.oversize = False
    with pytest.raises(MessageGone):
        with_client(google, fetch)


def test_watch_names_the_topic_and_only_the_allowlisted_labels() -> None:
    gmail = FakeGmail()
    google = FakeGoogle(gmail)

    async def renew(client: GoogleGmail) -> Any:
        return await client.watch([BILLS])

    watch = with_client(google, renew)

    assert google.watch_bodies == [
        {"topicName": TOPIC, "labelIds": [BILLS], "labelFilterBehavior": "INCLUDE"}
    ]
    assert watch.history_id == gmail.history_id
    assert watch.expires_at > datetime.now(UTC)
    with pytest.raises(ValueError):
        GoogleGmailAccess(OAUTH, TokenKey(bytes(32)), "gmail-topic", None)


def test_expired_access_token_is_renewed_once() -> None:
    gmail = FakeGmail()
    google = FakeGoogle(gmail)

    async def twice(client: GoogleGmail) -> int:
        first = await client.current_history_id()
        google.expire_access_once = True
        return first + await client.current_history_id()

    assert with_client(google, twice) == 2 * gmail.history_id
    refreshes = [r for r in google.requests if r.url.path == "/token"]
    assert len(refreshes) == 2


def test_revoked_grant_stops_reads_but_disconnect_still_finishes() -> None:
    gmail = FakeGmail()
    google = FakeGoogle(gmail)
    access, sealed = worker_access(google)
    google.refresh.clear()

    with pytest.raises(GrantRevoked) as error:
        run(access.client(sealed).current_history_id())
    assert "1//" not in str(error.value)

    work = MemoryWork(Mailbox((BILLS,), 1000))
    work.due = WorkClaim(1, False, False, 0, (BILLS,), disconnect=True, token=sealed)
    assert run(run_once(access, work)).disconnected
    assert gmail.stopped == 0 and google.revoked == []


def test_disconnect_stops_the_watch_and_revokes_the_token() -> None:
    gmail = FakeGmail()
    google = FakeGoogle(gmail)
    access, sealed = worker_access(google)
    work = MemoryWork(Mailbox((BILLS,), 1000))
    work.due = WorkClaim(1, False, False, 0, (BILLS,), disconnect=True, token=sealed)

    assert run(run_once(access, work)).disconnected

    assert gmail.stopped == 1 and len(google.revoked) == 1 and not google.refresh


def test_google_failures_name_no_secret() -> None:
    gmail = FakeGmail()
    google = FakeGoogle(gmail)
    google.fail["profile"] = 500

    async def read(client: GoogleGmail) -> int:
        return await client.current_history_id()

    with pytest.raises(GoogleError) as error:
        with_client(google, read)
    text = str(error.value)
    assert text == "profile: HTTP 500"
    assert all(secret not in text for secret in ("1//", "ya29.", OAUTH.client_secret))
