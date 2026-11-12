"""Real HTTPS -> PostgreSQL -> RabbitMQ -> competing workers -> durable reply."""

import asyncio
import json
import socket
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import pytest
import uvicorn  # type: ignore[import-not-found]
from aio_pika.abc import AbstractIncomingMessage

from markos_runtime.api.application import create_app
from markos_runtime.api.identity import FixtureIssuer, opaque
from markos_runtime.control.api_config import SyntheticConversationApiConfig
from markos_runtime.control.conversation import ConversationWorker
from markos_runtime.control.store import ControlStore, SessionContext
from markos_runtime.store.database import DurableStore
from markos_runtime.transport.broker import BrokerSession, TransportError
from markos_runtime.transport.consumer import Consumer
from markos_runtime.transport.publisher import Publisher
from tests.control_api.test_api import SCOPE, Client
from tests.control_api.test_conversation import (
    admit,
    conversation,
    handoff,
    message_path,
    mutate,
    rows,
    scenario_api,
)
from tests.control_api.test_https import HttpClient, certificate
from tests.integration.test_control_persistence import prepare, session
from tests.integration.test_rabbitmq import Harness, rabbit

__all__ = ["rabbit", "conversation"]


@pytest.fixture
def chat_rabbit(rabbit: Harness) -> Harness:
    path = (
        Path(__file__).resolve().parents[4]
        / "infrastructure/rabbitmq/definitions.control.example.json"
    )
    definitions = json.loads(path.read_text())
    for queue in definitions["queues"]:
        rabbit.queue(queue["name"])
    for binding in definitions["bindings"]:
        rabbit.bind(binding["source"], binding["destination"], binding["routing_key"])
    for role, read, write in [
        ("chatpub", r"^$", r"^v1\.control\.chat-requested$"),
        ("chatworker", r"^markos\.personal\.control-test-agent$", r"^v1\.control\.chat-completed$"),
        ("chatactivity", r"^markos\.personal\.control-activity$", r"^$"),
    ]:
        user = role + "-" + uuid4().hex[:12]
        rabbit.users[role] = user
        rabbit.request("PUT", "users", user, data={"password": "synthetic-test-only", "tags": ""})
        rabbit.request(
            "PUT",
            "permissions",
            rabbit.vhost,
            user,
            data={
                "configure": r"^$",
                "write": r"^markos\.personal\.events$" if role != "chatactivity" else r"^$",
                "read": read,
            },
        )
        rabbit.request(
            "PUT",
            "topic-permissions",
            rabbit.vhost,
            user,
            data={
                "exchange": "markos.personal.events",
                "configure": r"^$",
                "write": write,
                "read": r".*",
            },
        )
    return rabbit


async def wait_for(data: dict[str, Any], query: str, expected: list[Any]) -> None:
    async with asyncio.timeout(20):
        while rows(data, query) != expected:
            await asyncio.sleep(0.05)


