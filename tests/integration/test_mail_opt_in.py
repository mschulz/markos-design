"""Migration 0021 on synthetic PostgreSQL: owner opt-in, sealed token storage and disconnect."""

import asyncio
from typing import Any

import psycopg
import pytest

from markos_runtime.control.store import ControlConflict, ControlDenied, ControlNotFound
from markos_runtime.mail.optin import MailboxView, address_hash
from markos_runtime.mail.store import MailStore
from markos_runtime.mail.worker import PassResult, run_once
from markos_runtime.store import StorageError
from tests.gmail_support import FakeGmail
from tests.integration.test_mail_store import grant_mail, rows
from tests.mail_optin_support import (
    ADDRESS,
    BILLS,
    ELSE,
    KEY_ID,
    OWN,
    as_owner,
    connect_mailbox,
    disconnect_mailbox,
    token,
)

CONNECTED = MailboxView("connected", (BILLS,), None)


def admin(database: dict[str, str], statement: str) -> None:
    """Moves database due times to stand in for elapsed time. Fixed test statements only."""
    with psycopg.connect(database["admin"], autocommit=True) as connection:
        connection.execute(statement)


def worker(database: dict[str, str], gmail: FakeGmail) -> PassResult:
    async def run() -> PassResult:
        async with MailStore.connect(database["publisher"]) as store:
            return await run_once(gmail, store)

    return asyncio.run(run())


def changes(database: dict[str, str]) -> list[tuple[Any, ...]]:
    return rows(
        database,
        "SELECT h.action,h.labels,a.operation FROM markos_control.mail_history h "
        "JOIN markos_control.audit a ON a.actor_id=h.actor_id AND a.operation='mail.'||h.action "
        "AND a.owner_id=h.owner_id ORDER BY h.recorded_at",
    )


def test_administrator_connects_and_the_worker_receives_the_sealed_token(
    database: dict[str, str],
) -> None:
    grant_mail(database, "m0b_pub")
    assert as_owner(database, lambda mail, ctx: mail.read(ctx, OWN, "personal")) == MailboxView(
        "none", None, None
    )
    assert connect_mailbox(database) == CONNECTED
    assert rows(
        database,
        "SELECT b.address_hash,t.key_id,t.sealed FROM markos_runtime.mailboxes b "
        "JOIN markos_runtime.mail_tokens t USING(owner_id,domain)",
    ) == [(address_hash(" Owner@Example.invalid"), KEY_ID, token().sealed)]
    gmail = FakeGmail()
    gmail.deliver([BILLS])

    assert worker(database, gmail).renewed
    assert gmail.tokens == [token()] and gmail.watched == [(BILLS,)]
    view = as_owner(database, lambda mail, ctx: mail.read(ctx, OWN, "personal"), "mail-viewer")
    assert view.state == "connected" and view.watch_expires_at is not None
    assert changes(database) == [("connect", [BILLS], "mail.connect")]


def test_only_an_administrator_changes_the_mailbox(database: dict[str, str]) -> None:
    grant_mail(database, "m0b_pub")
    connect_mailbox(database)
    attempts = [
        lambda mail, ctx: mail.connect(ctx, OWN, "personal", ADDRESS, [BILLS], token()),
        lambda mail, ctx: mail.set_labels(ctx, OWN, "personal", ["INBOX"]),
        lambda mail, ctx: mail.disconnect(ctx, OWN, "personal"),
    ]
    for attempt in attempts:
        with pytest.raises(ControlDenied):
            as_owner(database, attempt, "mail-viewer")
    with pytest.raises(ControlDenied):
        as_owner(database, lambda mail, ctx: mail.read(ctx, OWN, "work"))
    with pytest.raises(ControlConflict):
        connect_mailbox(database, address="someone@example.invalid")
    assert changes(database) == [("connect", [BILLS], "mail.connect")]
    assert rows(database, "SELECT count(*) FROM markos_runtime.mail_tokens") == [(1,)]


def test_new_labels_are_audited_and_need_a_new_watch(database: dict[str, str]) -> None:
    grant_mail(database, "m0b_pub")
    connect_mailbox(database)
    gmail = FakeGmail()
    worker(database, gmail)
    labels = ["Label_bills", "Label_tax"]

    view = as_owner(database, lambda mail, ctx: mail.set_labels(ctx, OWN, "personal", labels))

    assert view.labels == tuple(labels)
    assert worker(database, gmail).renewed and gmail.watched[-1] == tuple(labels)
    with pytest.raises(StorageError):
        as_owner(database, lambda mail, ctx: mail.set_labels(ctx, OWN, "personal", ["bad label"]))
    assert changes(database)[-1] == ("labels", labels, "mail.labels")


