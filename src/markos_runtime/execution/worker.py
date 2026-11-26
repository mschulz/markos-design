"""One bounded synthetic no-op task. PostgreSQL owns claims and finalization."""

import asyncio
from contextlib import suppress
from uuid import uuid4

from markos_runtime.backoff import backoff
from markos_runtime.store.database import DurableStore, StorageError, TaskClaim


class Worker:
    def __init__(self, store: DurableStore, *, timeout_seconds: float = 10) -> None:
        if not 0 < timeout_seconds <= 10:
            raise StorageError("invalid synthetic execution deadline")
        self.store = store
        self.executor_id = uuid4()
        self.timeout_seconds = timeout_seconds
        self._running = False
        self.failed = False

    async def _execute(self, claim: TaskClaim) -> None:
        # Fixed allowlist: no plugins/tools/providers or arbitrary callable injection.
        if claim.event.event_type != "foundation.noop.requested" or claim.event.payload is None:
            raise StorageError("synthetic inline no-op required")
        await asyncio.sleep(0)

    async def run_once(self) -> bool:
        if self._running or self.failed:
            raise StorageError("worker unavailable")
        self._running = True
        task: asyncio.Task[None] | None = None
        claim: TaskClaim | None = None
        try:
            claim = await self.store.claim_task(self.executor_id)
            if claim is None:
                return False
            task = asyncio.create_task(self._execute(claim))
            done, _ = await asyncio.wait({task}, timeout=self.timeout_seconds)
            if not done:
                task.cancel()
                # Never finalize a timeout as success, even if a handler ignores cancellation.
                await self.store.finish_task(
                    claim, self.executor_id, failure="timeout", delay=backoff(claim.attempt)
                )
                await self._drain(task)
                return False
            try:
                task.result()
            except Exception:
                await self.store.finish_task(
                    claim, self.executor_id, failure="execution", delay=backoff(claim.attempt)
                )
                return False
            return await self.store.finish_task(claim, self.executor_id)
        except asyncio.CancelledError:
            if task is not None:
                task.cancel()
                await self._drain(task)
            if claim is not None:
                with suppress(StorageError):
                    await self.store.finish_task(
                        claim, self.executor_id, failure="cancelled", delay=1
                    )
            raise
        finally:
            if task is not None and not task.done():
                task.cancel()
                await self._drain(task)
            self._running = False

    async def _drain(self, task: asyncio.Task[None]) -> None:
        _, pending = await asyncio.wait({task}, timeout=2)
        if pending:
            self.failed = True  # Do not start overlapping handlers in this process.
        else:
            with suppress(asyncio.CancelledError, Exception):
                task.result()