def test_real_https_conversation_with_two_workers_and_completion_handoff(
    conversation: dict[str, Any], chat_rabbit: Harness, tmp_path: Path
) -> None:
    cert, key = certificate(tmp_path)

    async def scenario() -> None:
        listener = socket.socket()
        listener.bind(("127.0.0.1", 0))
        listener.listen(32)
        port = listener.getsockname()[1]
        issuer = FixtureIssuer()
        credential = opaque()
        issuer.add_account("both", credential)
        profile = SyntheticConversationApiConfig(
            schema_version=1,
            api_origin="https://127.0.0.1:" + str(port),
            conversation_profile="deterministic-conversation-v1",
        )
        async with (
            ControlStore.connect(conversation["gateway"]) as gateway,
            DurableStore.connect(conversation["chatpub"]) as pub,
            DurableStore.connect(conversation["chatworker"]) as worker_store,
            DurableStore.connect(conversation["chatworker"]) as competing_store,
            DurableStore.connect(conversation["chatactivity"]) as activity_store,
            BrokerSession.connect(chat_rabbit.dsn("chatpub")) as pub_broker,
            BrokerSession.connect(chat_rabbit.dsn("chatworker")) as worker_broker,
            BrokerSession.connect(chat_rabbit.dsn("chatactivity")) as activity_broker,
        ):
            await prepare(gateway, await session(gateway))
            app = create_app(
                profile, gateway, issuer, session_key=b"s" * 32, confirm_synthetic=True
            )
            server = uvicorn.Server(
                uvicorn.Config(
                    app,
                    ssl_certfile=str(cert),
                    ssl_keyfile=str(key),
                    proxy_headers=False,
                    access_log=False,
                    log_config=None,
                    lifespan="off",
                    limit_concurrency=16,
                    timeout_graceful_shutdown=2,
                )
            )
            server_task = asyncio.create_task(server.serve(sockets=[listener]))
            consumer = Consumer(
                worker_broker, worker_store, {"control-gateway": chat_rabbit.users["chatpub"]}
            )
            activity = Consumer(
                activity_broker,
                activity_store,
                {"control-test-agent": chat_rabbit.users["chatworker"]},
            )
            try:
                async with asyncio.timeout(5):
                    while not server.started:
                        await asyncio.sleep(0.01)
                await consumer.start()
                await activity.start()
                client = HttpClient(port, cert)
                await client.login(issuer, credential)
                conv = uuid4()
                status, _ = await client.call(
                    "POST",
                    "/api/v1/conversations" + SCOPE,
                    {
                        "schema_version": 1,
                        "domain": "personal",
                        "conversation_id": str(conv),
                        "operation_key": "conversation:http",
                    },
                )
                assert status == 201
                receipt = await admit(client, conv)
                assert await admit(client, conv) == receipt
                publisher = Publisher(
                    pub_broker, await pub_broker.channel(publisher=True), pub, chat_rabbit.auditor()
                )
                assert await publisher.publish_once() == 1
                await wait_for(
                    conversation,
                    "SELECT count(*) FROM markos_runtime.inbox WHERE "
                    "consumer_id='control-test-agent'",
                    [(1,)],
                )
                a, b = (
                    ConversationWorker(worker_store, issuer, confirm_synthetic=True),
                    ConversationWorker(competing_store, issuer, confirm_synthetic=True),
                )
                assert sorted(await asyncio.gather(a.run_once(), b.run_once())) == [False, True]
                completion = Publisher(
                    worker_broker,
                    await worker_broker.channel(publisher=True),
                    worker_store,
                    chat_rabbit.auditor(),
                )
                assert await completion.publish_once() == 1
                await wait_for(
                    conversation,
                    "SELECT count(*) FROM markos_runtime.inbox WHERE "
                    "consumer_id='control-activity'",
                    [(1,)],
                )
                status, page = await client.call("GET", message_path(conv) + SCOPE)
                assert status == 200 and [m["author_kind"] for m in page["items"]] == [
                    "human",
                    "test-agent",
                ]
                assert page["items"][1]["in_reply_to"] == receipt["message_id"]
                status_path = message_path(conv) + "/" + receipt["message_id"] + "/status" + SCOPE
                assert (await client.call("GET", status_path))[1]["state"] == "succeeded"
                assert rows(conversation, "SELECT count(*) FROM markos_runtime.task_results") == [
                    (2,)
                ]
                for name in ("foundation-worker", "foundation-audit"):
                    queue = chat_rabbit.request(
                        "GET", "queues", chat_rabbit.vhost, "markos.personal." + name
                    )
                    assert queue.get("messages", 0) == 0
            finally:
                await consumer.stop()
                await activity.stop()
                server.should_exit = True
                await asyncio.wait_for(server_task, 5)
                listener.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("fault", ["before-commit", "after-commit-before-ack"])
