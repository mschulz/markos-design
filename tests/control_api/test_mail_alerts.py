"""Gmail push alert route: Google-style token checks with a local test key, then one record."""

import asyncio
import base64
import http.client
import json
import ssl
import time
from pathlib import Path
from typing import Any

import jwt
import psycopg
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from markos_runtime.api.mail_alerts import ALERT_PATH, MailAlerts, PushIdentity, push_history
from markos_runtime.mail.store import MailStore
from tests.control_api.test_conversation import conversation
from tests.integration.test_mail_store import ADDRESS, BILLS, grant_mail, rows
from tests.web_support import web_server

__all__ = ["conversation"]

AUDIENCE = "https://markos.example.invalid/api/v1/mail/alerts"
ACCOUNT = "push@example-project.iam.gserviceaccount.invalid"
KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
OTHER_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)


class TestKeys:
    """Stands in for Google's published keys. Never calls Google."""

    async def key(self, kid: str) -> Any:
        if kid != "test-kid":
            raise jwt.PyJWKClientError("unknown kid")
        return KEY.public_key()


def token(**changes: Any) -> str:
    now = int(time.time())
    claims: dict[str, Any] = {
        "iss": "https://accounts.google.com",
        "aud": AUDIENCE,
        "email": ACCOUNT,
        "email_verified": True,
        "iat": now,
        "exp": now + 3600,
        "sub": "1234567890",
    }
    key, kid = changes.pop("_key", KEY), changes.pop("_kid", "test-kid")
    claims.update(changes)
    claims = {k: v for k, v in claims.items() if v is not None}
    return jwt.encode(claims, key, algorithm="RS256", headers={"kid": kid})


def push(history: object, *, urlsafe: bool = False) -> bytes:
    alert = json.dumps({"emailAddress": "a@example.invalid", "historyId": history}).encode()
    encode = base64.urlsafe_b64encode if urlsafe else base64.b64encode
    return json.dumps(
        {
            "message": {"data": encode(alert).decode(), "messageId": "1", "publishTime": "x"},
            "subscription": "projects/example/subscriptions/gmail",
        }
    ).encode()


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        (push(1234), 1234),
        (push("1234", urlsafe=True), 1234),
        (push(0), None),
        (b"{}", None),
        (b'{"message": {"data": "not base64!"}}', None),
        (b'{"message": {"data": "' + b"QUFB" * 1024 + b'"}}', None),
        (b"\xff", None),
        (b'{"message": {}, "message": {}}', None),
    ],
)
def test_push_history_reads_only_a_valid_alert(body: bytes, expected: int | None) -> None:
    assert push_history(body) == expected


def post(server: dict[str, Any], body: bytes, authorization: str | None) -> int:
    context = ssl.create_default_context(cafile=str(server["cert"]))
    connection = http.client.HTTPSConnection("127.0.0.1", server["port"], context=context)
    headers = {"Content-Type": "application/json"}
    if authorization is not None:
        headers["Authorization"] = authorization
    try:
        connection.request("POST", ALERT_PATH, body=body, headers=headers)
        return connection.getresponse().status
    finally:
        connection.close()


def alerts(data: dict[str, Any]) -> list[tuple[Any, ...]]:
    return rows(
        data, "SELECT alert_seq, poll_due_at<=clock_timestamp() FROM markos_runtime.mailboxes"
    )


def serve(
    data: dict[str, Any], tmp_path: Path, scenario: Any, *, opened: bool = True
) -> MailAlerts:
    grant_mail(data, "m0b_pub")

    async def run() -> MailAlerts:
        async with MailStore.connect(data["publisher"]) as store:
            if opened:
                await store.open_mailbox(ADDRESS, [BILLS])
                # Push the first poll out, so only an alert can make it due.
                with_admin(
                    data, "UPDATE markos_runtime.mailboxes SET poll_due_at=now()+interval '1 hour'"
                )
            adapter = MailAlerts(PushIdentity(AUDIENCE, ACCOUNT), TestKeys(), store)
            async with web_server(data, tmp_path, mail_alerts=adapter) as server:
                await asyncio.to_thread(scenario, server)
            return adapter

    return asyncio.run(run())


def with_admin(data: dict[str, Any], statement: str) -> None:
    with psycopg.connect(data["admin"], autocommit=True) as connection:
        connection.execute(statement)


def test_signed_alert_makes_the_poll_due_once(conversation: dict[str, Any], tmp_path: Path) -> None:
    def scenario(server: dict[str, Any]) -> None:
        assert alerts(conversation) == [(0, False)]
        assert post(server, push(5000), "Bearer " + token()) == 204
        assert alerts(conversation) == [(1, True)]
        # A repeated alert is accepted again; the database decides it changes nothing new.
        assert post(server, push(5000), "Bearer " + token(iss="accounts.google.com")) == 204

    adapter = serve(conversation, tmp_path, scenario)
    assert (adapter.accepted, adapter.dropped) == (2, 0)


DENIED = {
    "missing": None,
    "not bearer": "Basic " + "x" * 20,
    "unsigned": "Bearer " + token().rsplit(".", 1)[0] + ".",
    "wrong key": "Bearer " + token(_key=OTHER_KEY),
    "unknown kid": "Bearer " + token(_kid="other-kid"),
    "expired": "Bearer " + token(exp=int(time.time()) - 600, iat=int(time.time()) - 4000),
    "wrong audience": "Bearer " + token(aud="https://other.example.invalid/alerts"),
    "wrong issuer": "Bearer " + token(iss="https://issuer.example.invalid"),
    "wrong account": "Bearer " + token(email="other@example.invalid"),
    "unverified account": "Bearer " + token(email_verified=False),
    "no account": "Bearer " + token(email=None),
    "too long": "Bearer " + "x" * 5000,
}


def test_alert_route_refuses_bad_tokens_and_records_nothing(
    conversation: dict[str, Any], tmp_path: Path
) -> None:
    def scenario(server: dict[str, Any]) -> None:
        for name, authorization in DENIED.items():
            assert post(server, push(5000), authorization) == 401, name
        assert post(server, b'{"pad": "' + b"x" * 70000 + b'"}', "Bearer " + token()) == 413
        assert alerts(conversation) == [(0, False)]

    adapter = serve(conversation, tmp_path, scenario)
    assert (adapter.accepted, adapter.dropped) == (0, 0)


def test_malformed_alert_and_disconnected_mailbox_are_dropped(
    conversation: dict[str, Any], tmp_path: Path
) -> None:
    def scenario(server: dict[str, Any]) -> None:
        assert post(server, b'{"message": {"data": "@@"}}', "Bearer " + token()) == 204
        assert post(server, push(5000), "Bearer " + token()) == 204

    adapter = serve(conversation, tmp_path, scenario, opened=False)
    assert (adapter.accepted, adapter.dropped) == (0, 2)
    assert alerts(conversation) == []


def test_alert_route_is_off_without_the_adapter(
    conversation: dict[str, Any], tmp_path: Path
) -> None:
    async def run() -> int:
        async with web_server(conversation, tmp_path) as server:
            return await asyncio.to_thread(post, server, push(5000), "Bearer " + token())

    assert asyncio.run(run()) == 404
