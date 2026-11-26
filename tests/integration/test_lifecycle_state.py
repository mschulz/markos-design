import asyncio
from uuid import uuid4

import psycopg
import pytest

from markos_runtime.store.database import DurableStore, StorageError
from tests.integration.test_storage import event


def test_restore_generation_rejects_colliding_task_outbox_and_review_fences(
    database: dict[str, str],
) -> None:
    async def scenario() -> None:
        item = event()
        executor = uuid4()
        async with (
            DurableStore.connect(database["publisher"]) as pub,
            DurableStore.connect(database["consumer"]) as cons,
        ):
            await pub.append(item)
            await cons.handoff(item)
            dispatch = (await pub.claim_outbox())[0]
            task = await cons.claim_task(executor)
            assert task
            await cons.review_delivery(b"synthetic invalid", "invalid")
            review = (await cons.claim_reviews())[0]
            old = await pub.current_epoch()
            with psycopg.connect(database["migration"], autocommit=True) as migrator:
                fresh = migrator.execute("SELECT markos_runtime.fence_restored_state()").fetchone()
                assert fresh and fresh[0] != old
            with psycopg.connect(database["admin"], autocommit=True) as admin:
                # A restored numeric fence can collide; the old generation must still be denied.
                admin.execute("UPDATE markos_runtime.outbox SET fence=%s", (dispatch.fence - 1,))
                admin.execute(
                    "DELETE FROM markos_runtime.task_attempts WHERE task_id=%s", (task.task_id,)
                )
                admin.execute("UPDATE markos_runtime.tasks SET fence=%s", (task.fence - 1,))
                admin.execute(
                    "UPDATE markos_runtime.review_outbox SET fence=%s", (review.fence - 1,)
                )
            current = (await pub.claim_outbox())[0]
            new_task = await cons.claim_task(executor)
            assert new_task
            new_review = (await cons.claim_reviews())[0]
            assert (
                current.fence == dispatch.fence
                and new_task.fence == task.fence
                and new_review.fence == review.fence
            )
            assert not await pub.authorize_publication(
                dispatch.event_id, dispatch.fence, epoch=dispatch.epoch
            )
            assert not await pub.record_publication(
                dispatch.event_id, dispatch.fence, epoch=dispatch.epoch
            )
            assert not await pub.retry_claim(dispatch, 1, "confirm")
            assert not await cons.finish_task(task, executor)
            assert not await cons.renew_task(task, executor)
            assert not await cons.finish_review(review)
            assert await pub.record_publication(
                current.event_id, current.fence, epoch=current.epoch
            )
            assert await cons.finish_task(new_task, executor)
            assert await cons.finish_review(new_review)

    asyncio.run(scenario())


def test_heartbeat_scope_generation_and_instance_budget(database: dict[str, str]) -> None:
    async def scenario() -> None:
        async with DurableStore.connect(database["consumer"]) as cons:
            epoch = await cons.current_epoch()
            for _ in range(4):
                await cons.heartbeat("worker", uuid4(), epoch, "ready")
            with pytest.raises(StorageError):
                await cons.heartbeat("worker", uuid4(), epoch, "ready")
            with pytest.raises(StorageError):
                await cons.heartbeat("worker", uuid4(), uuid4(), "ready")
        with psycopg.connect(database["othercons"]) as other:
            assert other.execute(
                "SELECT count(*) FROM markos_runtime.service_heartbeats"
            ).fetchone() == (0,)

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "query",
    [
        "SELECT markos_runtime.fence_restored_state()",
        "SELECT markos_runtime.finish_task(NULL,NULL,NULL,NULL,1)",
        "SELECT markos_runtime.claim_outbox(1,60)",
        "UPDATE markos_runtime.runtime_epoch SET epoch=gen_random_uuid()",
    ],
)
def test_runtime_cannot_bypass_restore_or_legacy_fencing(
    database: dict[str, str], query: str
) -> None:
    with (
        psycopg.connect(database["consumer"], autocommit=True) as conn,
        pytest.raises(psycopg.Error),
    ):
        conn.execute(query)
