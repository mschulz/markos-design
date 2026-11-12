import asyncio
import json
from typing import Any, cast
from uuid import UUID

import pytest
from aio_pika.abc import AbstractIncomingMessage, AbstractRobustChannel, AbstractRobustConnection
from pamqp.commands import Basic

from markos_runtime.contracts import EventEnvelope
from markos_runtime.store.database import DurableStore, OutboxClaim, Receipt, StoreIdentity
from markos_runtime.transport.broker import (
    AbortableFactory,
    BrokerSession,
    TransportError,
    endpoint,
    positive_confirmation,
)
from markos_runtime.transport.consumer import Consumer
from markos_runtime.transport.management import TopologyAudit
from markos_runtime.transport.publisher import Publisher, backoff


@pytest.mark.parametrize("value", [None, False, True, Basic.Nack(), Basic.Reject(), object()])
def test_confirmation_requires_actual_ack(value: object) -> None:
    with pytest.raises(TransportError):
        positive_confirmation(value)
    positive_confirmation(Basic.Ack())


@pytest.mark.parametrize(
    "dsn",
    [
        "",
        "amqp://secret:private@remote.example/namespace",
        "amqp://localhost/",
        "amqps://u:p@remote.example/test?no_verify_ssl=1",
        "amqps://u:p@remote.example/test#private",
    ],
)
def test_endpoint_is_explicit_and_errors_redacted(dsn: str) -> None:
    with pytest.raises(TransportError) as error:
        endpoint(dsn)
    assert "private" not in str(error.value)
    assert endpoint("amqp://u:p@127.0.0.1/markos-test").host == "127.0.0.1"
    assert endpoint("amqps://u:p@broker.example/markos-test").scheme == "amqps"


def test_retry_backoff_is_bounded() -> None:
    assert all(1 <= backoff(n) <= 300 for n in [-1, 0, 1, 5, 50, 100000])


class FakeStore:
    def __init__(self) -> None:
        self.trace: list[str] = []
        self.claims: tuple[OutboxClaim, ...] = ()
        self.delay = 0.0
        self.fail = False

    async def handoff(self, event: EventEnvelope, *, wire: bytes | None = None) -> Receipt:
        self.trace.append("transaction")
        if self.fail:
            from markos_runtime.store import StorageError

            raise StorageError("storage operation failed")
        await asyncio.sleep(self.delay)
        self.trace.append("commit")
        return Receipt("created", UUID(int=1))

    async def review_delivery(self, body: bytes, reason: str) -> UUID:
        self.trace.append("review-commit:" + reason)
        return UUID(int=2)

    async def claim_outbox(self, **kwargs: object) -> tuple[OutboxClaim, ...]:
        return self.claims

    async def authorize_publication(self, eid: UUID, fence: int, *, epoch: UUID | None) -> bool:
        return True

    async def record_publication(self, eid: UUID, fence: int, *, epoch: UUID | None) -> bool:
        self.trace.append("publication-commit")
        return True

    async def retry_claim(self, claim: OutboxClaim, delay: int, code: str) -> bool:
        self.trace.append("retry:" + code)
        return True


class FakeMessage:
    def __init__(self, event: EventEnvelope, trace: list[str]) -> None:
        self.body = event.to_wire()
        self.message_id = str(event.event_id)
        self.content_type = "application/json"
        self.type = event.event_type
        self.user_id = "publisher-login"
        self.trace = trace

    async def ack(self) -> None:
        self.trace.append("ack")


def consumer(store: FakeStore) -> Consumer:
    result = Consumer(
        cast(BrokerSession, object()),
        cast(DurableStore, store),
        {"foundation-generator": "publisher-login"},
        active_seconds=0.03,
        total_seconds=0.06,
    )
    result.identity = StoreIdentity(
        "foundation-worker", UUID("11111111-1111-4111-8111-111111111111"), "personal", False, True
    )
    return result


def test_ack_follows_commit(event_document: dict[str, Any]) -> None:
    async def scenario() -> None:
        store = FakeStore()
        event = EventEnvelope.from_wire(json.dumps(event_document).encode())
        message = FakeMessage(event, store.trace)
        await consumer(store)._callback(cast(AbstractIncomingMessage, message))
        assert store.trace == ["transaction", "commit", "ack"]

    asyncio.run(scenario())


@pytest.mark.parametrize("failure", ["database", "active-timeout", "semaphore-wait"])
def test_failure_never_acks_and_requests_channel_recovery(
    event_document: dict[str, Any], failure: str
) -> None:
    async def scenario() -> None:
        store = FakeStore()
        instance = consumer(store)
        if failure == "database":
            store.fail = True
        elif failure == "active-timeout":
            store.delay = 1
        else:
            await instance._handoffs.acquire()
            await instance._handoffs.acquire()
        event = EventEnvelope.from_wire(json.dumps(event_document).encode())
        message = FakeMessage(event, store.trace)
        await instance._callback(cast(AbstractIncomingMessage, message))
        assert "ack" not in store.trace and instance._failed.is_set()

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "mutation", ["bad-json", "spoof-user", "missing-user", "wrong-id", "wrong-domain", "oversize"]
)
def test_invalid_delivery_review_commits_before_ack(
    event_document: dict[str, Any], mutation: str
) -> None:
    async def scenario() -> None:
        store = FakeStore()
        event = EventEnvelope.from_wire(json.dumps(event_document).encode())
        message = FakeMessage(event, store.trace)
        if mutation == "bad-json":
            message.body = b"private invalid input"
        elif mutation == "spoof-user":
            message.user_id = "wrong-login"
        elif mutation == "missing-user":
            message.user_id = ""
        elif mutation == "wrong-id":
            message.message_id = "wrong"
        elif mutation == "wrong-domain":
            event_document["domain"] = "work"
            message.body = json.dumps(event_document).encode()
        else:
            message.body = b"x" * 65537
        await consumer(store)._callback(cast(AbstractIncomingMessage, message))
        assert len(store.trace) == 2 and store.trace[0].startswith("review-commit:")
        assert store.trace[1] == "ack"

    asyncio.run(scenario())


