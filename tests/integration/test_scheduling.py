import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

import psycopg
import pytest

from markos_runtime.contracts.schedules import ScheduleDefinition
from markos_runtime.execution.scheduler import Scheduler
from markos_runtime.execution.worker import Worker
from markos_runtime.store.database import DurableStore, StorageError
from tests.integration.test_storage import count
from tests.test_scheduling import definition


def schedule(**changes: Any) -> ScheduleDefinition:
    return definition(
        schedule_id=str(uuid4()),
        start_at=(datetime.now(UTC) - timedelta(minutes=5)).isoformat(),
        **changes,
    )


def test_scan_commits_occurrences_events_outboxes_and_cursor_together(
    database: dict[str, str],
) -> None:
    async def scenario() -> None:
        async with DurableStore.connect(database["publisher"]) as store:
            scheduler = Scheduler(store)
            d = schedule()
            await scheduler.register(d)
            assert await scheduler.scan_once() == 2
            assert (
                count(database, "events")
                == count(database, "outbox")
                == count(database, "schedule_occurrences")
                == 2
            )
            assert count(database, "schedule_scans") == 1
            async with DurableStore.connect(database["consumer"]) as worker:
                with psycopg.connect(database["consumer"]) as conn:
                    rows = conn.execute("SELECT wire FROM markos_runtime.events").fetchall()
                from markos_runtime.contracts.events import EventEnvelope

                for row in rows:
                    await worker.handoff(EventEnvelope.from_wire(bytes(row[0])))
                assert await Worker(worker).run_once()

    asyncio.run(scenario())
    assert count(database, "task_results") == 1


def test_concurrent_scanners_and_restart_create_no_duplicate_occurrences(
    database: dict[str, str],
) -> None:
    async def scenario() -> None:
        async with (
            DurableStore.connect(database["publisher"]) as a,
            DurableStore.connect(database["publisher"]) as b,
        ):
            d = schedule(missed_policy="coalesce")
            await Scheduler(a).register(d)
            assert (
                sum(await asyncio.gather(Scheduler(a).scan_once(), Scheduler(b).scan_once())) == 1
            )
        async with DurableStore.connect(database["publisher"]) as restarted:
            assert await Scheduler(restarted).scan_once() == 0

    asyncio.run(scenario())
    assert count(database, "schedule_occurrences") == count(database, "outbox") == 1


@pytest.mark.parametrize(
    "policy,created,skipped", [("catch_up", 2, 0), ("coalesce", 1, 5), ("skip", 0, 6)]
)
def test_missed_policies_are_persisted_and_audited(
    database: dict[str, str], policy: str, created: int, skipped: int
) -> None:
    async def scenario() -> None:
        async with DurableStore.connect(database["publisher"]) as store:
            s = Scheduler(store)
            d = schedule(missed_policy=policy)
            if policy == "skip":
                d = d.model_copy(update={"start_at": d.start_at - timedelta(seconds=30)})
            await s.register(d)
            assert await s.scan_once() == created

    asyncio.run(scenario())
    with psycopg.connect(database["publisher"]) as conn:
        audit = conn.execute("SELECT audit FROM markos_runtime.schedule_scans").fetchone()
        assert audit and audit[0]["policy"] == policy and audit[0]["skipped"] == skipped
        assert audit[0]["pending"] == (policy == "catch_up")


def test_cursor_rollback_on_outbox_failure_prevents_lost_job(database: dict[str, str]) -> None:
    async def scenario() -> None:
        async with DurableStore.connect(database["publisher"]) as store:
            s = Scheduler(store)
            d = schedule()
            await s.register(d)
            with psycopg.connect(database["admin"], autocommit=True) as conn:
                conn.execute(
                    "CREATE FUNCTION markos_runtime.fail_schedule() RETURNS trigger "
                    "LANGUAGE plpgsql AS $$BEGIN RAISE EXCEPTION 'synthetic';END$$"
                )
                conn.execute(
                    "CREATE TRIGGER fail_schedule BEFORE INSERT ON markos_runtime.outbox "
                    "FOR EACH ROW EXECUTE FUNCTION markos_runtime.fail_schedule()"
                )
            with pytest.raises(StorageError):
                await s.scan_once()
            assert (
                count(database, "events")
                == count(database, "schedule_scans")
                == count(database, "schedule_occurrences")
                == 0
            )
            with psycopg.connect(database["admin"], autocommit=True) as conn:
                assert conn.execute("SELECT next_due FROM markos_runtime.schedules").fetchone() == (
                    d.start_at,
                )
                conn.execute("DROP TRIGGER fail_schedule ON markos_runtime.outbox")
            assert await s.scan_once() == 2

    asyncio.run(scenario())


