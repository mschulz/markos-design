import asyncio
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import uuid4

import psycopg
import pytest
from psycopg.rows import dict_row

from markos_runtime.contracts import EventEnvelope
from markos_runtime.store import DurableStore, StorageError
from markos_runtime.store.migrations import migrate


def event(
    *,
    source: str = "foundation-generator",
    domain: str = "personal",
    owner: str = "11111111-1111-4111-8111-111111111111",
    operation: str | None = None,
    label: str = "synthetic",
    expired: bool = False,
) -> EventEnvelope:
    now = datetime.now(UTC)
    body: dict[str, Any] = {
        "schema_version": 1,
        "event_id": str(uuid4()),
        "event_type": "foundation.noop.requested",
        "owner_id": owner,
        "domain": domain,
        "source": source,
        "occurred_at": (now - timedelta(hours=2)).isoformat(),
        "correlation_id": str(uuid4()),
        "causation_id": None,
        "trace_id": str(uuid4()),
        "sensitivity": "internal",
        "operation_key": operation or str(uuid4()),
        "payload": {"label": label},
    }
    if expired:
        body["expires_at"] = (now - timedelta(hours=1)).isoformat()
    return EventEnvelope.from_wire(json.dumps(body).encode())


def count(database: dict[str, str], table: str) -> int:
    # Only hard-coded test table names enter this helper.
    from psycopg import sql

    with psycopg.connect(database["admin"]) as connection:
        row = connection.execute(
            sql.SQL("SELECT count(*) FROM markos_runtime.{}").format(sql.Identifier(table))
        ).fetchone()
        assert row is not None
        return int(row[0])


def test_event_outbox_consumers_atomic_and_duplicate(database: dict[str, str]) -> None:
    async def scenario() -> None:
        item = event()
        async with DurableStore.connect(database["publisher"]) as store:
            assert (await store.append(item)).status == "created"
            assert (await store.append(item)).status == "duplicate"
        assert count(database, "events") == count(database, "outbox") == 1
        assert count(database, "event_consumers") == 2

    asyncio.run(scenario())


def test_failed_event_write_rolls_back_all_rows(database: dict[str, str]) -> None:
    with psycopg.connect(database["admin"], autocommit=True) as admin:
        admin.execute(
            "CREATE FUNCTION markos_runtime.fail_insert() RETURNS trigger LANGUAGE plpgsql "
            "AS $$BEGIN RAISE EXCEPTION 'synthetic fault'; END$$"
        )
        admin.execute(
            "CREATE TRIGGER fail_outbox BEFORE INSERT ON markos_runtime.outbox "
            "FOR EACH ROW EXECUTE FUNCTION markos_runtime.fail_insert()"
        )

    async def scenario() -> None:
        async with DurableStore.connect(database["publisher"]) as store:
            with pytest.raises(StorageError):
                await store.append(event())

    asyncio.run(scenario())
    assert (
        count(database, "events")
        == count(database, "event_consumers")
        == count(database, "outbox")
        == 0
    )


def test_conflicting_event_persists_bounded_quarantine(database: dict[str, str]) -> None:
    async def scenario() -> None:
        item = event()
        changed = item.model_copy(update={"operation_key": "changed"})
        async with DurableStore.connect(database["publisher"]) as store:
            await store.append(item)
            for _ in range(3):
                assert (await store.append(changed)).status == "conflict"
        assert (
            count(database, "events")
            == count(database, "outbox")
            == count(database, "quarantine")
            == 1
        )

    asyncio.run(scenario())


def test_handoff_commit_duplicate_and_independent_consumers(database: dict[str, str]) -> None:
    async def scenario() -> None:
        item = event()
        async with DurableStore.connect(database["publisher"]) as store:
            await store.append(item)
        async with DurableStore.connect(database["consumer"]) as store:
            first = await store.handoff(item)
            assert first.status == "created" and first.task_id
        # A new pool/connection simulates a redelivery after the first commit.
        async with DurableStore.connect(database["consumer"]) as store:
            second = await store.handoff(item)
            assert second.status == "duplicate" and second.task_id == first.task_id
        async with DurableStore.connect(database["audit"]) as store:
            independent = await store.handoff(item)
            assert independent.status == "created" and independent.task_id != first.task_id
        assert count(database, "inbox") == count(database, "tasks") == 2

    asyncio.run(scenario())


