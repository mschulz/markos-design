"""Real broker + PostgreSQL tests; only disposable local CI services are allowed."""

import asyncio
import base64
import json
import os
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from urllib.parse import quote
from urllib.request import Request, urlopen
from uuid import uuid4

import aio_pika
import psycopg
import pytest

from markos_runtime.store import DurableStore, StorageError
from markos_runtime.transport.broker import BrokerSession, TransportError
from markos_runtime.transport.consumer import Consumer
from markos_runtime.transport.management import TopologyAudit
from markos_runtime.transport.publisher import Publisher
from tests.integration.test_storage import count, event


class Harness:
    def __init__(self, base: str, user: str, password: str) -> None:
        self.base, self.user, self.password = base, user, password
        suffix = uuid4().hex[:12]
        self.vhost = "markos-test-" + suffix
        self.users = {
            key: key + "-" + suffix for key in ["publisher", "consumer", "audit", "observer"]
        }
        self.amqp_base = os.environ.get("MARKOS_TEST_AMQP_BASE", "amqp://127.0.0.1:5672")

    def request(self, method: str, *parts: str, data: object = None) -> Any:
        token = base64.b64encode(f"{self.user}:{self.password}".encode()).decode()
        req = Request(
            self.base + "/api/" + "/".join(quote(x, safe="") for x in parts),
            method=method,
            headers={"Authorization": "Basic " + token, "Content-Type": "application/json"},
            data=None if data is None else json.dumps(data).encode(),
        )
        with urlopen(req, timeout=10) as response:
            body = response.read()
        return json.loads(body) if body else None

    def dsn(self, role: str) -> str:
        return (
            self.amqp_base.replace("://", f"://{self.users[role]}:synthetic-test-only@")
            + "/"
            + self.vhost
        )

    def auditor(self) -> TopologyAudit:
        return TopologyAudit(self.base, self.users["observer"], "synthetic-test-only", self.vhost)

    def queue(self, name: str, limit: int = 10000) -> None:
        self.request(
            "PUT",
            "queues",
            self.vhost,
            name,
            data={
                "durable": True,
                "auto_delete": False,
                "arguments": {
                    "x-queue-type": "classic",
                    "x-max-length": limit,
                    "x-max-length-bytes": 33554432,
                    "x-overflow": "reject-publish",
                },
            },
        )

    def bind(self, exchange: str, queue: str, key: str = "v1.foundation.noop-requested") -> None:
        self.request(
            "POST",
            "bindings",
            self.vhost,
            "e",
            exchange,
            "q",
            queue,
            data={"routing_key": key, "arguments": {}},
        )

    def unbind(self, queue: str) -> None:
        source = "markos.personal.events"
        for b in self.request("GET", "bindings", self.vhost, "e", source, "q", queue):
            self.request(
                "DELETE", "bindings", self.vhost, "e", source, "q", queue, b["properties_key"]
            )

    def setup(self) -> None:
        self.request("PUT", "vhosts", self.vhost, data={})
        self.request("PUT", "vhost-limits", self.vhost, "max-queues", data={"value": 64})
        self.request("PUT", "vhost-limits", self.vhost, "max-connections", data={"value": 32})
        for role, username in self.users.items():
            self.request(
                "PUT",
                "users",
                username,
                data={
                    "password": "synthetic-test-only",
                    "tags": "management" if role == "observer" else "",
                },
            )
            write = (
                r"^markos\.personal\.(events|review)$"
                if role == "publisher"
                else r"^markos\.personal\.review$"
            )
            read = {
                "consumer": r"^markos\.personal\.foundation-worker$",
                "audit": r"^markos\.personal\.foundation-audit$",
            }.get(role, r"^$")
            self.request(
                "PUT",
                "permissions",
                self.vhost,
                username,
                data={
                    "configure": r"^$",
                    "write": r"^$" if role == "observer" else write,
                    "read": read,
                },
            )
        definitions = json.loads(
            (
                Path(__file__).resolve().parents[4]
                / "infrastructure/rabbitmq/definitions.example.json"
            ).read_text()
        )
        for descriptor in definitions["exchanges"]:
            self.request(
                "PUT",
                "exchanges",
                self.vhost,
                descriptor["name"],
                data={k: v for k, v in descriptor.items() if k not in {"name", "vhost"}},
            )
        self.request(
            "PUT",
            "exchanges",
            self.vhost,
            "markos.work.events",
            data={
                "type": "topic",
                "durable": True,
                "auto_delete": False,
                "internal": False,
                "arguments": {},
            },
        )
        for descriptor in definitions["queues"]:
            self.request(
                "PUT",
                "queues",
                self.vhost,
                descriptor["name"],
                data={k: v for k, v in descriptor.items() if k not in {"name", "vhost"}},
            )
        for binding in definitions["bindings"]:
            self.bind(binding["source"], binding["destination"], binding["routing_key"])

    def cleanup(self) -> None:
        self.request("DELETE", "vhosts", self.vhost)
        for username in self.users.values():
            self.request("DELETE", "users", username)


