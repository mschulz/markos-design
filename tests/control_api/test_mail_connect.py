"""Owner opt-in over HTTPS: fixture Google consent with PKCE, labels and disconnect."""

import asyncio
import os
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlencode, urlsplit
from uuid import UUID

import pytest

pytest.importorskip("cryptography")

from markos_runtime.api.mail_connect import (  # noqa: E402
    CALLBACK_PATH,
    MAIL_FLOW_COOKIE,
    FixtureGmailConsent,
    MailConnect,
)
from markos_runtime.mail.gmail import SealedToken  # noqa: E402
from markos_runtime.mail.sealing import TokenKey  # noqa: E402
from tests.control_api.test_conversation import conversation  # noqa: E402
from tests.integration.conftest import OWNER  # noqa: E402
from tests.integration.test_mail_store import grant_mail, rows  # noqa: E402
from tests.web_support import WebBrowserClient, web_server  # noqa: E402

__all__ = ["conversation"]

SCOPE = "?" + urlencode({"owner_id": OWNER, "domain": "personal"})
BILLS = "Label_bills"
WORKER = TokenKey(os.urandom(32))


def serve(data: dict[str, Any], tmp_path: Path, scenario: Any, **consent: Any) -> Any:
    grant_mail(data, "m0b_pub")
    fixture = FixtureGmailConsent(**consent)

    async def run() -> Any:
        adapter = MailConnect(fixture, WORKER.public)
        async with web_server(data, tmp_path, mail_connect=adapter) as server:
            return await scenario(server, fixture)

    return asyncio.run(run())


async def browser(server: dict[str, Any], who: str = "administrator") -> WebBrowserClient:
    client = WebBrowserClient(server["port"], server["cert"])
    await client.login(server["issuer"], server["credentials"][who])
    return client


async def consent(client: WebBrowserClient, labels: list[str] | None = None) -> str:
    body = {"schema_version": 1, "labels": labels or [BILLS]}
    status, flow = await client.call("POST", "/api/v1/mail/connect" + SCOPE, body)
    assert status == 200, flow
    assert MAIL_FLOW_COOKIE in client.cookies
    return str(flow["authorization_url"])


async def callback(client: WebBrowserClient, **params: str) -> str:
    status, _ = await client.call("GET", CALLBACK_PATH + "?" + urlencode(params))
    assert status == 303
    location = dict(client.last_headers)[b"location"].decode()
    assert location.startswith("/app/mail#") and MAIL_FLOW_COOKIE not in client.cookies
    return location.split("#", 1)[1]


def stored(data: dict[str, Any]) -> list[tuple[Any, ...]]:
    return rows(data, "SELECT owner_id,domain,key_id,sealed FROM markos_runtime.mail_tokens")


def test_administrator_connects_through_consent(
    conversation: dict[str, Any], tmp_path: Path
) -> None:
    async def scenario(server: dict[str, Any], fixture: FixtureGmailConsent) -> None:
        client = await browser(server)
        url = await consent(client)
        params = {k: v[0] for k, v in parse_qs(urlsplit(url).query).items()}
        assert params["code_challenge_method"] == "S256"
        assert params["redirect_uri"] == server["origin"] + CALLBACK_PATH
        assert params["scope"] == "https://www.googleapis.com/auth/gmail.readonly"
        state, code = fixture.approve(url)
        assert await callback(client, state=state, code=code) == "connected"
        status, box = await client.call("GET", "/api/v1/mail" + SCOPE)
        assert status == 200 and box == {
            "state": "connected",
            "labels": [BILLS],
            "watch_expires_at": None,
        }
        # The code is single use: a replay finds no flow cookie.
        assert await callback(client, state=state, code=code) == "expired"

    serve(conversation, tmp_path, scenario)
    [(owner, domain, key, sealed)] = stored(conversation)
    token = SealedToken(UUID(str(owner)), domain, key, bytes(sealed))
    assert WORKER.open(token).startswith("1//fixture-")
    assert b"fixture" not in bytes(sealed)