def test_inbox_failure_rolls_back_task_and_dispatch(database: dict[str, str]) -> None:
    with psycopg.connect(database["admin"], autocommit=True) as admin:
        admin.execute(
            "CREATE FUNCTION markos_runtime.fail_receipt() RETURNS trigger LANGUAGE plpgsql "
            "AS $$BEGIN RAISE EXCEPTION 'synthetic failure'; END$$"
        )
        admin.execute(
            "CREATE TRIGGER fail_inbox BEFORE INSERT ON markos_runtime.inbox "
            "FOR EACH ROW EXECUTE FUNCTION markos_runtime.fail_receipt()"
        )

    async def scenario() -> None:
        item = event()
        async with DurableStore.connect(database["publisher"]) as store:
            await store.append(item)
        async with DurableStore.connect(database["consumer"]) as store:
            with pytest.raises(StorageError):
                await store.handoff(item)

    asyncio.run(scenario())
    assert count(database, "tasks") == count(database, "inbox") == 0
    with psycopg.connect(database["admin"]) as admin:
        assert admin.execute(
            "SELECT count(*) FROM markos_runtime.event_consumers WHERE handed_off_at IS NOT NULL"
        ).fetchone() == (0,)


def test_operation_key_dedup_and_conflicting_payload(database: dict[str, str]) -> None:
    async def scenario() -> None:
        items = [
            event(operation="same"),
            event(operation="same"),
            event(operation="same", label="changed"),
        ]
        async with DurableStore.connect(database["publisher"]) as store:
            for item in items:
                await store.append(item)
        async with DurableStore.connect(database["consumer"]) as store:
            first, second, third = [await store.handoff(item) for item in items]
            assert first.task_id == second.task_id and second.status == "duplicate"
            assert third.status == "conflict" and third.task_id is None
        assert count(database, "tasks") == 1 and count(database, "inbox") == 2
        assert count(database, "quarantine") == 1

    asyncio.run(scenario())


def test_expired_event_is_reviewed_without_task(database: dict[str, str]) -> None:
    async def scenario() -> None:
        item = event(expired=True)
        async with DurableStore.connect(database["publisher"]) as store:
            await store.append(item)
        async with DurableStore.connect(database["consumer"]) as store:
            assert (await store.handoff(item)).status == "review"
        assert count(database, "tasks") == count(database, "inbox") == 0
        assert count(database, "recovery_reviews") == 2

    asyncio.run(scenario())


@pytest.mark.parametrize("role", ["workpub", "otherpub", "consumer", "unmapped"])
def test_publish_scope_rejected(database: dict[str, str], role: str) -> None:
    async def scenario() -> None:
        async with DurableStore.connect(database[role]) as store:
            with pytest.raises(StorageError):
                await store.append(event())

    asyncio.run(scenario())
    assert count(database, "events") == 0


@pytest.mark.parametrize("role", ["workcons", "othercons", "publisher", "unmapped"])
def test_handoff_scope_rejected(database: dict[str, str], role: str) -> None:
    async def scenario() -> None:
        item = event()
        async with DurableStore.connect(database["publisher"]) as store:
            await store.append(item)
        async with DurableStore.connect(database[role]) as store:
            with pytest.raises(StorageError):
                await store.handoff(item)

    asyncio.run(scenario())
    assert count(database, "tasks") == 0


def test_rls_no_scope_escalation_or_direct_writes(database: dict[str, str]) -> None:
    async def seed() -> None:
        async with DurableStore.connect(database["publisher"]) as store:
            await store.append(event())

    asyncio.run(seed())
    with psycopg.connect(database["workcons"], autocommit=True) as client:
        client.execute(
            "SELECT set_config('markos.owner_id','11111111-1111-4111-8111-111111111111',false)"
        )
        assert client.execute("SELECT count(*) FROM markos_runtime.events").fetchone() == (0,)
        for query in [
            "SET ROLE markos_migrator",
            "SELECT * FROM markos_runtime.service_identities",
            "DELETE FROM markos_runtime.events",
            "TRUNCATE markos_runtime.events",
            "CREATE TABLE markos_runtime.illegal(id integer)",
        ]:
            with pytest.raises(psycopg.Error):
                client.execute(query)
    with psycopg.connect(database["publisher"]) as client:
        assert client.execute("SELECT count(*) FROM markos_runtime.events").fetchone() == (1,)


def test_concurrent_handoffs_create_one_scoped_task(database: dict[str, str]) -> None:
    async def scenario() -> None:
        item = event()
        async with DurableStore.connect(database["publisher"]) as store:
            await store.append(item)
        async with DurableStore.connect(database["consumer"]) as store:
            results = await asyncio.gather(*(store.handoff(item) for _ in range(4)))
            assert sorted(r.status for r in results) == [
                "created",
                "duplicate",
                "duplicate",
                "duplicate",
            ]
            assert len({r.task_id for r in results}) == 1

    asyncio.run(scenario())
    assert count(database, "tasks") == count(database, "inbox") == 1


