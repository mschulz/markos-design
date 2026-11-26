"""Migration 0020 on synthetic PostgreSQL: alerts, backstop poll, watch renewal and leases."""

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

import psycopg

from markos_runtime.mail.ingest import SyncResult
from markos_runtime.mail.store import MailStore
from markos_runtime.mail.worker import PassResult, alert_history, run_once
from tests.gmail_support import FakeGmail
from tests.integration.test_mail_store import ADDRESS, BILLS, grant_mail, rows


def admin(database: dict[str, str], statement: str) -> None:
    """Moves database due times to stand in for elapsed time. Fixed test statements only."""
    with psycopg.connect(database["admin"], autocommit=True) as connection:
        connection.execute(statement)


def opened(database: dict[str, str], scenario: Callable[[MailStore], Awaitable[Any]]) -> Any:
    grant_mail(database, "m0b_pub")

    async def run() -> Any:
        async with MailStore.connect(database["publisher"]) as store:
            await store.open_mailbox(ADDRESS, [BILLS])
            return await scenario(store)

    return asyncio.run(run())


def due(database: dict[str, str]) -> tuple[Any, ...]:
    return rows(
        database,
        "SELECT poll_due_at>clock_timestamp()+interval '14 minutes',"
        "renew_due_at>clock_timestamp()+interval '23 hours',"
        "watch_expires_at>clock_timestamp()+interval '6 days',lease_until IS NULL "
        "FROM markos_runtime.mailboxes",
    )[0]


async def alert(store: MailStore, data: bytes) -> bool:
    """Records a Gmail alert as the push endpoint will."""
    history = alert_history(data)
    assert history is not None
    return await store.note_alert(history)


def test_first_pass_renews_and_imports_then_waits_for_the_next_due_time(
    database: dict[str, str],
) -> None:
    gmail = FakeGmail()
    gmail.deliver([BILLS])

    async def scenario(store: MailStore) -> list[PassResult]:
        return [await run_once(gmail, store), await run_once(gmail, store)]

    assert opened(database, scenario) == [
        PassResult(True, SyncResult("first-import", 1)),
        PassResult(),
    ]
    assert gmail.watched == [(BILLS,)]
    assert due(database) == (True, True, True, True)


def test_alerts_make_the_poll_due_and_repeats_or_old_alerts_add_nothing(
    database: dict[str, str],
) -> None:
    gmail = FakeGmail()

    async def scenario(store: MailStore) -> list[PassResult]:
        first = await run_once(gmail, store)
        old = gmail.alert()
        message = gmail.deliver([BILLS])
        noted = [await alert(store, data) for data in (gmail.alert(), gmail.alert(), old)]
        second = await run_once(gmail, store)
        noted.append(await alert(store, gmail.alert()))
        assert noted == [True, True, False, False]
        third = await run_once(gmail, store)
        assert message in {
            r[0] for r in rows(database, "SELECT message_id FROM markos_runtime.mail_messages")
        }
        return [first, second, third]

    assert opened(database, scenario) == [
        PassResult(True, SyncResult("first-import", 0)),
        PassResult(False, SyncResult("incremental", 1)),
        PassResult(),
    ]
    assert rows(database, "SELECT alert_seq FROM markos_runtime.mailboxes") == [(2,)]


def test_backstop_poll_finds_mail_whose_alert_was_lost(database: dict[str, str]) -> None:
    gmail = FakeGmail()

    async def scenario(store: MailStore) -> list[PassResult]:
        results = [await run_once(gmail, store)]
        gmail.deliver([BILLS])
        results.append(await run_once(gmail, store))
        admin(database, "UPDATE markos_runtime.mailboxes SET poll_due_at=clock_timestamp()")
        results.append(await run_once(gmail, store))
        return results

    assert opened(database, scenario) == [
        PassResult(True, SyncResult("first-import", 0)),
        PassResult(),
        PassResult(False, SyncResult("incremental", 1)),
    ]


def test_watch_is_renewed_after_downtime_and_before_expiry(database: dict[str, str]) -> None:
    gmail = FakeGmail()

    async def scenario(store: MailStore) -> list[PassResult]:
        results = [await run_once(gmail, store)]
        # The worker was down for three days: the daily renewal time passed long ago.
        admin(
            database,
            "UPDATE markos_runtime.mailboxes SET renew_due_at=clock_timestamp()-interval '3 days',"
            "watch_expires_at=clock_timestamp()+interval '4 days'",
        )
        results.append(await run_once(gmail, store))
        # A renewal time not yet due, but the watch expires within a day.
        admin(
            database,
            "UPDATE markos_runtime.mailboxes SET renew_due_at=clock_timestamp()+interval '1 day',"
            "watch_expires_at=clock_timestamp()+interval '20 hours'",
        )
        results.append(await run_once(gmail, store))
        return results

    assert opened(database, scenario) == [
        PassResult(True, SyncResult("first-import", 0)),
        PassResult(True, None),
        PassResult(True, None),
    ]
    assert len(gmail.watched) == 3
    assert due(database) == (True, True, True, True)


def test_lease_keeps_one_worker_and_a_crashed_worker_is_taken_over(
    database: dict[str, str],
) -> None:
    async def scenario(store: MailStore) -> None:
        crashed = await store.claim_work(60)
        assert crashed is not None and crashed.renew and crashed.sync
        assert await store.claim_work(60) is None
        admin(database, "UPDATE markos_runtime.mailboxes SET lease_until=clock_timestamp()")
        taken = await store.claim_work(60)
        assert taken is not None and taken.fence == crashed.fence + 1
        assert await store.finish_work(crashed, True, None) is False
        assert await store.finish_work(taken, True, None) is True
        assert await store.finish_work(taken, True, None) is False

    opened(database, scenario)


def test_alert_during_a_sync_keeps_the_poll_due(database: dict[str, str]) -> None:
    async def scenario(store: MailStore) -> None:
        claim = await store.claim_work(60)
        assert claim is not None
        assert await store.note_alert(5000) is True
        assert await store.finish_work(claim, True, None) is True

    opened(database, scenario)
    assert rows(
        database, "SELECT poll_due_at<=clock_timestamp() FROM markos_runtime.mailboxes"
    ) == [(True,)]
