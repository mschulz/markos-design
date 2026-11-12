import asyncio
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from pathlib import Path
from uuid import UUID, uuid4

import psycopg
import pytest
from psycopg import sql

from markos_runtime.contracts.events import SourceReference
from markos_runtime.contracts.replay import ReplayRequest
from markos_runtime.execution.worker import Worker
from markos_runtime.recovery.service import Recovery
from markos_runtime.store.database import DurableStore, StorageError
from tests.integration.test_storage import count, event


def clock(database: dict[str, str], when: datetime) -> None:
    # Only this isolated test DB's migration identity can replace the clock.
    body = "SELECT TIMESTAMPTZ '" + when.isoformat() + "'"
    with psycopg.connect(database["migration"], autocommit=True) as conn:
        conn.execute(
            sql.SQL(
                "CREATE OR REPLACE FUNCTION markos_runtime.recovery_now() "
                "RETURNS timestamptz LANGUAGE sql VOLATILE SET search_path=pg_catalog AS {}"
            ).format(sql.Literal(body))
        )


async def seed(database: dict[str, str], *, completed: bool = False) -> tuple[UUID, datetime]:
    clock(database, datetime.now(UTC))
    item = event()
    async with DurableStore.connect(database["publisher"]) as pub:
        await pub.append(item)
        with psycopg.connect(database["admin"]) as conn:
            row = conn.execute(
                "SELECT created_at FROM markos_runtime.events WHERE event_id=%s", (item.event_id,)
            ).fetchone()
            assert row is not None
            start = row[0].astimezone(UTC)
        clock(database, start)
        claim = (await pub.claim_outbox())[0]
        assert await pub.record_publication(item.event_id, claim.fence)
    if completed:
        for role in ["consumer", "audit"]:
            async with DurableStore.connect(database[role]) as cons:
                await cons.handoff(item)
                assert await Worker(cons).run_once()
    return item.event_id, start


def test_reconcile_includes_confirmed_events_and_is_idempotent(database: dict[str, str]) -> None:
    async def scenario() -> None:
        eid, start = await seed(database)
        clock(database, start + timedelta(seconds=61))
        async with DurableStore.connect(database["publisher"]) as pub:
            recovery = Recovery(pub)
            assert await recovery.reconcile_once() == 1
            assert await recovery.reconcile_once() == 0
            claim = (await pub.claim_outbox())[0]
            assert claim.event_id == eid and claim.event.event_id == eid
            assert claim.consumers == ("foundation-audit", "foundation-worker")

    asyncio.run(scenario())
    assert count(database, "replay_history") == 1


def test_competing_replay_requests_reserve_once(database: dict[str, str]) -> None:
    async def scenario() -> None:
        eid, _ = await seed(database)
        async with (
            DurableStore.connect(database["publisher"]) as a,
            DurableStore.connect(database["publisher"]) as b,
        ):
            results = await asyncio.gather(
                *(
                    Recovery(s).request(ReplayRequest(schema_version=1, event_id=eid))
                    for s in (a, b, a, b)
                )
            )
            assert results.count("queued") == 1 and results.count("pending") == 3

    asyncio.run(scenario())


def test_global_replay_budget_spaces_requests_across_replicas(database: dict[str, str]) -> None:
    async def scenario() -> None:
        a, _ = await seed(database)
        b, _ = await seed(database)
        async with DurableStore.connect(database["publisher"]) as pub:
            assert (
                await Recovery(pub).request(ReplayRequest(schema_version=1, event_id=a)) == "queued"
            )
            assert (
                await Recovery(pub).request(ReplayRequest(schema_version=1, event_id=b)) == "queued"
            )

    asyncio.run(scenario())
    with psycopg.connect(database["admin"]) as conn:
        rows = conn.execute(
            "SELECT available_at FROM markos_runtime.replay_history ORDER BY available_at"
        ).fetchall()
        assert rows[1][0] - rows[0][0] >= timedelta(seconds=0.1)