def test_schedule_versions_are_immutable_and_old_occurrences_retained(
    database: dict[str, str],
) -> None:
    async def scenario() -> None:
        async with DurableStore.connect(database["publisher"]) as store:
            s = Scheduler(store)
            d = schedule(missed_policy="coalesce")
            await s.register(d)
            await s.register(d)
            assert await s.scan_once() == 1
            with pytest.raises(StorageError):
                await s.register(d.model_copy(update={"interval_seconds": 120}))
            await s.register(d.model_copy(update={"version": 2}))
            assert await s.scan_once() == 1
            with pytest.raises(StorageError):
                await s.register(d)

    asyncio.run(scenario())
    assert count(database, "schedule_versions") == count(database, "schedule_occurrences") == 2


def test_disable_is_fenced_and_explicit_resume_recovers_missed_work(
    database: dict[str, str],
) -> None:
    async def scenario() -> None:
        async with DurableStore.connect(database["publisher"]) as store:
            s = Scheduler(store)
            d = schedule()
            await s.register(d)
            assert not await s.set_enabled(d.schedule_id, 2, False)
            assert await s.set_enabled(d.schedule_id, 1, False)
            assert await s.scan_once() == 0
            assert await s.set_enabled(d.schedule_id, 1, True)
            assert await s.scan_once() == 2

    asyncio.run(scenario())
    assert count(database, "schedule_controls") == 2


def test_scheduler_owner_domain_and_grant_denials(database: dict[str, str]) -> None:
    async def scenario() -> None:
        async with DurableStore.connect(database["publisher"]) as store:
            s = Scheduler(store)
            with pytest.raises(StorageError):
                await s.register(schedule(domain="work"))
            d = schedule()
            await s.register(d)
        async with DurableStore.connect(database["otherpub"]) as other:
            assert await Scheduler(other).scan_once() == 0
            assert not await Scheduler(other).set_enabled(d.schedule_id, 1, False)
        async with DurableStore.connect(database["consumer"]) as consumer:
            with pytest.raises(StorageError):
                await Scheduler(consumer).scan_once()

    asyncio.run(scenario())
    for table in [
        "schedules",
        "schedule_versions",
        "schedule_scans",
        "schedule_occurrences",
        "schedule_controls",
    ]:
        with psycopg.connect(database["otherpub"]) as conn:
            assert conn.execute(f"SELECT count(*) FROM markos_runtime.{table}").fetchone() == (0,)


def test_failed_schedule_transition_is_atomic_for_all_due_schedules(
    database: dict[str, str],
) -> None:
    async def scenario() -> None:
        async with DurableStore.connect(database["publisher"]) as store:
            s = Scheduler(store)
            await s.register(schedule())
            await s.register(schedule())
            with psycopg.connect(database["admin"], autocommit=True) as conn:
                conn.execute(
                    "UPDATE markos_runtime.schedules SET "
                    "definition=jsonb_set(definition,'{timezone}','\"invalid/zone\"') "
                    "WHERE schedule_id=(SELECT max(schedule_id::text)::uuid FROM "
                    "markos_runtime.schedules)"
                )
            with pytest.raises(ValueError):
                await s.scan_once()

    asyncio.run(scenario())
    assert count(database, "events") == count(database, "schedule_scans") == 0


@pytest.mark.parametrize(
    "changes",
    [
        {"kind": None},
        {"schema_version": None},
        {"version": True},
        {"catch_up_limit": 101},
        {"timezone": "invalid/zone"},
        {"task_type": "send.email"},
    ],
)
def test_direct_sql_cannot_bypass_schedule_contract(
    database: dict[str, str], changes: dict[str, Any]
) -> None:
    import json

    from psycopg.types.json import Jsonb

    doc = json.loads(schedule().model_dump_json())
    doc.update(changes)
    with (
        psycopg.connect(database["publisher"], autocommit=True) as conn,
        pytest.raises(psycopg.Error),
    ):
        conn.execute("SELECT markos_runtime.register_schedule(%s)", (Jsonb(doc),))
    assert count(database, "schedules") == 0