def test_chat_handoff_crash_redelivers_one_task(
    conversation: dict[str, Any], chat_rabbit: Harness, fault: str
) -> None:
    class LoseAck(Consumer):
        lost = False
        deliveries = 0

        async def _handoff(self, message: AbstractIncomingMessage) -> None:
            await super()._handoff(message)
            self.deliveries += 1
            if not self.lost:
                self.lost = True
                raise TransportError("synthetic lost acknowledgement")

    async def action(
        client: Client,
        issuer: FixtureIssuer,
        gateway: ControlStore,
        conv: UUID,
        ctx: SessionContext,
    ) -> None:
        await admit(client, conv)
        if fault == "before-commit":
            mutate(
                conversation,
                "CREATE FUNCTION markos_control.fail_inbox() RETURNS trigger "
                "LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'synthetic before "
                "commit';END $$",
            )
            mutate(
                conversation,
                "CREATE TRIGGER test_fault BEFORE INSERT ON markos_runtime.inbox "
                "FOR EACH ROW EXECUTE FUNCTION markos_control.fail_inbox()",
            )
        async with (
            DurableStore.connect(conversation["chatpub"]) as pub,
            DurableStore.connect(conversation["chatworker"]) as target,
            BrokerSession.connect(chat_rabbit.dsn("chatpub")) as broker,
            BrokerSession.connect(chat_rabbit.dsn("chatworker")) as receiver,
        ):
            consumer: Consumer = (LoseAck if fault == "after-commit-before-ack" else Consumer)(
                receiver, target, {"control-gateway": chat_rabbit.users["chatpub"]}
            )
            try:
                await consumer.start()
                publisher = Publisher(
                    broker, await broker.channel(publisher=True), pub, chat_rabbit.auditor()
                )
                assert await publisher.publish_once() == 1
                if fault == "before-commit":
                    async with asyncio.timeout(5):
                        await consumer._failed.wait()
                    assert rows(conversation, "SELECT count(*) FROM markos_runtime.inbox") == [(0,)]
                    assert rows(conversation, "SELECT count(*) FROM markos_runtime.tasks") == [(0,)]
                    mutate(conversation, "DROP TRIGGER test_fault ON markos_runtime.inbox")
                else:
                    assert isinstance(consumer, LoseAck)
                    async with asyncio.timeout(5):
                        while not consumer.lost:
                            await asyncio.sleep(0.01)
                if isinstance(consumer, LoseAck):
                    async with asyncio.timeout(10):
                        while consumer.deliveries < 2:
                            await asyncio.sleep(0.05)
                await wait_for(conversation, "SELECT count(*) FROM markos_runtime.inbox", [(1,)])
                async with asyncio.timeout(10):
                    while not consumer.ready.is_set():
                        await asyncio.sleep(0.05)
                assert await ConversationWorker(target, issuer, confirm_synthetic=True).run_once()
                assert rows(conversation, "SELECT count(*) FROM markos_runtime.tasks") == [(1,)]
                assert rows(
                    conversation,
                    "SELECT count(*) FROM markos_control.messages WHERE author_kind='test-agent'",
                ) == [(1,)]
            finally:
                await consumer.stop()

    asyncio.run(scenario_api(conversation, action))


def test_completion_topology_failure_preserves_reply_and_retries_same_event(
    conversation: dict[str, Any], chat_rabbit: Harness
) -> None:
    async def action(
        client: Client,
        issuer: FixtureIssuer,
        gateway: ControlStore,
        conv: UUID,
        ctx: SessionContext,
    ) -> None:
        receipt = await admit(client, conv)
        await handoff(conversation, receipt)
        async with (
            DurableStore.connect(conversation["chatworker"]) as worker_store,
            BrokerSession.connect(chat_rabbit.dsn("chatworker")) as broker,
        ):
            assert await ConversationWorker(worker_store, issuer, confirm_synthetic=True).run_once()
            original = rows(
                conversation, "SELECT completion_id,reply_id FROM markos_control.chat_runs"
            )
            chat_rabbit.unbind("markos.personal.control-activity")
            publisher = Publisher(
                broker, await broker.channel(publisher=True), worker_store, chat_rabbit.auditor()
            )
            assert await publisher.publish_once() == 0
            assert (
                rows(conversation, "SELECT completion_id,reply_id FROM markos_control.chat_runs")
                == original
            )
            chat_rabbit.bind(
                "markos.personal.events",
                "markos.personal.control-activity",
                "v1.control.chat-completed",
            )
            mutate(
                conversation,
                "UPDATE markos_runtime.outbox SET "
                "available_at=clock_timestamp()-interval '1 second' WHERE "
                "publisher_id='control-test-agent'",
            )
            assert await publisher.publish_once() == 1
            assert (
                rows(conversation, "SELECT completion_id,reply_id FROM markos_control.chat_runs")
                == original
            )

    asyncio.run(scenario_api(conversation, action))


def test_broker_denies_chat_publisher_route_expansion(chat_rabbit: Harness) -> None:
    async def scenario() -> None:
        async with BrokerSession.connect(chat_rabbit.dsn("chatpub")) as broker:
            with pytest.raises(TransportError):
                await broker.send(
                    await broker.channel(publisher=True),
                    "markos.personal.events",
                    "v1.control.chat-completed",
                    b"{}",
                    "bad",
                    "control.chat.completed",
                )

    asyncio.run(scenario())