def test_age_boundary_and_expiring_one_use_permit(database: dict[str, str]) -> None:
    async def scenario() -> None:
        eid, _ = await seed(database)
        # The 30-day window crosses New York's DST change; caller timezone must not alter it.
        start = datetime(2026, 10, 13, 12, tzinfo=UTC)
        with psycopg.connect(database["admin"], autocommit=True) as conn:
            conn.execute("UPDATE markos_runtime.events SET occurred_at=%s", (start,))
        async with DurableStore.connect(database["publisher"]) as pub:
            recovery = Recovery(pub)
            clock(database, start + timedelta(days=30))
            assert await recovery.request(ReplayRequest(schema_version=1, event_id=eid)) == "queued"
            claim = (await pub.claim_outbox())[0]
            assert await pub.record_publication(eid, claim.fence)
            clock(database, start + timedelta(days=30, microseconds=1))
            with psycopg.connect(database["publisher"], autocommit=True) as raw:
                raw.execute("SET TIME ZONE 'America/New_York'")
                assert raw.execute(
                    "SELECT markos_runtime.request_replay(%s,NULL)", (eid,)
                ).fetchone() == ("review",)
            assert await recovery.request(ReplayRequest(schema_version=1, event_id=eid)) == "review"
            permit = await recovery.approve(eid, seconds=1)
            clock(database, start + timedelta(days=30, seconds=2))
            assert (
                await recovery.request(
                    ReplayRequest(schema_version=1, event_id=eid, permit_id=permit)
                )
                == "review"
            )
            permit = await recovery.approve(eid, seconds=60)
            assert (
                await recovery.request(
                    ReplayRequest(schema_version=1, event_id=eid, permit_id=permit)
                )
                == "queued"
            )
            assert (
                await recovery.request(
                    ReplayRequest(schema_version=1, event_id=eid, permit_id=permit)
                )
                == "review"
            )

    asyncio.run(scenario())


def test_permit_is_bound_to_event_hash_and_targets(database: dict[str, str]) -> None:
    async def scenario() -> None:
        eid, start = await seed(database)
        clock(database, start + timedelta(days=31))
        async with DurableStore.connect(database["publisher"]) as pub:
            permit = await Recovery(pub).approve(eid)
            other, _ = await seed(database)
            assert (
                await Recovery(pub).request(
                    ReplayRequest(schema_version=1, event_id=other, permit_id=permit)
                )
                == "review"
            )
            with psycopg.connect(database["admin"], autocommit=True) as conn:
                conn.execute(
                    "UPDATE markos_runtime.replay_permits SET "
                    "body_hash=repeat('0',64) WHERE permit_id=%s",
                    (permit,),
                )
            assert (
                await Recovery(pub).request(
                    ReplayRequest(schema_version=1, event_id=eid, permit_id=permit)
                )
                == "review"
            )

    asyncio.run(scenario())


def test_permit_expiry_after_publication_blocks_handoff(database: dict[str, str]) -> None:
    async def scenario() -> None:
        eid, start = await seed(database)
        clock(database, start + timedelta(days=31))
        async with DurableStore.connect(database["publisher"]) as pub:
            recovery = Recovery(pub)
            permit = await recovery.approve(eid, seconds=60)
            assert (
                await recovery.request(
                    ReplayRequest(schema_version=1, event_id=eid, permit_id=permit)
                )
                == "queued"
            )
            with psycopg.connect(database["admin"], autocommit=True) as conn:
                conn.execute("UPDATE markos_runtime.outbox SET available_at=clock_timestamp()")
            claim = (await pub.claim_outbox())[0]
            assert await pub.record_publication(eid, claim.fence)
        clock(database, start + timedelta(days=31, seconds=61))
        async with DurableStore.connect(database["consumer"]) as cons:
            assert (await cons.handoff(claim.event)).status == "review"
        assert count(database, "tasks") == 0

    asyncio.run(scenario())