def test_disconnect_stops_work_then_the_worker_revokes_and_deletes_the_token(
    database: dict[str, str],
) -> None:
    grant_mail(database, "m0b_pub")
    connect_mailbox(database)
    gmail = FakeGmail()
    worker(database, gmail)

    assert disconnect_mailbox(database) == MailboxView("disconnecting", None, None)
    assert disconnect_mailbox(database).state == "disconnecting"

    async def refused(store: MailStore) -> None:
        with pytest.raises(StorageError):
            await store.note_alert(gmail.history_id + 1)

    async def run() -> None:
        async with MailStore.connect(database["publisher"]) as store:
            await refused(store)

    asyncio.run(run())
    with pytest.raises(ControlConflict):
        connect_mailbox(database)
    assert worker(database, gmail) == PassResult(disconnected=True)
    assert (gmail.stopped, gmail.revoked) == (1, 1)
    assert rows(database, "SELECT count(*) FROM markos_runtime.mail_tokens") == [(0,)]
    assert disconnect_mailbox(database).state == "disconnected"
    assert worker(database, gmail) == PassResult()
    assert [c[0] for c in changes(database)] == ["connect", "disconnect"]

    assert connect_mailbox(database) == CONNECTED
    assert worker(database, gmail).renewed


def test_unreachable_google_is_retried_then_the_token_is_deleted_after_a_day(
    database: dict[str, str],
) -> None:
    grant_mail(database, "m0b_pub")
    connect_mailbox(database)
    disconnect_mailbox(database)
    gmail = FakeGmail(unreachable=True)

    with pytest.raises(OSError):
        worker(database, gmail)
    assert worker(database, gmail) == PassResult()
    admin(database, "UPDATE markos_runtime.mailboxes SET lease_until=clock_timestamp()")
    with pytest.raises(OSError):
        worker(database, gmail)
    assert rows(database, "SELECT count(*) FROM markos_runtime.mail_tokens") == [(1,)]

    admin(
        database,
        "UPDATE markos_runtime.mailboxes "
        "SET lease_until=NULL,disconnect_deadline=clock_timestamp()",
    )
    assert worker(database, gmail) == PassResult()
    assert rows(database, "SELECT count(*) FROM markos_runtime.mail_tokens") == [(0,)]
    assert disconnect_mailbox(database).state == "disconnected"


def test_finish_under_an_old_fence_records_nothing(database: dict[str, str]) -> None:
    grant_mail(database, "m0b_pub")
    connect_mailbox(database)
    disconnect_mailbox(database)

    async def scenario(store: MailStore) -> tuple[bool, bool]:
        claim = await store.claim_work(30)
        assert claim is not None and claim.disconnect and claim.token == token()
        admin(database, "UPDATE markos_runtime.mailboxes SET fence=fence+1")
        return await store.finish_disconnect(claim), await store.finish_work(claim, True, None)

    async def run() -> tuple[bool, bool]:
        async with MailStore.connect(database["publisher"]) as store:
            return await scenario(store)

    assert asyncio.run(run()) == (False, False)
    assert rows(database, "SELECT count(*) FROM markos_runtime.mail_tokens") == [(1,)]


def test_tokens_and_history_are_reachable_only_through_functions(database: dict[str, str]) -> None:
    grant_mail(database, "m0b_pub")
    connect_mailbox(database)
    connect_mailbox(database, ["INBOX"], "other@example.invalid", ELSE)
    for login, table in (
        ("publisher", "markos_runtime.mail_tokens"),
        ("gateway", "markos_runtime.mail_tokens"),
        ("gateway", "markos_runtime.mailboxes"),
        ("gateway", "markos_control.mail_history"),
    ):
        with (
            psycopg.connect(database[login]) as connection,
            pytest.raises(psycopg.errors.InsufficientPrivilege),
        ):
            connection.execute(f"SELECT count(*) FROM {table}")
    with (
        psycopg.connect(database["publisher"]) as connection,
        pytest.raises(psycopg.errors.UndefinedFunction),
    ):
        connection.execute("SELECT markos_runtime.open_mailbox(%s,%s)", ("a" * 64, [BILLS]))
    disconnect_mailbox(database)
    with pytest.raises(ControlNotFound):
        as_owner(database, lambda mail, ctx: mail.set_labels(ctx, OWN, "personal", [BILLS]))
