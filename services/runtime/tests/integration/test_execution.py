import asyncio
from datetime import timedelta
from uuid import uuid4

import psycopg
import pytest

from markos_runtime.execution.worker import Worker
from markos_runtime.store.database import DurableStore, StorageError, TaskClaim
from tests.integration.test_storage import count, event


def expire(database: dict[str, str]) -> None:
    with psycopg.connect(database["admin"], autocommit=True) as admin:
        admin.execute(
            "UPDATE markos_runtime.tasks SET lease_until=clock_timestamp()-interval "
            "'1s',available_at=clock_timestamp()-interval '1s'"
        )
        admin.execute(
            "UPDATE markos_runtime.execution_slots SET lease_until=clock_timestamp()-interval '1s'"
        )


async def seed(database: dict[str, str], number: int = 1) -> None:
    async with (
        DurableStore.connect(database["publisher"]) as pub,
        DurableStore.connect(database["consumer"]) as cons,
    ):
        for _ in range(number):
            item = event()
            await pub.append(item)
            await cons.handoff(item)


def test_synthetic_worker_completes_once_after_duplicate_handoff(database: dict[str, str]) -> None:
    async def scenario() -> None:
        await seed(database)
        async with DurableStore.connect(database["consumer"]) as store:
            assert await Worker(store).run_once()
            assert not await Worker(store).run_once()

    asyncio.run(scenario())
    assert count(database, "task_results") == count(database, "task_attempts") == 1
    with psycopg.connect(database["consumer"]) as conn:
        result = conn.execute("SELECT result FROM markos_runtime.task_results").fetchone()
        assert result is not None and result[0]["effects"] == "none"


def test_competing_replicas_cannot_exceed_two_global_leases(database: dict[str, str]) -> None:
    async def scenario() -> None:
        await seed(database, 4)
        async with (
            DurableStore.connect(database["consumer"]) as a,
            DurableStore.connect(database["consumer"]) as b,
        ):
            claims = await asyncio.gather(*(s.claim_task(uuid4()) for s in (a, b, a, b)))
            assert sum(c is not None for c in claims) == 2
            assert len({c.task_id for c in claims if c}) == 2

    asyncio.run(scenario())


def test_global_limit_applies_across_domains_and_owners(database: dict[str, str]) -> None:
    async def scenario() -> None:
        await seed(database, 2)
        async with (
            DurableStore.connect(database["workpub"]) as pub,
            DurableStore.connect(database["workcons"]) as other,
        ):
            item = event(source="work-generator", domain="work")
            await pub.append(item)
            await other.handoff(item)
            async with DurableStore.connect(database["consumer"]) as store:
                assert await store.claim_task(uuid4())
                assert await other.claim_task(uuid4())
                assert await store.claim_task(uuid4()) is None
                async with (
                    DurableStore.connect(database["otherpub"]) as pub2,
                    DurableStore.connect(database["othercons"]) as other2,
                ):
                    item2 = event(
                        source="other-generator", owner="99999999-9999-4999-8999-999999999999"
                    )
                    await pub2.append(item2)
                    await other2.handoff(item2)
                    assert await other2.claim_task(uuid4()) is None

    asyncio.run(scenario())


def test_crash_reclaims_with_higher_fence_and_rejects_late_completion(
    database: dict[str, str],
) -> None:
    async def scenario() -> None:
        await seed(database)
        old_owner, new_owner = uuid4(), uuid4()
        async with DurableStore.connect(database["consumer"]) as store:
            old = await store.claim_task(old_owner)
            assert old
            expire(database)
            new = await store.claim_task(new_owner)
            assert new
            assert old.task_id == new.task_id and new.fence > old.fence and new.attempt == 2
            assert not await store.finish_task(old, old_owner)
            assert not await store.renew_task(old, old_owner)
            assert not await store.finish_task(new, old_owner)
            assert await store.finish_task(new, new_owner)

    asyncio.run(scenario())
    assert count(database, "task_results") == 1
    with psycopg.connect(database["admin"]) as conn:
        assert conn.execute(
            "SELECT outcome FROM markos_runtime.task_attempts ORDER BY fence"
        ).fetchall() == [("abandoned",), ("completed",)]