def test_only_an_administrator_with_a_browser_starts_consent(
    conversation: dict[str, Any], tmp_path: Path
) -> None:
    async def scenario(server: dict[str, Any], fixture: FixtureGmailConsent) -> None:
        viewer = await browser(server, "viewer")
        body = {"schema_version": 1, "labels": [BILLS]}
        assert (await viewer.call("POST", "/api/v1/mail/connect" + SCOPE, body))[0] == 403
        status, box = await viewer.call("GET", "/api/v1/mail" + SCOPE)
        assert status == 200 and box["state"] == "none"
        cli = WebBrowserClient(server["port"], server["cert"])
        await cli.login(server["issuer"], server["credentials"]["administrator"], cli=True)
        assert (await cli.call("POST", "/api/v1/mail/connect" + SCOPE, body))[0] == 403
        admin = await browser(server)
        bad = [[], ["INBOX", "INBOX"], ["has space"], ["x" * 65]]
        for labels in bad:
            status, _ = await admin.call(
                "POST", "/api/v1/mail/connect" + SCOPE, {"schema_version": 1, "labels": labels}
            )
            assert status == 400, labels
        admin.csrf = "wrong"
        assert (await admin.call("POST", "/api/v1/mail/connect" + SCOPE, body))[0] == 403
        assert fixture.issued == []

    serve(conversation, tmp_path, scenario)
    assert stored(conversation) == []


def test_callback_refuses_a_wrong_state_session_or_declined_consent(
    conversation: dict[str, Any], tmp_path: Path
) -> None:
    async def scenario(server: dict[str, Any], fixture: FixtureGmailConsent) -> None:
        client = await browser(server)
        state, code = fixture.approve(await consent(client))
        assert await callback(client, state="x" * 43, code=code) == "expired"
        # The flow cookie belongs to the session that started it.
        url = await consent(client)
        state, code = fixture.approve(url)
        other = await browser(server)
        other.cookies[MAIL_FLOW_COOKIE] = client.cookies[MAIL_FLOW_COOKIE]
        assert await callback(other, state=state, code=code) == "expired"
        await consent(client)
        assert await callback(client, state=state, error="access_denied") == "expired"
        state, _ = fixture.approve(await consent(client))
        assert await callback(client, state=state, error="access_denied") == "declined"
        state, _ = fixture.approve(await consent(client))
        assert await callback(client, state=state, code="forged") == "failed"
        assert fixture.issued == []

    serve(conversation, tmp_path, scenario)
    assert stored(conversation) == []


def test_conflict_revokes_the_new_grant(conversation: dict[str, Any], tmp_path: Path) -> None:
    async def scenario(server: dict[str, Any], fixture: FixtureGmailConsent) -> None:
        client = await browser(server)
        state, code = fixture.approve(await consent(client))
        assert await callback(client, state=state, code=code) == "connected"
        fixture.address = "someone-else@example.invalid"
        state, code = fixture.approve(await consent(client))
        assert await callback(client, state=state, code=code) == "conflict"
        assert fixture.revoked == fixture.issued[1:]

    serve(conversation, tmp_path, scenario)
    assert len(stored(conversation)) == 1


def test_labels_and_disconnect_over_https(conversation: dict[str, Any], tmp_path: Path) -> None:
    async def scenario(server: dict[str, Any], fixture: FixtureGmailConsent) -> None:
        client = await browser(server)
        state, code = fixture.approve(await consent(client))
        assert await callback(client, state=state, code=code) == "connected"
        viewer = await browser(server, "viewer")
        body = {"schema_version": 1, "labels": ["INBOX"]}
        assert (await viewer.call("POST", "/api/v1/mail/labels" + SCOPE, body))[0] == 403
        status, box = await client.call("POST", "/api/v1/mail/labels" + SCOPE, body)
        assert status == 200 and box["labels"] == ["INBOX"]
        assert (
            await client.call(
                "POST", "/api/v1/mail/disconnect" + SCOPE, {"schema_version": 1, "x": 1}
            )
        )[0] == 400
        status, box = await client.call(
            "POST", "/api/v1/mail/disconnect" + SCOPE, {"schema_version": 1}
        )
        assert status == 200 and box["state"] == "disconnecting"

    serve(conversation, tmp_path, scenario)


def test_mail_routes_and_page_are_absent_without_the_adapter(
    conversation: dict[str, Any], tmp_path: Path
) -> None:
    async def run() -> None:
        async with web_server(conversation, tmp_path) as server:
            client = await browser(server)
            assert (await client.call("GET", "/api/v1/mail" + SCOPE))[0] == 404
            status, me = await client.call("GET", "/api/v1/me")
            assert status == 200 and me["mail_available"] is False
            assert (await client.call("GET", "/app/mail"))[0] == 404

    asyncio.run(run())


def test_mail_page_appears_with_the_adapter(conversation: dict[str, Any], tmp_path: Path) -> None:
    async def scenario(server: dict[str, Any], fixture: FixtureGmailConsent) -> None:
        client = await browser(server)
        status, me = await client.call("GET", "/api/v1/me")
        assert status == 200 and me["mail_available"] is True
        status, page = await client.call("GET", "/app/mail")
        assert status == 200 and 'href="/app/mail"' in page

    serve(conversation, tmp_path, scenario)