def test_concurrent_claims_and_fenced_expired_owner(database: dict[str, str]) -> None:
    async def scenario() -> None:
        async with DurableStore.connect(database["publisher"]) as store:
            for _ in range(4):
                await store.append(event())
            left, right = await asyncio.gather(
                store.claim_outbox(limit=2), store.claim_outbox(limit=2)
            )
            assert len(left) == len(right) == 2
            assert not {x.event_id for x in left} & {x.event_id for x in right}
            stale = left[0]
            with psycopg.connect(database["admin"], autocommit=True) as admin:
                admin.execute(
                    "UPDATE markos_runtime.outbox SET lease_until=clock_timestamp()-interval '1s' "
                    "WHERE event_id=%s",
                    (stale.event_id,),
                )
            assert not await store.record_publication(
                stale.event_id, stale.fence, epoch=stale.epoch
            )
            fresh = (await store.claim_outbox(limit=1))[0]
            assert fresh.event_id == stale.event_id and fresh.fence > stale.fence
            assert not await store.record_publication(
                stale.event_id, stale.fence, epoch=stale.epoch
            )
            assert await store.record_publication(fresh.event_id, fresh.fence, epoch=fresh.epoch)
            assert not await store.record_publication(
                fresh.event_id, fresh.fence, epoch=fresh.epoch
            )

    asyncio.run(scenario())


def test_claim_skips_locked_rows(database: dict[str, str]) -> None:
    async def scenario() -> None:
        async with DurableStore.connect(database["publisher"]) as store:
            for _ in range(2):
                await store.append(event())
            with psycopg.connect(database["admin"]) as admin:
                locked = admin.execute(
                    "SELECT event_id FROM markos_runtime.outbox ORDER BY event_id "
                    "LIMIT 1 FOR UPDATE"
                ).fetchone()
                assert locked is not None
                result = await store.claim_outbox(limit=2)
                assert len(result) == 1 and result[0].event_id != locked[0]

    asyncio.run(scenario())


def test_admin_identity_not_accepted_by_runtime(database: dict[str, str]) -> None:
    async def scenario() -> None:
        for role in ["admin", "migration"]:
            with pytest.raises(StorageError, match="non-admin"):
                async with DurableStore.connect(database[role]):
                    pass

    asyncio.run(scenario())


def test_migrations_idempotent_and_changed_bytes_rejected(
    database: dict[str, str], tmp_path: Path
) -> None:
    directory = Path(__file__).resolve().parents[4] / "infrastructure/migrations"

    async def scenario() -> None:
        async with await psycopg.AsyncConnection.connect(
            database["migration"], autocommit=True, row_factory=dict_row
        ) as connection:
            await migrate(connection, directory)
            path = directory / "0001_durable_foundation.sql"
            for existing in directory.glob("*.sql"):
                (tmp_path / existing.name).write_bytes(existing.read_bytes())
            (tmp_path / path.name).write_bytes(path.read_bytes() + b"\n-- changed")
            with pytest.raises(StorageError, match="checksum"):
                await migrate(connection, tmp_path)
        async with await psycopg.AsyncConnection.connect(
            database["consumer"], autocommit=True, row_factory=dict_row
        ) as client:
            with pytest.raises(StorageError):
                await migrate(client, directory)

    asyncio.run(scenario())
    with psycopg.connect(database["admin"]) as admin:
        assert admin.execute(
            "SELECT count(*) FROM markos_runtime.schema_migrations"
        ).fetchone() == (12,)


def test_no_intended_consumers_rolls_back_event(database: dict[str, str]) -> None:
    with psycopg.connect(database["admin"], autocommit=True) as admin:
        admin.execute(
            "UPDATE markos_runtime.service_identities SET can_consume=false,can_publish=true "
            "WHERE domain='personal'"
        )

    async def scenario() -> None:
        async with DurableStore.connect(database["publisher"]) as store:
            with pytest.raises(StorageError):
                await store.append(event())

    asyncio.run(scenario())
    assert count(database, "events") == count(database, "outbox") == 0


def test_consumer_changed_event_is_quarantined(database: dict[str, str]) -> None:
    async def scenario() -> None:
        item = event()
        changed = item.model_copy(update={"operation_key": "tampered"})
        async with DurableStore.connect(database["publisher"]) as store:
            await store.append(item)
        async with DurableStore.connect(database["consumer"]) as store:
            assert (await store.handoff(changed)).status == "conflict"

    asyncio.run(scenario())
    assert count(database, "tasks") == count(database, "inbox") == 0
    assert count(database, "quarantine") == 1