def test_expired_lease_cannot_finalize_even_before_takeover(database: dict[str, str]) -> None:
    async def scenario() -> None:
        await seed(database)
        async with DurableStore.connect(database["consumer"]) as store:
            owner = uuid4()
            claim = await store.claim_task(owner)
            assert claim
            expire(database)
            assert not await store.finish_task(claim, owner)

    asyncio.run(scenario())
    assert count(database, "task_results") == 0


def test_renewal_and_slot_release_are_scoped(database: dict[str, str]) -> None:
    async def scenario() -> None:
        await seed(database, 3)
        async with (
            DurableStore.connect(database["consumer"]) as store,
            DurableStore.connect(database["audit"]) as other,
        ):
            owner = uuid4()
            claim = await store.claim_task(owner)
            assert claim
            assert await store.renew_task(claim, owner, seconds=60)
            assert not await other.finish_task(claim, owner)
            assert not await other.renew_task(claim, owner)
            assert await store.finish_task(claim, owner)
            assert not await store.finish_task(claim, owner)
            assert await store.claim_task(uuid4()) and await store.claim_task(uuid4())

    asyncio.run(scenario())


def test_failures_back_off_then_enter_durable_review(database: dict[str, str]) -> None:
    async def scenario() -> None:
        await seed(database)
        async with DurableStore.connect(database["consumer"]) as store:
            owner = uuid4()
            for attempt in range(1, 4):
                claim = await store.claim_task(owner)
                assert claim and claim.attempt == attempt
                assert await store.finish_task(claim, owner, failure="execution", delay=1) == (
                    attempt < 3
                )
                assert await store.claim_task(owner) is None
                expire(database)
            assert await store.claim_task(owner) is None

    asyncio.run(scenario())
    assert count(database, "execution_reviews") == 1 and count(database, "task_results") == 0


def test_crash_exhaustion_is_reviewed_without_a_fourth_execution(database: dict[str, str]) -> None:
    async def scenario() -> None:
        await seed(database)
        async with DurableStore.connect(database["consumer"]) as store:
            for _ in range(3):
                assert await store.claim_task(uuid4())
                expire(database)
            assert await store.claim_task(uuid4()) is None

    asyncio.run(scenario())
    assert count(database, "execution_reviews") == 1 and count(database, "task_attempts") == 3


def test_expiry_is_rechecked_at_execution_and_completion(database: dict[str, str]) -> None:
    async def scenario() -> None:
        await seed(database, 2)
        async with DurableStore.connect(database["consumer"]) as store:
            owner = uuid4()
            claim = await store.claim_task(owner)
            assert claim
            with psycopg.connect(database["admin"], autocommit=True) as conn:
                conn.execute(
                    "UPDATE markos_runtime.events SET expires_at=clock_timestamp()-interval '1s'"
                )
            assert not await store.finish_task(claim, owner)
            assert await store.claim_task(owner) is None

    asyncio.run(scenario())
    assert count(database, "execution_reviews") == 2 and count(database, "task_results") == 0


def test_source_references_cannot_execute_without_current_access_checks(
    database: dict[str, str],
) -> None:
    async def scenario() -> None:
        item = event().model_copy(update={"payload": None, "payload_ref": None})
        from markos_runtime.contracts.events import SourceReference

        item = item.model_copy(
            update={
                "payload_ref": SourceReference(
                    source_id=uuid4(), owner_id=item.owner_id, domain=item.domain, sha256="0" * 64
                )
            }
        )
        async with (
            DurableStore.connect(database["publisher"]) as pub,
            DurableStore.connect(database["consumer"]) as cons,
        ):
            await pub.append(item)
            await cons.handoff(item)
            assert not await Worker(cons).run_once()

    asyncio.run(scenario())
    assert count(database, "execution_reviews") == 1