class FakeSession:
    vhost = "markos-test"

    def __init__(self, store: FakeStore, fail: bool = False) -> None:
        self.store, self.fail = store, fail

    async def send(self, *args: object) -> None:
        self.store.trace.append("send")
        if self.fail:
            raise TransportError("unknown confirm")
        self.store.trace.append("confirmed")


class FakeAudit:
    vhost = "markos-test"

    def __init__(self, fail: bool = False) -> None:
        self.fail = fail

    async def verify(self, *args: object, **kwargs: object) -> None:
        if self.fail:
            raise TransportError("missing binding")


@pytest.mark.parametrize("failure", ["none", "confirm", "topology"])
def test_outbox_only_advances_after_confirm(event_document: dict[str, Any], failure: str) -> None:
    async def scenario() -> None:
        store = FakeStore()
        event = EventEnvelope.from_wire(json.dumps(event_document).encode())
        store.claims = (OutboxClaim(event.event_id, event, 1, 0, ("foundation-worker",)),)
        publisher = Publisher(
            cast(BrokerSession, FakeSession(store, failure == "confirm")),
            cast(AbstractRobustChannel, object()),
            cast(DurableStore, store),
            cast(TopologyAudit, FakeAudit(failure == "topology")),
        )
        assert await publisher.publish_once() == (1 if failure == "none" else 0)
        if failure == "none":
            assert store.trace == ["send", "confirmed", "publication-commit"]
        else:
            assert "publication-commit" not in store.trace
            assert store.trace[-1] == "retry:" + failure

    asyncio.run(scenario())


def test_channel_close_timeout_aborts_socket() -> None:
    class Factory(AbortableFactory):
        def __init__(self) -> None:
            self.aborted = False

        def abort(self) -> None:
            self.aborted = True

    class Hung:
        async def close(self) -> None:
            await asyncio.sleep(1)

    async def scenario() -> None:
        factory = Factory()
        session = BrokerSession(
            cast(AbstractRobustConnection, Hung()),
            factory,
            endpoint("amqp://u:p@localhost/markos-test"),
        )
        await session.close_channel(cast(AbstractRobustChannel, Hung()), seconds=0.01)
        assert factory.aborted

    asyncio.run(scenario())


@pytest.mark.parametrize("bad", ["ttl", "quorum", "drop-head", "unlimited", "auto-delete"])
def test_topology_safety_profile_rejects_unsafe_queue(bad: str) -> None:
    descriptor: dict[str, Any] = {
        "durable": True,
        "auto_delete": False,
        "type": "classic",
        "arguments": {
            "x-max-length": 10000,
            "x-max-length-bytes": 33554432,
            "x-overflow": "reject-publish",
        },
    }
    TopologyAudit.validate_queue(descriptor)
    if bad == "ttl":
        descriptor["arguments"]["x-message-ttl"] = 1
    elif bad == "quorum":
        descriptor["type"] = "quorum"
    elif bad == "drop-head":
        descriptor["arguments"]["x-overflow"] = "drop-head"
    elif bad == "auto-delete":
        descriptor["auto_delete"] = True
    else:
        descriptor["arguments"].pop("x-max-length")
    with pytest.raises(TransportError):
        TopologyAudit.validate_queue(descriptor)


def test_watchdog_signals_even_if_handoff_delays_cancellation(
    event_document: dict[str, Any],
) -> None:
    class ResistantStore(FakeStore):
        def __init__(self) -> None:
            super().__init__()
            self.release = asyncio.Event()

        async def handoff(self, event: EventEnvelope, *, wire: bytes | None = None) -> Receipt:
            try:
                await self.release.wait()
            except asyncio.CancelledError:
                await self.release.wait()
            self.trace.append("late-commit")
            return Receipt("created", UUID(int=1))

    async def scenario() -> None:
        store = ResistantStore()
        instance = Consumer(
            cast(BrokerSession, object()),
            cast(DurableStore, store),
            {"foundation-generator": "publisher-login"},
            active_seconds=0.02,
            total_seconds=0.5,
        )
        instance.identity = StoreIdentity(
            "foundation-worker",
            UUID("11111111-1111-4111-8111-111111111111"),
            "personal",
            False,
            True,
        )
        message = FakeMessage(
            EventEnvelope.from_wire(json.dumps(event_document).encode()), store.trace
        )
        task = asyncio.create_task(instance._callback(cast(AbstractIncomingMessage, message)))
        await asyncio.sleep(0.05)
        assert instance._failed.is_set() and not task.done()
        store.release.set()
        await task
        assert "ack" not in store.trace

    asyncio.run(scenario())