def test_payload_30_day_and_dedup_90_day_floors(database: dict[str, str]) -> None:
    async def scenario() -> None:
        eid, start = await seed(database, completed=True)
        async with DurableStore.connect(database["publisher"]) as pub:
            recovery = Recovery(pub)
            clock(database, start + timedelta(days=30, microseconds=-1))
            assert await recovery.cleanup_once() == ()
            clock(database, start + timedelta(days=30))
            audit = count(database, "retention_audit")
            assert (await recovery.cleanup_once())[0].action == "payload_retired"
            assert count(database, "retention_audit") == audit
            with psycopg.connect(database["admin"]) as conn:
                assert conn.execute(
                    "SELECT wire IS NOT NULL FROM markos_runtime.events"
                ).fetchone() == (True,)
            await recovery.cleanup_once(dry_run=False)
            assert count(database, "inbox") == count(database, "task_results") == 2
            with psycopg.connect(database["admin"]) as conn:
                assert conn.execute("SELECT wire FROM markos_runtime.events").fetchone() == (None,)
            clock(database, start + timedelta(days=90, microseconds=-1))
            assert await recovery.cleanup_once() == ()
            clock(database, start + timedelta(days=90))
            assert (await recovery.cleanup_once(dry_run=False))[0].action == "metadata_purged"
            assert (
                count(database, "events")
                == count(database, "tasks")
                == count(database, "inbox")
                == 0
            )
            assert count(database, "event_tombstones") == 1
            assert (
                await recovery.request(ReplayRequest(schema_version=1, event_id=eid)) == "retired"
            )

    asyncio.run(scenario())


def test_unfinished_dispatch_and_unresolved_review_hold_payload(database: dict[str, str]) -> None:
    async def scenario() -> None:
        eid, start = await seed(database)
        clock(database, start + timedelta(days=365))
        async with DurableStore.connect(database["publisher"]) as pub:
            assert await Recovery(pub).cleanup_once(dry_run=False) == ()
        assert count(database, "events") == 1

    asyncio.run(scenario())


def test_completed_event_quarantine_is_held_until_explicit_resolution(
    database: dict[str, str],
) -> None:
    async def scenario() -> None:
        eid, start = await seed(database, completed=True)
        with psycopg.connect(database["admin"], autocommit=True) as conn:
            conn.execute(
                "INSERT INTO markos_runtime.quarantine "
                "VALUES(%s,'personal','foundation-generator',%s,repeat('0',64),'event_conflict',clock_timestamp(),NULL)",
                ("11111111-1111-4111-8111-111111111111", eid),
            )
        clock(database, start + timedelta(days=100))
        async with DurableStore.connect(database["publisher"]) as pub:
            recovery = Recovery(pub)
            assert await recovery.cleanup_once(dry_run=False) == ()
            await recovery.resolve_reviews(eid)
            assert (await recovery.cleanup_once())[0].action == "payload_retired"
            assert count(database, "tasks") == 2  # Resolution restarts the activity floor.

    asyncio.run(scenario())


def test_deletion_fences_stale_claims_and_prevents_resurrection(database: dict[str, str]) -> None:
    async def scenario() -> None:
        item = event()
        async with (
            DurableStore.connect(database["publisher"]) as pub,
            DurableStore.connect(database["consumer"]) as cons,
        ):
            await pub.append(item)
            await cons.handoff(item)
            claim = (await pub.claim_outbox())[0]
            owner = uuid4()
            task = await cons.claim_task(owner)
            assert task
            await Recovery(pub).delete_event(item.event_id)
            assert not await pub.authorize_publication(item.event_id, claim.fence)
            assert not await pub.record_publication(item.event_id, claim.fence)
            assert not await cons.finish_task(task, owner)
            assert (await cons.handoff(item)).status == "review"
            assert (await pub.append(item)).status == "review"
            assert (
                await Recovery(pub).request(ReplayRequest(schema_version=1, event_id=item.event_id))
                == "review"
            )
            with psycopg.connect(database["admin"]) as conn:
                assert conn.execute("SELECT wire FROM markos_runtime.events").fetchone() == (None,)

    asyncio.run(scenario())
    assert count(database, "task_results") == 0


@pytest.mark.parametrize("failure", ["missing", "unavailable", "checksum", "grant"])
def test_source_access_is_rechecked_before_replay(database: dict[str, str], failure: str) -> None:
    async def scenario() -> None:
        source = uuid4()
        body = b"synthetic source"
        item = event().model_copy(
            update={
                "payload": None,
                "payload_ref": SourceReference(
                    source_id=source,
                    owner_id=UUID("11111111-1111-4111-8111-111111111111"),
                    domain="personal",
                    sha256=sha256(body).hexdigest(),
                ),
            }
        )
        async with DurableStore.connect(database["publisher"]) as pub:
            recovery = Recovery(pub)
            services = ("foundation-generator", "foundation-worker", "foundation-audit")
            await recovery.observe_synthetic_source(source, body, services)
            assert (await pub.append(item)).status == "created"
            claim = (await pub.claim_outbox())[0]
            assert await pub.record_publication(item.event_id, claim.fence)
            if failure == "missing":
                with psycopg.connect(database["admin"], autocommit=True) as conn:
                    conn.execute("DELETE FROM markos_runtime.source_grants")
                    conn.execute("DELETE FROM markos_runtime.source_observations")
            else:
                await recovery.observe_synthetic_source(
                    source,
                    b"changed" if failure == "checksum" else body,
                    services[:1] if failure == "grant" else services,
                    accessible=failure != "unavailable",
                )
            assert (
                await recovery.request(ReplayRequest(schema_version=1, event_id=item.event_id))
                == "review"
            )
        async with DurableStore.connect(database["consumer"]) as cons:
            assert (await cons.handoff(item)).status == "review"

    asyncio.run(scenario())
    assert count(database, "tasks") == 0