@pytest.fixture
def rabbit() -> Iterator[Harness]:
    base = os.environ.get("MARKOS_TEST_RABBIT_HTTP")
    if not base:
        if os.environ.get("MARKOS_REQUIRE_RABBIT_TESTS") == "1":
            pytest.fail("required broker tests lack endpoint")
        pytest.skip("set explicit disposable RabbitMQ test endpoints")
    if not base.startswith("http://127.0.0.1:"):
        pytest.fail("broker tests require disposable loopback HTTP service")
    harness = Harness(
        base,
        os.environ.get("MARKOS_TEST_RABBIT_USER", "m0c-admin"),
        os.environ.get("MARKOS_TEST_RABBIT_PASSWORD", "synthetic-test-only"),
    )
    try:
        harness.setup()
        yield harness
    finally:
        harness.cleanup()


async def until(check: Any, seconds: float = 15) -> None:
    async with asyncio.timeout(seconds):
        while not check():
            await asyncio.sleep(0.05)


def due(database: dict[str, str]) -> None:
    with psycopg.connect(database["admin"], autocommit=True) as admin:
        admin.execute(
            "UPDATE markos_runtime.outbox SET available_at=clock_timestamp()-interval '1s'"
        )


def test_confirm_independent_subscribers_and_commit_before_ack(
    database: dict[str, str], rabbit: Harness
) -> None:
    async def scenario() -> None:
        async with (
            DurableStore.connect(database["publisher"]) as store,
            BrokerSession.connect(rabbit.dsn("publisher")) as session,
        ):
            assert session.factory.writer is not None
            await rabbit.auditor().verify("personal", ("foundation-worker", "foundation-audit"))
            item = event()
            await store.append(item)
            publisher = Publisher(
                session, await session.channel(publisher=True), store, rabbit.auditor()
            )
            assert await publisher.publish_once() == 1
        for role in ["consumer", "audit"]:
            async with (
                DurableStore.connect(database[role]) as store,
                BrokerSession.connect(rabbit.dsn(role)) as session,
            ):
                consumer = Consumer(
                    session, store, {"foundation-generator": rabbit.users["publisher"]}
                )
                await consumer.start()
                try:
                    await until(
                        lambda role=role: (
                            count(database, "inbox") == (1 if role == "consumer" else 2)
                        )
                    )
                finally:
                    await consumer.stop()
        assert count(database, "tasks") == 2

    asyncio.run(scenario())


def test_missing_one_expected_binding_leaves_outbox_pending(
    database: dict[str, str], rabbit: Harness
) -> None:
    rabbit.unbind("markos.personal.foundation-audit")

    async def scenario() -> None:
        async with (
            DurableStore.connect(database["publisher"]) as store,
            BrokerSession.connect(rabbit.dsn("publisher")) as session,
        ):
            await store.append(event())
            publisher = Publisher(
                session, await session.channel(publisher=True), store, rabbit.auditor()
            )
            assert await publisher.publish_once() == 0
            assert await store.claim_outbox() == ()  # Persistent backoff, not immediate retry.
        with psycopg.connect(database["admin"]) as admin:
            assert admin.execute(
                "SELECT state,last_failure,attempts FROM markos_runtime.outbox"
            ).fetchone() == ("pending", "topology", 1)

    asyncio.run(scenario())