def test_completion_fault_rolls_back_result_state_and_slot_release(
    database: dict[str, str],
) -> None:
    async def scenario() -> None:
        await seed(database)
        async with DurableStore.connect(database["consumer"]) as store:
            owner = uuid4()
            claim = await store.claim_task(owner)
            assert claim
            with psycopg.connect(database["admin"], autocommit=True) as admin:
                admin.execute(
                    "CREATE FUNCTION markos_runtime.fail_result() RETURNS trigger LANGUAGE "
                    "plpgsql AS $$BEGIN RAISE EXCEPTION 'synthetic';END$$"
                )
                admin.execute(
                    "CREATE TRIGGER fail_result BEFORE INSERT ON "
                    "markos_runtime.task_results FOR EACH ROW EXECUTE FUNCTION "
                    "markos_runtime.fail_result()"
                )
            with pytest.raises(StorageError):
                await store.finish_task(claim, owner)
            assert count(database, "task_results") == 0
            with psycopg.connect(database["admin"], autocommit=True) as admin:
                assert admin.execute("SELECT state FROM markos_runtime.tasks").fetchone() == (
                    "running",
                )
                admin.execute("DROP TRIGGER fail_result ON markos_runtime.task_results")
            assert await store.finish_task(claim, owner)

    asyncio.run(scenario())


def test_runtime_cannot_grant_execution_or_change_slots(database: dict[str, str]) -> None:
    with psycopg.connect(database["admin"], autocommit=True) as admin:
        admin.execute(
            "UPDATE markos_runtime.service_identities SET can_execute=false WHERE "
            "service_id='foundation-worker'"
        )

    async def scenario() -> None:
        async with DurableStore.connect(database["consumer"]) as store:
            with pytest.raises(StorageError):
                await store.claim_task(uuid4())

    asyncio.run(scenario())
    for query in [
        "UPDATE markos_runtime.service_identities SET can_execute=true",
        "UPDATE markos_runtime.execution_slots SET lease_until=NULL",
        "DELETE FROM markos_runtime.task_attempts",
    ]:
        with (
            psycopg.connect(database["consumer"], autocommit=True) as conn,
            pytest.raises(psycopg.Error),
        ):
            conn.execute(query)


def test_worker_timeout_and_cancellation_leave_recoverable_task(database: dict[str, str]) -> None:
    async def scenario() -> None:
        await seed(database)
        async with DurableStore.connect(database["consumer"]) as store:
            worker = Worker(store, timeout_seconds=0.01)

            async def slow(claim: TaskClaim) -> None:
                await asyncio.sleep(60)

            worker._execute = slow  # type: ignore[method-assign]
            assert not await worker.run_once()
            expire(database)
            worker = Worker(store)
            entered = asyncio.Event()

            async def wait(claim: TaskClaim) -> None:
                entered.set()
                await asyncio.sleep(60)

            worker._execute = wait  # type: ignore[method-assign]
            task = asyncio.create_task(worker.run_once())
            await entered.wait()
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            assert count(database, "task_results") == 0
            expire(database)
            assert await Worker(store).run_once()

    asyncio.run(scenario())


def test_database_failure_does_not_run_handler(database: dict[str, str]) -> None:
    async def scenario() -> None:
        await seed(database)
        with psycopg.connect(database["admin"], autocommit=True) as admin:
            admin.execute(
                "REVOKE EXECUTE ON FUNCTION markos_runtime.claim_task(uuid,integer) FROM "
                "markos_runtime_client"
            )
        async with DurableStore.connect(database["consumer"]) as store:
            worker = Worker(store)

            async def forbidden(claim: TaskClaim) -> None:
                pytest.fail("unrecorded execution")

            worker._execute = forbidden  # type: ignore[method-assign]
            with pytest.raises(StorageError):
                await worker.run_once()

    asyncio.run(scenario())
    assert count(database, "task_attempts") == 0