def test_source_deletion_redacts_all_references_and_blocks_reimport(
    database: dict[str, str],
) -> None:
    async def scenario() -> None:
        source = uuid4()
        body = b"synthetic original"
        item = event().model_copy(
            update={
                "payload": None,
                "payload_ref": SourceReference(
                    source_id=source,
                    owner_id=UUID("11111111-1111-4111-8111-111111111111"),
                    domain="personal",
                    sha256=sha256(body).hexdigest(),
                ),
            }
        )
        async with DurableStore.connect(database["publisher"]) as pub:
            recovery = Recovery(pub)
            await recovery.observe_synthetic_source(
                source, body, ("foundation-generator", "foundation-worker", "foundation-audit")
            )
            await pub.append(item)
            await recovery.delete_source(source)
            with pytest.raises(StorageError):
                await recovery.observe_synthetic_source(source, body, ())
            newer = item.model_copy(update={"event_id": uuid4(), "operation_key": str(uuid4())})
            assert (await pub.append(newer)).status == "review"
            with psycopg.connect(database["admin"]) as conn:
                assert conn.execute(
                    "SELECT count(*) FROM markos_runtime.events WHERE wire IS NOT NULL"
                ).fetchone() == (0,)

    asyncio.run(scenario())


@pytest.mark.parametrize("role", ["otherpub", "workpub", "consumer", "unmapped"])
def test_cross_scope_and_privileged_helper_denials(database: dict[str, str], role: str) -> None:
    async def scenario() -> None:
        eid, _ = await seed(database)
        async with DurableStore.connect(database[role]) as store:
            with pytest.raises(StorageError):
                await Recovery(store).request(ReplayRequest(schema_version=1, event_id=eid))
            with pytest.raises(StorageError):
                await Recovery(store).delete_event(eid)

    asyncio.run(scenario())
    for query in [
        "SELECT markos_runtime.recovery_now()",
        "SELECT markos_runtime.append_event_m0b('x'::bytea)",
        "SELECT markos_runtime.redact_event(NULL,NULL,NULL)",
        "UPDATE markos_runtime.replay_budget SET next_at='epoch'",
    ]:
        with psycopg.connect(database[role], autocommit=True) as conn, pytest.raises(psycopg.Error):
            conn.execute(query)


def test_cleanup_fault_rolls_back_entire_deletion_graph(database: dict[str, str]) -> None:
    async def scenario() -> None:
        _, start = await seed(database, completed=True)
        clock(database, start + timedelta(days=91))
        with psycopg.connect(database["admin"], autocommit=True) as conn:
            conn.execute(
                "CREATE FUNCTION markos_runtime.fail_cleanup() RETURNS trigger "
                "LANGUAGE plpgsql AS $$BEGIN RAISE EXCEPTION 'synthetic';END$$"
            )
            conn.execute(
                "CREATE TRIGGER fail_cleanup BEFORE DELETE ON markos_runtime.events "
                "FOR EACH ROW EXECUTE FUNCTION markos_runtime.fail_cleanup()"
            )
        async with DurableStore.connect(database["publisher"]) as pub:
            with pytest.raises(StorageError):
                await Recovery(pub).cleanup_once(dry_run=False)
        assert (
            count(database, "task_results")
            == count(database, "inbox")
            == count(database, "tasks")
            == 2
        )

    asyncio.run(scenario())