def test_zero_route_race_is_returned_not_confirmed(
    database: dict[str, str], rabbit: Harness
) -> None:
    class RacingAudit(TopologyAudit):
        async def verify(self, *args: Any, **kwargs: Any) -> None:
            await super().verify(*args, **kwargs)
            rabbit.unbind("markos.personal.foundation-worker")
            rabbit.unbind("markos.personal.foundation-audit")

    async def scenario() -> None:
        async with (
            DurableStore.connect(database["publisher"]) as store,
            BrokerSession.connect(rabbit.dsn("publisher")) as session,
        ):
            await store.append(event())
            audit = RacingAudit(
                rabbit.base, rabbit.users["observer"], "synthetic-test-only", rabbit.vhost
            )
            publisher = Publisher(session, await session.channel(publisher=True), store, audit)
            assert await publisher.publish_once() == 0
        with psycopg.connect(database["admin"]) as admin:
            assert admin.execute(
                "SELECT state,last_failure FROM markos_runtime.outbox"
            ).fetchone() == ("pending", "confirm")

    asyncio.run(scenario())


def test_partial_fanout_queue_full_nack_retains_original_id(
    database: dict[str, str], rabbit: Harness
) -> None:
    target = "markos.personal.foundation-worker"
    rabbit.request("DELETE", "queues", rabbit.vhost, target)
    rabbit.queue(target, 1)
    rabbit.bind("markos.personal.events", target)
    # Admin's explicit test-only HTTP get/publish is not a runtime permission.
    rabbit.request(
        "POST",
        "exchanges",
        rabbit.vhost,
        "amq.default",
        "publish",
        data={
            "routing_key": target,
            "properties": {"delivery_mode": 2},
            "payload": "fill",
            "payload_encoding": "string",
        },
    )

    async def scenario() -> None:
        async with (
            DurableStore.connect(database["publisher"]) as store,
            BrokerSession.connect(rabbit.dsn("publisher")) as session,
        ):
            item = event()
            await store.append(item)
            publisher = Publisher(
                session, await session.channel(publisher=True), store, rabbit.auditor()
            )
            assert await publisher.publish_once() == 0
            rabbit.request(
                "POST",
                "queues",
                rabbit.vhost,
                target,
                "get",
                data={"count": 1, "ackmode": "ack_requeue_false", "encoding": "auto"},
            )
            due(database)
            assert await publisher.publish_once() == 1
            with psycopg.connect(database["admin"]) as admin:
                assert admin.execute("SELECT event_id FROM markos_runtime.events").fetchone() == (
                    item.event_id,
                )
        for role in ["consumer", "audit"]:
            async with (
                DurableStore.connect(database[role]) as store,
                BrokerSession.connect(rabbit.dsn(role)) as session,
            ):
                consumer = Consumer(
                    session, store, {"foundation-generator": rabbit.users["publisher"]}
                )
                await consumer.start()
                try:
                    await until(
                        lambda role=role: (
                            count(database, "inbox") == (1 if role == "consumer" else 2)
                        )
                    )
                finally:
                    await consumer.stop()
        assert count(database, "tasks") == 2

    asyncio.run(scenario())


def test_unknown_confirm_after_acceptance_retries_without_duplicate_task(
    database: dict[str, str], rabbit: Harness
) -> None:
    async def scenario() -> None:
        async with (
            DurableStore.connect(database["publisher"]) as store,
            BrokerSession.connect(rabbit.dsn("publisher")) as session,
        ):
            await store.append(event())
            channel = await session.channel(publisher=True)
            send = session.send
            first = True

            async def lost(*args: Any, **kwargs: Any) -> None:
                nonlocal first
                await send(*args, **kwargs)
                if first:
                    first = False
                    raise TransportError("synthetic unknown result after broker acceptance")

            session.send = lost  # type: ignore[method-assign]
            publisher = Publisher(session, channel, store, rabbit.auditor())
            assert await publisher.publish_once() == 0
            due(database)
            assert await publisher.publish_once() == 1
        async with (
            DurableStore.connect(database["consumer"]) as store,
            BrokerSession.connect(rabbit.dsn("consumer")) as session,
        ):
            consumer = Consumer(session, store, {"foundation-generator": rabbit.users["publisher"]})
            await consumer.start()
            try:
                await until(lambda: count(database, "inbox") == 1)
            finally:
                await consumer.stop()
        assert count(database, "tasks") == 1

    asyncio.run(scenario())