def test_positive_owner_domain_and_consumer_isolation(database: dict[str, str]) -> None:
    async def scenario() -> None:
        cases = [
            ("publisher", "consumer", event(operation="same")),
            (
                "workpub",
                "workcons",
                event(source="work-generator", domain="work", operation="same"),
            ),
            (
                "otherpub",
                "othercons",
                event(
                    source="other-generator",
                    owner="99999999-9999-4999-8999-999999999999",
                    operation="same",
                ),
            ),
        ]
        for publisher, consumer, item in cases:
            async with DurableStore.connect(database[publisher]) as store:
                assert (await store.append(item)).status == "created"
            async with DurableStore.connect(database[consumer]) as store:
                assert (await store.handoff(item)).status == "created"
        for consumer in ["consumer", "workcons", "othercons"]:
            with psycopg.connect(database[consumer]) as client:
                assert client.execute("SELECT count(*) FROM markos_runtime.events").fetchone() == (
                    1,
                )
                assert client.execute("SELECT count(*) FROM markos_runtime.tasks").fetchone() == (
                    1,
                )
                assert client.execute("SELECT count(*) FROM markos_runtime.inbox").fetchone() == (
                    1,
                )
        with psycopg.connect(database["audit"]) as client:
            assert client.execute("SELECT count(*) FROM markos_runtime.tasks").fetchone() == (0,)

    asyncio.run(scenario())


def test_concurrent_business_operation_dedup(database: dict[str, str]) -> None:
    async def scenario() -> None:
        items = [event(operation="shared"), event(operation="shared")]
        async with DurableStore.connect(database["publisher"]) as store:
            for item in items:
                await store.append(item)
        async with DurableStore.connect(database["consumer"]) as store:
            receipts = await asyncio.gather(*(store.handoff(item) for item in items))
            assert {r.status for r in receipts} == {"created", "duplicate"}
            assert len({r.task_id for r in receipts}) == 1

    asyncio.run(scenario())
    assert count(database, "tasks") == 1 and count(database, "inbox") == 2


def test_failed_migration_transaction_rolls_back_ddl_and_ledger(
    database: dict[str, str], tmp_path: Path
) -> None:
    directory = Path(__file__).resolve().parents[4] / "infrastructure/migrations"
    for existing in directory.glob("*.sql"):
        (tmp_path / existing.name).write_bytes(existing.read_bytes())
    (tmp_path / "0013_fault.sql").write_text(
        "CREATE TABLE markos_runtime.must_rollback(id integer); SELECT missing_function();"
    )

    async def scenario() -> None:
        async with await psycopg.AsyncConnection.connect(
            database["migration"], autocommit=True, row_factory=dict_row
        ) as connection:
            with pytest.raises(StorageError, match="migration failed"):
                await migrate(connection, tmp_path)

    asyncio.run(scenario())
    with psycopg.connect(database["admin"]) as admin:
        assert admin.execute("SELECT to_regclass('markos_runtime.must_rollback')").fetchone() == (
            None,
        )
        assert admin.execute(
            "SELECT count(*) FROM markos_runtime.schema_migrations"
        ).fetchone() == (12,)


@pytest.mark.parametrize(
    "mutation",
    [
        "unknown",
        "bool-version",
        "nil-id",
        "naive-time",
        "payload-type",
        "foreign-reference",
        "duplicate-root",
        "duplicate-payload",
    ],
)
def test_direct_sql_cannot_bypass_envelope_validation(
    database: dict[str, str], mutation: str
) -> None:
    document = json.loads(event().to_wire())
    if mutation == "unknown":
        document["extra"] = "not-allowed"
    elif mutation == "bool-version":
        document["schema_version"] = True
    elif mutation == "nil-id":
        document["trace_id"] = "00000000-0000-0000-0000-000000000000"
    elif mutation == "naive-time":
        document["occurred_at"] = "2026-11-12T00:00:00"
    elif mutation == "payload-type":
        document["payload"] = {"label": 1}
    elif mutation == "foreign-reference":
        document["payload"] = None
        document["payload_ref"] = {
            "source_id": str(uuid4()),
            "owner_id": str(uuid4()),
            "domain": "personal",
            "sha256": "a" * 64,
        }
    body = json.dumps(document).encode()
    if mutation == "duplicate-root":
        body = body[:-1] + b',"domain":"personal"}'
    elif mutation == "duplicate-payload":
        body = body.replace(b'{"label": "synthetic"}', b'{"label":"a","label":"b"}')
    with (
        psycopg.connect(database["publisher"], autocommit=True) as client,
        pytest.raises(psycopg.Error),
    ):
        client.execute("SELECT markos_runtime.append_event(%s)", (body,))
    assert count(database, "events") == count(database, "outbox") == 0