def test_reviewed_old_handoffs_resume_only_fixed_noop_under_valid_permit(
    database: dict[str, str],
) -> None:
    from markos_runtime.contracts.events import EventEnvelope

    async def scenario() -> None:
        eid, start = await seed(database)
        with psycopg.connect(database["admin"]) as conn:
            row = conn.execute("SELECT wire FROM markos_runtime.events").fetchone()
            assert row is not None
            item = EventEnvelope.from_wire(bytes(row[0]))
        for role in ["consumer", "audit"]:
            async with DurableStore.connect(database[role]) as cons:
                await cons.handoff(item)
        clock(database, start + timedelta(days=31))
        for role in ["consumer", "audit"]:
            async with DurableStore.connect(database[role]) as cons:
                assert not await Worker(cons).run_once()
        async with DurableStore.connect(database["publisher"]) as pub:
            recovery = Recovery(pub)
            assert await recovery.request(ReplayRequest(schema_version=1, event_id=eid)) == "review"
            permit = await recovery.approve(eid, seconds=60)
            assert (
                await recovery.request(
                    ReplayRequest(schema_version=1, event_id=eid, permit_id=permit)
                )
                == "resumed"
            )
        for role in ["consumer", "audit"]:
            async with DurableStore.connect(database[role]) as cons:
                assert await Worker(cons).run_once()
        assert count(database, "task_results") == 2

    asyncio.run(scenario())


@pytest.mark.parametrize("state", ["unpublished", "pending_task", "running_task"])
def test_unfinished_work_is_never_age_purged(database: dict[str, str], state: str) -> None:
    from markos_runtime.contracts.events import EventEnvelope

    async def scenario() -> None:
        eid, start = await seed(database)
        with psycopg.connect(database["admin"], autocommit=True) as conn:
            row = conn.execute("SELECT wire FROM markos_runtime.events").fetchone()
            assert row
            item = EventEnvelope.from_wire(bytes(row[0]))
            if state == "unpublished":
                conn.execute("UPDATE markos_runtime.outbox SET state='pending'")
        if state != "unpublished":
            for role in ["consumer", "audit"]:
                async with DurableStore.connect(database[role]) as cons:
                    await cons.handoff(item)
            if state == "running_task":
                async with DurableStore.connect(database["consumer"]) as cons:
                    assert await cons.claim_task(uuid4())
        clock(database, start + timedelta(days=365))
        async with DurableStore.connect(database["publisher"]) as pub:
            assert await Recovery(pub).cleanup_once(dry_run=False) == ()
        with psycopg.connect(database["admin"]) as conn:
            assert conn.execute(
                "SELECT wire IS NOT NULL FROM markos_runtime.events"
            ).fetchone() == (True,)

    asyncio.run(scenario())


def test_capabilities_are_required_even_in_the_correct_owner_domain(
    database: dict[str, str],
) -> None:
    async def scenario() -> None:
        eid, _ = await seed(database)
        with psycopg.connect(database["admin"], autocommit=True) as conn:
            conn.execute(
                "UPDATE markos_runtime.service_identities SET can_replay=false,"
                "can_manage_retention=false,can_approve_replay=false "
                "WHERE service_id='foundation-generator'"
            )
        async with DurableStore.connect(database["publisher"]) as pub:
            recovery = Recovery(pub)
            with pytest.raises(StorageError):
                await recovery.reconcile_once()
            with pytest.raises(StorageError):
                await recovery.approve(eid)
            with pytest.raises(StorageError):
                await recovery.cleanup_once(dry_run=False)
            with pytest.raises(StorageError):
                await recovery.observe_synthetic_source(uuid4(), b"synthetic", ())

    asyncio.run(scenario())


def test_shared_operation_retains_primary_task_until_alias_graph_is_released(
    database: dict[str, str],
) -> None:
    from markos_runtime.contracts.events import EventEnvelope

    async def scenario() -> None:
        _, start = await seed(database, completed=True)
        with psycopg.connect(database["admin"]) as conn:
            row = conn.execute("SELECT wire FROM markos_runtime.events").fetchone()
            assert row
            alias = EventEnvelope.from_wire(bytes(row[0])).model_copy(update={"event_id": uuid4()})
        async with DurableStore.connect(database["publisher"]) as pub:
            assert (await pub.append(alias)).status == "created"
            claim = (await pub.claim_outbox())[0]
            assert await pub.record_publication(alias.event_id, claim.fence)
        for role in ["consumer", "audit"]:
            async with DurableStore.connect(database[role]) as cons:
                assert (await cons.handoff(alias)).status == "duplicate"
        clock(database, start + timedelta(days=91))
        async with DurableStore.connect(database["publisher"]) as pub:
            recovery = Recovery(pub)
            await recovery.cleanup_once(dry_run=False)
            assert count(database, "events") == 1 and count(database, "task_results") == 2
            await recovery.cleanup_once(dry_run=False)
            assert count(database, "events") == count(database, "tasks") == 0

    asyncio.run(scenario())