def test_after_commit_before_ack_failure_closes_channel_and_redelivers(
    database: dict[str, str], rabbit: Harness
) -> None:
    class CrashOnce(Consumer):
        deliveries: list[bool] = []

        async def _handoff(self, message: Any) -> None:
            self.deliveries.append(message.redelivered)
            await super()._handoff(message)
            if len(self.deliveries) == 1:
                raise StorageError("synthetic crash after commit")

    async def scenario() -> None:
        async with (
            DurableStore.connect(database["publisher"]) as store,
            BrokerSession.connect(rabbit.dsn("publisher")) as session,
        ):
            await store.append(event())
            assert (
                await Publisher(
                    session, await session.channel(publisher=True), store, rabbit.auditor()
                ).publish_once()
                == 1
            )
        async with (
            DurableStore.connect(database["consumer"]) as store,
            BrokerSession.connect(rabbit.dsn("consumer")) as session,
        ):
            consumer = CrashOnce(
                session, store, {"foundation-generator": rabbit.users["publisher"]}
            )
            await consumer.start()
            try:
                await until(lambda: len(consumer.deliveries) >= 2)
            finally:
                await consumer.stop()
            assert consumer.deliveries[-1] is True
        assert count(database, "tasks") == count(database, "inbox") == 1

    asyncio.run(scenario())


def test_poison_delivery_review_is_durable_and_reference_confirmed(
    database: dict[str, str], rabbit: Harness
) -> None:
    # Validated publisher user identity; malformed body must never reach a task.
    async def scenario() -> None:
        async with BrokerSession.connect(rabbit.dsn("publisher")) as session:
            await session.send(
                await session.channel(publisher=True),
                "markos.personal.events",
                "v1.foundation.noop-requested",
                b"PRIVATE_SYNTHETIC_POISON",
                "invalid",
                "invalid",
            )
        async with (
            DurableStore.connect(database["consumer"]) as store,
            BrokerSession.connect(rabbit.dsn("consumer")) as consumer_session,
            BrokerSession.connect(rabbit.dsn("consumer")) as review_session,
        ):
            consumer = Consumer(
                consumer_session, store, {"foundation-generator": rabbit.users["publisher"]}
            )
            await consumer.start()
            try:
                await until(lambda: count(database, "delivery_reviews") == 1)
            finally:
                await consumer.stop()
            assert count(database, "tasks") == 0
            publisher = Publisher(
                review_session,
                await review_session.channel(publisher=True),
                store,
                rabbit.auditor(),
            )
            assert await publisher.publish_reviews_once() == 1
        records = rabbit.request(
            "POST",
            "queues",
            rabbit.vhost,
            "markos.personal.operator-review",
            "get",
            data={"count": 1, "ackmode": "ack_requeue_false", "encoding": "auto"},
        )
        assert len(records) == 1
        assert "PRIVATE_SYNTHETIC_POISON" not in records[0]["payload"]
        assert "review_id" in json.loads(records[0]["payload"])

    asyncio.run(scenario())


def test_broker_permissions_and_validated_user_id(rabbit: Harness) -> None:
    async def scenario() -> None:
        async with BrokerSession.connect(rabbit.dsn("publisher")) as session:
            channel = await session.channel(publisher=True)
            with pytest.raises(TransportError):
                await session.send(
                    channel, "markos.work.events", "v1.foundation.noop-requested", b"{}", "x", "x"
                )
        async with BrokerSession.connect(rabbit.dsn("publisher")) as session:
            channel = await session.channel(publisher=True)
            exchange = await channel.get_exchange("markos.personal.events", ensure=False)
            with pytest.raises(aio_pika.AMQPException):
                await exchange.publish(
                    aio_pika.Message(b"{}", user_id=rabbit.users["consumer"]),
                    routing_key="v1.foundation.noop-requested",
                    timeout=5,
                )
        async with BrokerSession.connect(rabbit.dsn("observer")) as session:
            channel = await session.channel(publisher=False)
            with pytest.raises(aio_pika.AMQPException):
                queue = await channel.declare_queue(
                    "markos.personal.foundation-worker", passive=True, robust=False
                )
                await queue.get(timeout=2)

    asyncio.run(scenario())


