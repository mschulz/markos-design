"""Migration 0019 on synthetic PostgreSQL: one record per message, allowlist, cursor, scope."""

import asyncio
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any

import psycopg
import pytest

from markos_runtime.mail.gmail import MessageMeta
from markos_runtime.mail.ingest import SyncResult, sync
from markos_runtime.mail.store import MailStore
from markos_runtime.store import StorageError
from tests.gmail_support import PDF, FakeGmail
from tests.mail_optin_support import BILLS, ELSE, connect_mailbox


def grant_mail(database: dict[str, str], *roles: str) -> None:
    with psycopg.connect(database["migration"], autocommit=True) as connection:
        for role in roles:
            connection.execute(
                "UPDATE markos_runtime.service_identities SET can_ingest_mail=true "
                "WHERE login_role=%s",
                (role,),
            )


def rows(database: dict[str, str], query: str) -> list[tuple[Any, ...]]:
    with psycopg.connect(database["admin"]) as connection:
        return connection.execute(query).fetchall()


def with_store(dsn: str, action: Callable[[MailStore], Awaitable[Any]]) -> Any:
    async def scenario() -> Any:
        async with MailStore.connect(dsn) as store:
            return await action(store)

    return asyncio.run(scenario())


def meta(message_id: str, labels: tuple[str, ...], history_id: int = 1001) -> MessageMeta:
    when = datetime(2024, 3, 1, 9, tzinfo=UTC)
    return MessageMeta(message_id, message_id, history_id, when, labels, (PDF,))


def test_sync_stores_each_message_once_with_cursor_and_resync_notices(
    database: dict[str, str],
) -> None:
    grant_mail(database, "m0b_pub")
    gmail = FakeGmail()
    first = [gmail.deliver([BILLS, "INBOX"], attachments=[PDF]) for _ in range(3)]
    private = gmail.deliver(["INBOX"])
    connect_mailbox(database)

    async def scenario(store: MailStore) -> list[SyncResult]:
        results = [await sync(gmail, store)]
        message = gmail.deliver([BILLS])
        gmail.relabel(message, [BILLS, "STARRED"])
        results.append(await sync(gmail, store))
        results.append(await sync(gmail, store))
        gmail.deliver([BILLS])
        gmail.forget_history()
        results.append(await sync(gmail, store))
        return results

    assert with_store(database["publisher"], scenario) == [
        SyncResult("first-import", 3),
        SyncResult("incremental", 1),
        SyncResult("incremental", 0),
        SyncResult("history-lost", 1),
    ]
    stored = rows(database, "SELECT message_id,attachments FROM markos_runtime.mail_messages")
    assert len(stored) == 5 and private not in {r[0] for r in stored}
    assert {r[0] for r in stored} >= set(first)
    assert dict(stored)[first[0]] == [
        {
            "part_id": "1",
            "attachment_id": PDF.attachment_id,
            "mime_type": "application/pdf",
            "size": 2048,
        }
    ]
    assert rows(database, "SELECT history_id FROM markos_runtime.mailboxes") == [
        (gmail.history_id,)
    ]
    assert rows(database, "SELECT reason FROM markos_runtime.mail_resyncs ORDER BY created_at") == [
        ("first-import",),
        ("history-lost",),
    ]


def test_database_refuses_messages_outside_the_allowlist(database: dict[str, str]) -> None:
    grant_mail(database, "m0b_pub")
    connect_mailbox(database)

    async def scenario(store: MailStore) -> None:
        with pytest.raises(StorageError):
            await store.store([meta("a1", (BILLS,)), meta("a2", ("INBOX",))])
        with pytest.raises(StorageError):
            await store.advance(None, 1001, [meta("a3", ("INBOX",))], "first-import")

    with_store(database["publisher"], scenario)
    assert rows(database, "SELECT count(*) FROM markos_runtime.mail_messages") == [(0,)]
    assert rows(database, "SELECT history_id FROM markos_runtime.mailboxes") == [(None,)]


def test_cursor_is_compare_and_set_and_never_moves_backward(database: dict[str, str]) -> None:
    grant_mail(database, "m0b_pub")
    connect_mailbox(database)

    async def scenario(store: MailStore) -> None:
        with pytest.raises(StorageError):
            await store.advance(None, 1001, [], None)
        assert await store.advance(None, 1001, [meta("b1", (BILLS,))], "first-import") == 1
        assert await store.advance(None, 1005, [meta("b2", (BILLS,))], "first-import") is None
        assert await store.advance(1000, 1005, [], None) is None
        with pytest.raises(StorageError):
            await store.advance(1001, 1000, [], None)
        assert await store.advance(1001, 1001, [meta("b1", (BILLS,))], None) == 0
        assert await store.advance(1001, 1009, [meta("b3", (BILLS,))], None) == 1

    with_store(database["publisher"], scenario)
    assert rows(database, "SELECT message_id FROM markos_runtime.mail_messages ORDER BY 1") == [
        ("b1",),
        ("b3",),
    ]
    assert rows(database, "SELECT history_id FROM markos_runtime.mailboxes") == [(1009,)]


def test_mail_functions_need_the_mail_grant_and_stay_in_scope(database: dict[str, str]) -> None:
    grant_mail(database, "m0b_pub", "m0b_otherpub")
    connect_mailbox(database)

    async def own(store: MailStore) -> None:
        await store.advance(None, 1001, [meta("c1", (BILLS,))], "first-import")

    async def unopened(store: MailStore) -> None:
        assert await store.mailbox() is None

    async def other(store: MailStore) -> None:
        assert await store.advance(None, 2001, [meta("c1", ("INBOX",))], "first-import") == 1

    async def refused(store: MailStore) -> None:
        with pytest.raises(StorageError):
            await store.mailbox()

    with_store(database["publisher"], own)
    with_store(database["otherpub"], unopened)
    connect_mailbox(database, ["INBOX"], "other@example.invalid", ELSE)
    with_store(database["otherpub"], other)
    with_store(database["consumer"], refused)
    assert rows(
        database, "SELECT owner_id::text,label_ids FROM markos_runtime.mail_messages ORDER BY 1"
    ) == [
        ("11111111-1111-4111-8111-111111111111", [BILLS]),
        ("99999999-9999-4999-8999-999999999999", ["INBOX"]),
    ]
    with (
        psycopg.connect(database["publisher"]) as connection,
        pytest.raises(psycopg.errors.InsufficientPrivilege),
    ):
        connection.execute("SELECT count(*) FROM markos_runtime.mail_messages")
