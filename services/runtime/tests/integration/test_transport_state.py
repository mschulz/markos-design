import asyncio

import psycopg
import pytest

from markos_runtime.store import DurableStore, StorageError
from markos_runtime.store.database import StorageRejected
from tests.integration.test_storage import count, event


def test_retry_delay_and_original_identity_are_durable(database: dict[str, str]) -> None:
    async def scenario() -> None:
        async with DurableStore.connect(database["publisher"]) as store:
            item = event()
            await store.append(item)
            first = (await store.claim_outbox())[0]
            assert len(first.consumers) == 2
            assert await store.retry_claim(first, 1, "confirm")
            assert await store.claim_outbox() == ()
            with psycopg.connect(database["admin"], autocommit=True) as admin:
                admin.execute(
                    "UPDATE markos_runtime.outbox SET available_at=clock_timestamp()-interval '1s'"
                )
            second = (await store.claim_outbox())[0]
            assert (
                second.event_id == first.event_id
                and second.attempts == 1
                and second.fence > first.fence
            )
            assert not await store.record_publication(first.event_id, first.fence)

    asyncio.run(scenario())


def test_review_reference_and_outbox_are_atomic_and_idempotent(database: dict[str, str]) -> None:
    async def scenario() -> None:
        async with DurableStore.connect(database["consumer"]) as store:
            first = await store.review_delivery(b"synthetic malformed", "invalid")
            assert await store.review_delivery(b"synthetic malformed", "invalid") == first
            claims = await store.claim_reviews()
            assert len(claims) == 1 and claims[0].reference.review_id == first
            assert claims[0].reference.schema_version == 1
            assert await store.finish_review(claims[0])
            assert not await store.finish_review(claims[0])
        async with DurableStore.connect(database["audit"]) as store:
            assert await store.claim_reviews() == ()

    asyncio.run(scenario())
    assert count(database, "delivery_reviews") == count(database, "review_outbox") == 1


def test_failed_review_outbox_rolls_back_quarantine(database: dict[str, str]) -> None:
    with psycopg.connect(database["admin"], autocommit=True) as admin:
        admin.execute(
            "CREATE FUNCTION markos_runtime.fail_review() RETURNS trigger LANGUAGE plpgsql "
            "AS $$BEGIN RAISE EXCEPTION 'synthetic fault'; END$$"
        )
        admin.execute(
            "CREATE TRIGGER fail_review BEFORE INSERT ON markos_runtime.review_outbox "
            "FOR EACH ROW EXECUTE FUNCTION markos_runtime.fail_review()"
        )

    async def scenario() -> None:
        async with DurableStore.connect(database["consumer"]) as store:
            with pytest.raises(StorageError):
                await store.review_delivery(b"synthetic malformed", "invalid")

    asyncio.run(scenario())
    assert count(database, "delivery_reviews") == count(database, "review_outbox") == 0


def test_unknown_registered_identity_is_permanent_rejection(database: dict[str, str]) -> None:
    async def scenario() -> None:
        async with DurableStore.connect(database["consumer"]) as store:
            with pytest.raises(StorageRejected):
                await store.handoff(event())

    asyncio.run(scenario())


def test_cancelled_transaction_discards_connection_before_pool_reuse(
    database: dict[str, str],
) -> None:
    async def scenario() -> None:
        async with DurableStore.connect(database["consumer"]) as store:
            with pytest.raises(TimeoutError):
                async with asyncio.timeout(0.05), store._transaction() as connection:
                    await connection.execute("SELECT pg_sleep(1)")
            assert connection.closed
            assert (await store.identity()).service_id == "foundation-worker"

    asyncio.run(scenario())


def test_expired_outbox_is_held_for_review(database: dict[str, str]) -> None:
    async def scenario() -> None:
        async with DurableStore.connect(database["publisher"]) as store:
            assert (await store.append(event(expired=True))).status == "review"
            assert await store.claim_outbox() == ()

    asyncio.run(scenario())
    with psycopg.connect(database["admin"]) as admin:
        assert admin.execute("SELECT state FROM markos_runtime.outbox").fetchone() == ("review",)
