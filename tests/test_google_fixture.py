"""The real Google clients against the fixture Google app, in process. No request leaves it."""

import asyncio
import base64
import json
from collections.abc import Coroutine
from datetime import date
from typing import Any
from urllib.parse import parse_qs, urlsplit
from uuid import uuid4

import pytest

httpx2 = pytest.importorskip("httpx2")
jwt = pytest.importorskip("jwt")
pytest.importorskip("fastapi")
pytest.importorskip("cryptography")

from markos_runtime.api.identity import challenge  # noqa: E402
from markos_runtime.api.mail_alerts import GOOGLE_ISSUERS, push_history  # noqa: E402
from markos_runtime.control_lifecycle.google_fixture import google_app  # noqa: E402
from markos_runtime.mail.gmail import ConsentDenied  # noqa: E402
from markos_runtime.mail.google import (  # noqa: E402
    GoogleConsent,
    GoogleEndpoints,
    GoogleGmailAccess,
    GrantRevoked,
    OAuthClient,
)
from markos_runtime.mail.ingest import Mailbox, sync  # noqa: E402
from markos_runtime.mail.sealing import TokenKey, seal  # noqa: E402
from tests.gmail_support import MemoryRecords  # noqa: E402

ORIGIN = "https://127.0.0.1:19445"
REDIRECT = "https://127.0.0.1:18443/api/v1/mail/callback"
AUDIENCE = "https://127.0.0.1:18443/api/v1/mail/alerts"
ACCOUNT = "gmail-push@markos-fixture.iam.gserviceaccount.test"
OAUTH = OAuthClient("markos-fixture.apps.googleusercontent.test", "fixture-client-secret")
ADMIN = "fixture-admin-token"
TOPIC = "projects/markos-fixture/topics/gmail"
LABEL = "Label_bills"


def run[T](work: Coroutine[Any, Any, T]) -> T:
    return asyncio.run(work)


def fixture() -> Any:
    app = google_app(
        ORIGIN, OAUTH.client_id, OAUTH.client_secret, REDIRECT, AUDIENCE, ACCOUNT, ADMIN
    )
    return httpx2.AsyncClient(transport=httpx2.ASGITransport(app=app), base_url=ORIGIN)


async def control(http: Any, operation: str, **args: Any) -> Any:
    response = await http.post(
        "/fixture-control",
        json={"operation": operation, **args},
        headers={"Authorization": "Bearer " + ADMIN},
    )
    assert response.status_code == 200
    return response.json()


async def connect(http: Any, verifier: str = "v" * 43) -> Any:
    """The browser's trip through the fixture consent screen, then the gateway's exchange."""
    consent = GoogleConsent(OAUTH, http, GoogleEndpoints.at(ORIGIN))
    url = consent.authorization_url("state-1", challenge(verifier), REDIRECT)
    assert url.startswith(ORIGIN + "/o/oauth2/v2/auth?")
    response = await http.get(url)
    assert response.status_code == 303
    location = response.headers["location"]
    assert location.startswith(REDIRECT + "?")
    query = {k: v[0] for k, v in parse_qs(urlsplit(location).query).items()}
    assert query["state"] == "state-1"
    return consent, query["code"]


def test_consent_round_trip_checks_pkce_and_revokes() -> None:
    async def scenario() -> None:
        async with fixture() as http:
            consent, code = await connect(http)
            with pytest.raises(ConsentDenied):
                await consent.exchange(code, "w" * 43, REDIRECT)
            consent, code = await connect(http)
            grant = await consent.exchange(code, "v" * 43, REDIRECT)
            assert grant.address == "owner@example.invalid"
            with pytest.raises(ConsentDenied):
                await consent.exchange(code, "v" * 43, REDIRECT)
            assert (await control(http, "state"))["grants"] == 1
            await consent.revoke(grant.refresh_token)
            await consent.revoke(grant.refresh_token)
            state = await control(http, "state")
            assert state["grants"] == 0 and state["revoked"] == 1

    run(scenario())


def test_authorize_refuses_another_client_or_redirect() -> None:
    async def scenario() -> None:
        async with fixture() as http:
            for client, redirect in (
                (OAuthClient("other.apps.googleusercontent.test", "x"), REDIRECT),
                (OAUTH, "https://127.0.0.1:18443/elsewhere"),
            ):
                consent = GoogleConsent(client, http, GoogleEndpoints.at(ORIGIN))
                url = consent.authorization_url("s", challenge("v" * 43), redirect)
                assert (await http.get(url)).status_code == 400

    run(scenario())


def test_worker_sync_watch_and_attachment_through_fixture() -> None:
    async def scenario() -> None:
        async with fixture() as http:
            consent, code = await connect(http)
            grant = await consent.exchange(code, "v" * 43, REDIRECT)
            key = TokenKey(bytes(range(32)))
            sealed = seal(key.public, uuid4(), "personal", grant.refresh_token)
            access = GoogleGmailAccess(OAUTH, key, TOPIC, http, GoogleEndpoints.at(ORIGIN))
            gmail = access.client(sealed)
            watch = await gmail.watch([LABEL])
            records = MemoryRecords(Mailbox((LABEL,), watch.history_id))
            pdf = b"%PDF-1.4 synthetic"
            wire = base64.b64encode(pdf).decode()
            kept = (await control(http, "deliver", labels=[LABEL], pdf=wire))["message_id"]
            await control(http, "deliver", labels=["INBOX"])
            result = await sync(gmail, records)
            assert result.stored >= 1 and set(records.stored) == {kept}
            meta = records.stored[kept]
            assert [a.mime_type for a in meta.attachments] == ["application/pdf"]
            assert await gmail.attachment(kept, meta.attachments[0].attachment_id) == pdf
            listing = await gmail.list_messages(LABEL, date(2020, 1, 1), None)
            assert listing.message_ids == (kept,)
            await gmail.stop()
            await gmail.revoke()
            state = await control(http, "state")
            assert state["watched"] == [[LABEL]] and state["stopped"] == 1
            assert state["revoked"] == 1
            with pytest.raises(GrantRevoked):
                await access.client(sealed).current_history_id()

    run(scenario())


def test_push_token_verifies_against_published_keys() -> None:
    async def scenario() -> None:
        async with fixture() as http:
            await control(http, "deliver", labels=[LABEL])
            push = await control(http, "push")
            keys = (await http.get("/oauth2/v3/certs")).json()["keys"]
            kid = jwt.get_unverified_header(push["token"])["kid"]
            key = jwt.PyJWK(next(k for k in keys if k["kid"] == kid)).key
            claims = jwt.decode(
                push["token"], key, algorithms=["RS256"], audience=AUDIENCE, issuer=GOOGLE_ISSUERS
            )
            assert claims["email"] == ACCOUNT and claims["email_verified"] is True
            assert push_history(json.dumps(push["body"]).encode()) == 1001

    run(scenario())


def test_fixture_refuses_without_tokens_or_on_another_host() -> None:
    async def scenario() -> None:
        async with fixture() as http:
            denied = await http.post("/fixture-control", json={"operation": "state"})
            assert denied.status_code == 401
            assert (await http.get("/gmail/v1/users/me/profile")).status_code == 401
            other = await http.get("https://localhost:19445/oauth2/v3/certs")
            assert other.status_code == 421

    run(scenario())