def test_held_old_records_do_not_starve_later_cleanup_batches(database: dict[str, str]) -> None:
    async def scenario() -> None:
        first, start = await seed(database, completed=True)
        later, _ = await seed(database, completed=True)
        with psycopg.connect(database["admin"], autocommit=True) as conn:
            conn.execute(
                "INSERT INTO markos_runtime.recovery_reviews"
                "(owner_id,domain,service_id,event_id,reason) "
                "VALUES(%s,'personal','foundation-generator',%s,'aged')",
                ("11111111-1111-4111-8111-111111111111", first),
            )
        clock(database, start + timedelta(days=91))
        async with DurableStore.connect(database["publisher"]) as pub:
            decisions = await Recovery(pub).cleanup_once(limit=1, dry_run=False)
            assert len(decisions) == 1 and decisions[0].event_id == later
        assert count(database, "events") == 1

    asyncio.run(scenario())


def test_upgrade_preserves_existing_m0d_results_and_sets_conservative_floor(
    admin_dsn: str, tmp_path: Path
) -> None:
    from psycopg.conninfo import make_conninfo
    from psycopg.rows import dict_row

    from markos_runtime.store.migrations import migrate
    from tests.integration.conftest import ROLES

    name = "markos_upgrade_" + uuid4().hex
    directory = Path(__file__).resolve().parents[4] / "infrastructure/migrations"
    for file in sorted(directory.glob("*.sql"))[:3]:
        (tmp_path / file.name).write_bytes(file.read_bytes())
    with psycopg.connect(admin_dsn, autocommit=True) as admin:
        admin.execute(
            sql.SQL("CREATE DATABASE {} OWNER markos_migrator").format(sql.Identifier(name))
        )
    migration = make_conninfo(
        admin_dsn, dbname=name, user="markos_migrator", password="synthetic-test-only"
    )
    dsns = {
        role: make_conninfo(admin_dsn, dbname=name, user=values[0], password="synthetic-test-only")
        for role, values in ROLES.items()
    }

    async def scenario() -> None:
        async with await psycopg.AsyncConnection.connect(
            migration, autocommit=True, row_factory=dict_row
        ) as conn:
            await migrate(conn, tmp_path)
            for identity in ROLES.values():
                await conn.execute(
                    "INSERT INTO markos_runtime.service_identities "
                    "(login_role,service_id,owner_id,domain,can_publish,can_consume) "
                    "VALUES(%s,%s,%s,%s,%s,%s)",
                    identity,
                )
            await conn.execute(
                "UPDATE markos_runtime.service_identities SET can_execute=can_consume"
            )
        item = event()
        async with DurableStore.connect(dsns["publisher"]) as pub:
            await pub.append(item)
            claim = (await pub.claim_outbox())[0]
            assert await pub.record_publication(item.event_id, claim.fence)
        for role in ["consumer", "audit"]:
            async with DurableStore.connect(dsns[role]) as store:
                await store.handoff(item)
                assert await Worker(store).run_once()
        async with await psycopg.AsyncConnection.connect(
            migration, autocommit=True, row_factory=dict_row
        ) as conn:
            await migrate(conn, directory)
            row = await (
                await conn.execute("SELECT wire,terminal_at FROM markos_runtime.events")
            ).fetchone()
            assert row and bytes(row["wire"]) == item.to_wire() and row["terminal_at"] is not None
            total = await (
                await conn.execute("SELECT count(*) FROM markos_runtime.task_results")
            ).fetchone()
            assert total and total["count"] == 2
        async with DurableStore.connect(dsns["publisher"]) as pub:
            with pytest.raises(StorageError):
                await Recovery(pub).reconcile_once()

    try:
        asyncio.run(scenario())
    finally:
        with psycopg.connect(admin_dsn, autocommit=True) as admin:
            admin.execute(sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(name)))