def test_hard_process_exit_recovers_committed_claim(database: dict[str, str]) -> None:
    import json
    import subprocess
    import sys

    asyncio.run(seed(database))
    script = """
import asyncio,json,os,sys
from uuid import uuid4
from markos_runtime.store.database import DurableStore
async def main():
    async with DurableStore.connect(json.load(sys.stdin)) as store:
        claim=await store.claim_task(uuid4())
        assert claim is not None
        os._exit(0)  # No finally, graceful release or in-process state survives.
asyncio.run(main())
"""
    result = subprocess.run(
        [sys.executable, "-c", script],
        input=json.dumps(database["consumer"]),
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert result.returncode == 0
    expire(database)

    async def recovered() -> None:
        async with DurableStore.connect(database["consumer"]) as store:
            assert await Worker(store).run_once()

    asyncio.run(recovered())
    assert count(database, "task_results") == 1 and count(database, "task_attempts") == 2


def test_finalization_database_failure_never_records_uncommitted_success(
    database: dict[str, str],
) -> None:
    async def scenario() -> None:
        await seed(database)
        with psycopg.connect(database["admin"], autocommit=True) as conn:
            conn.execute(
                "REVOKE EXECUTE ON FUNCTION "
                "markos_runtime.finish_task(uuid,bigint,uuid,text,integer) "
                "FROM markos_runtime_client"
            )
        async with DurableStore.connect(database["consumer"]) as store:
            with pytest.raises(StorageError):
                await Worker(store).run_once()
            assert count(database, "task_results") == 0
            with psycopg.connect(database["admin"], autocommit=True) as conn:
                conn.execute(
                    "GRANT EXECUTE ON FUNCTION "
                    "markos_runtime.finish_task(uuid,bigint,uuid,text,integer) "
                    "TO markos_runtime_client"
                )
            expire(database)
            assert await Worker(store).run_once()

    asyncio.run(scenario())


def test_invalid_execution_budgets_and_cross_owner_visibility(database: dict[str, str]) -> None:
    async def scenario() -> None:
        await seed(database)
        async with DurableStore.connect(database["consumer"]) as store:
            for seconds in [0, 301, True]:
                with pytest.raises(StorageError):
                    await store.claim_task(uuid4(), lease_seconds=seconds)
            owner = uuid4()
            claim = await store.claim_task(owner)
            assert claim
            async with DurableStore.connect(database["othercons"]) as other:
                assert not await other.finish_task(claim, owner)
            with pytest.raises(StorageError):
                await store.finish_task(claim, owner, failure="private secret error")

    asyncio.run(scenario())
    for table in ["tasks", "task_attempts", "execution_reviews", "task_results"]:
        with psycopg.connect(database["othercons"]) as conn:
            assert conn.execute(f"SELECT count(*) FROM markos_runtime.{table}").fetchone() == (0,)


def test_transaction_clock_is_utc_even_with_non_utc_session(database: dict[str, str]) -> None:
    async def scenario() -> None:
        async with DurableStore.connect(database["consumer"], max_connections=1) as store:
            async with store._pool.connection() as conn:
                await conn.execute("SET TIME ZONE 'America/New_York'")
            async with store._transaction() as conn:
                row = await (await conn.execute("SELECT clock_timestamp()")).fetchone()
                assert row is not None and row["clock_timestamp"].utcoffset() == timedelta(0)

    asyncio.run(scenario())


def test_cancellation_resistant_handler_cannot_finalize_late(database: dict[str, str]) -> None:
    async def scenario() -> None:
        await seed(database)
        async with DurableStore.connect(database["consumer"]) as store:
            worker = Worker(store, timeout_seconds=0.01)
            release = asyncio.Event()

            async def resistant(claim: TaskClaim) -> None:
                while not release.is_set():
                    try:
                        await release.wait()
                    except asyncio.CancelledError:
                        continue

            worker._execute = resistant  # type: ignore[method-assign]
            running = asyncio.create_task(worker.run_once())
            try:
                async with asyncio.timeout(10):
                    while not worker.failed:
                        await asyncio.sleep(0.01)
            finally:
                release.set()
            assert not await running
            with pytest.raises(StorageError):
                await worker.run_once()
            assert count(database, "task_results") == 0

    asyncio.run(scenario())