def test_competing_replicas_share_one_logical_consumer(
    database: dict[str, str], rabbit: Harness
) -> None:
    async def scenario() -> None:
        async with (
            DurableStore.connect(database["consumer"]) as left_store,
            DurableStore.connect(database["consumer"]) as right_store,
            BrokerSession.connect(rabbit.dsn("consumer")) as left_session,
            BrokerSession.connect(rabbit.dsn("consumer")) as right_session,
        ):
            left = Consumer(
                left_session, left_store, {"foundation-generator": rabbit.users["publisher"]}
            )
            right = Consumer(
                right_session, right_store, {"foundation-generator": rabbit.users["publisher"]}
            )
            await left.start()
            await right.start()
            try:
                async with (
                    DurableStore.connect(database["publisher"]) as store,
                    BrokerSession.connect(rabbit.dsn("publisher")) as session,
                ):
                    for _ in range(10):
                        await store.append(event())
                    assert (
                        await Publisher(
                            session, await session.channel(publisher=True), store, rabbit.auditor()
                        ).publish_once()
                        == 10
                    )
                await until(lambda: count(database, "inbox") == 10)
                assert count(database, "tasks") == 10
            finally:
                await left.stop()
                await right.stop()

    asyncio.run(scenario())


def test_connection_abort_recovers_one_subscription(
    database: dict[str, str], rabbit: Harness
) -> None:
    async def scenario() -> None:
        async with (
            DurableStore.connect(database["consumer"]) as store,
            BrokerSession.connect(rabbit.dsn("consumer")) as session,
        ):
            consumer = Consumer(session, store, {"foundation-generator": rabbit.users["publisher"]})
            await consumer.start()
            try:
                session.factory.abort()
                await asyncio.sleep(0.1)
                await asyncio.wait_for(consumer.ready.wait(), 15)
                async with (
                    DurableStore.connect(database["publisher"]) as publisher_store,
                    BrokerSession.connect(rabbit.dsn("publisher")) as publisher_session,
                ):
                    await publisher_store.append(event())
                    assert (
                        await Publisher(
                            publisher_session,
                            await publisher_session.channel(publisher=True),
                            publisher_store,
                            rabbit.auditor(),
                        ).publish_once()
                        == 1
                    )
                await until(lambda: count(database, "inbox") == 1)
                # Management consumer statistics are eventually refreshed after reconnect.
                await until(
                    lambda: (
                        len(
                            [
                                r
                                for r in rabbit.request("GET", "consumers", rabbit.vhost)
                                if r["queue"]["name"] == "markos.personal.foundation-worker"
                            ]
                        )
                        == 1
                    )
                )
            finally:
                await consumer.stop()

    asyncio.run(scenario())


def test_stalled_handoff_requeues_and_recovers(database: dict[str, str], rabbit: Harness) -> None:
    class StallOnce(Consumer):
        first = True

        async def _handoff(self, message: Any) -> None:
            if self.first:
                self.first = False
                await asyncio.sleep(10)
            await super()._handoff(message)

    async def scenario() -> None:
        async with (
            DurableStore.connect(database["publisher"]) as store,
            BrokerSession.connect(rabbit.dsn("publisher")) as session,
        ):
            await store.append(event())
            assert (
                await Publisher(
                    session, await session.channel(publisher=True), store, rabbit.auditor()
                ).publish_once()
                == 1
            )
        async with (
            DurableStore.connect(database["consumer"]) as store,
            BrokerSession.connect(rabbit.dsn("consumer")) as session,
        ):
            consumer = StallOnce(
                session,
                store,
                {"foundation-generator": rabbit.users["publisher"]},
                active_seconds=0.05,
                total_seconds=0.1,
            )
            await consumer.start()
            try:
                await until(lambda: count(database, "inbox") == 1)
            finally:
                await consumer.stop()
        assert count(database, "tasks") == 1

    asyncio.run(scenario())
