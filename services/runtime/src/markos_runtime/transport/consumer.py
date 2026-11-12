"""Dedicated channel, manual ack after durable handoff and independent deadlines."""

import asyncio
from contextlib import suppress

from aio_pika.abc import AbstractIncomingMessage, AbstractRobustChannel

from markos_runtime.config import ProposedLimits
from markos_runtime.contracts.base import MAX_EVENT_BYTES, ContractError
from markos_runtime.contracts.events import EventEnvelope
from markos_runtime.store.database import DurableStore, StorageRejected, StoreIdentity
from markos_runtime.transport.broker import BrokerSession, TransportError


class Consumer:
    def __init__(
        self,
        session: BrokerSession,
        store: DurableStore,
        publisher_users: dict[str, str],
        *,
        active_seconds: float | None = None,
        total_seconds: float | None = None,
        limits: ProposedLimits | None = None,
    ) -> None:
        self.limits: ProposedLimits = limits or getattr(session, "limits", ProposedLimits())
        active_seconds = (
            self.limits.active_handoff_seconds if active_seconds is None else active_seconds
        )
        total_seconds = (
            self.limits.total_delivery_seconds if total_seconds is None else total_seconds
        )
        if (
            not 0 < active_seconds < total_seconds <= self.limits.total_delivery_seconds
            or active_seconds > self.limits.active_handoff_seconds
        ):
            raise TransportError("invalid handoff deadlines")
        self.session, self.store, self.publisher_users = session, store, dict(publisher_users)
        self.active_seconds, self.total_seconds = active_seconds, total_seconds
        self._handoffs = asyncio.Semaphore(self.limits.handoffs_per_process)
        self._tasks: set[asyncio.Task[None]] = set()
        self._closed = asyncio.Event()
        self._channel: AbstractRobustChannel | None = None
        self._supervisor: asyncio.Task[None] | None = None
        self.identity: StoreIdentity | None = None
        self.ready = asyncio.Event()
        self._failed = asyncio.Event()
        self._epoch = 0
        self.failure: TransportError | None = None

    def _closed_callback(self, *args: object) -> None:
        self._failed.set()

    async def start(self) -> None:
        if self._supervisor is not None:
            raise TransportError("consumer already started")
        self.identity = await self.store.identity()
        if not self.identity.can_consume:
            raise TransportError("consumer identity not granted")
        # First subscription fails closed; later recovery is single-owner and serialized.
        await self._subscribe()
        self._supervisor = asyncio.create_task(self._recover())

    async def _subscribe(self) -> None:
        assert self.identity is not None
        channel = await self.session.channel(publisher=False)
        self._channel = channel
        self._epoch += 1
        epoch = self._epoch
        self._failed.clear()
        channel.close_callbacks.add(self._closed_callback)
        try:
            await channel.set_qos(prefetch_count=self.limits.prefetch)
            queue = await channel.declare_queue(
                f"markos.{self.identity.domain}.{self.identity.service_id}",
                passive=True,
                robust=False,
            )

            async def callback(message: AbstractIncomingMessage) -> None:
                await self._callback(message, epoch)

            await queue.consume(callback, no_ack=False, robust=False)
            self.ready.set()
        except Exception:
            await self.session.close_channel(channel)
            raise TransportError("consumer subscription unavailable") from None

    def _deadline(self, task: asyncio.Task[None]) -> None:
        # This watchdog signals recovery independently of coroutine cancellation progress.
        self.ready.clear()
        self._epoch += 1
        self._failed.set()
        task.cancel()

    async def _callback(self, message: AbstractIncomingMessage, epoch: int | None = None) -> None:
        task = asyncio.current_task()
        assert task is not None
        generation = self._epoch if epoch is None else epoch
        self._tasks.add(task)
        loop = asyncio.get_running_loop()
        total_timer = loop.call_later(self.total_seconds, self._deadline, task)
        active_timer: asyncio.TimerHandle | None = None
        try:
            async with self._handoffs:
                active_timer = loop.call_later(self.active_seconds, self._deadline, task)
                await self._handoff(message)
                active_timer.cancel()
            if generation != self._epoch or self._failed.is_set() or self._closed.is_set():
                return  # Late/uncertain handlers never acknowledge an old channel generation.
            await message.ack()
        except asyncio.CancelledError:
            if self._closed.is_set():
                raise
            self.ready.clear()
            self._failed.set()
        except Exception:
            self.ready.clear()
            self._failed.set()
        finally:
            total_timer.cancel()
            if active_timer is not None:
                active_timer.cancel()
            self._tasks.discard(task)

    async def _drain(self) -> bool:
        tasks = tuple(self._tasks)
        for task in tasks:
            task.cancel()
        if not tasks:
            return True
        _, pending = await asyncio.wait(tasks, timeout=self.limits.close_seconds)
        if pending:
            self.session.factory.abort()
            self.failure = TransportError("handoff cancellation incomplete; consumer stopped")
            return False
        return True

    async def _handoff(self, message: AbstractIncomingMessage) -> None:
        assert self.identity is not None
        body = message.body
        try:
            event = EventEnvelope.from_wire(body)
        except ContractError:
            await self.store.review_delivery(
                body, "oversize" if len(body) > MAX_EVENT_BYTES else "invalid"
            )
            return
        if (
            message.content_type != "application/json"
            or message.message_id != str(event.event_id)
            or message.type != event.event_type
            or message.user_id != self.publisher_users.get(event.source)
            or message.user_id is None
            or (event.owner_id, event.domain) != (self.identity.owner_id, self.identity.domain)
        ):
            await self.store.review_delivery(body, "unauthorized")
            return
        try:
            result = await self.store.handoff(event)
        except StorageRejected:
            await self.store.review_delivery(body, "unauthorized")
            return
        if result.status in {"conflict", "expired"}:
            await self.store.review_delivery(body, result.status)

    async def _recover(self) -> None:
        attempts = 0
        while not self._closed.is_set():
            await self._failed.wait()
            self.ready.clear()
            self._epoch += 1
            if self._channel is not None:
                await self.session.close_channel(self._channel)
            if not await self._drain():
                await self.session.close()
                return
            if self._closed.is_set():
                break
            await asyncio.sleep(min(300, 2 ** min(attempts, 8)))
            if self._closed.is_set():
                break
            try:
                await self._subscribe()
                attempts = 0
            except TransportError:
                attempts += 1
                self._failed.set()

    async def stop(self) -> None:
        self._closed.set()
        self._failed.set()
        self.ready.clear()
        if self._supervisor is not None:
            self._supervisor.cancel()
            with suppress(asyncio.CancelledError):
                await self._supervisor
        if self._channel is not None:
            await self.session.close_channel(self._channel)
        self._epoch += 1
        await self._drain()
