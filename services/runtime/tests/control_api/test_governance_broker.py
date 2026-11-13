"""Full synthetic HTTPS -> RabbitMQ -> durable approval -> SDK fixture -> completion path."""

import asyncio
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from markos_runtime.contracts.governance import ReviewedFixtureGrant
from markos_runtime.store.database import DurableStore
from markos_runtime.tools.transport import FixtureConnection, FixtureTransport
from markos_runtime.tools.worker import GovernedConversationWorker
from markos_runtime.transport.broker import BrokerSession
from markos_runtime.transport.consumer import Consumer
from markos_runtime.transport.publisher import Publisher
from tests.control_api.test_api import SCOPE
from tests.control_api.test_chat_transport import chat_rabbit, wait_for
from tests.control_api.test_conversation import conversation, request_value, rows
from tests.integration.conftest import OWNER
from tests.integration.test_rabbitmq import Harness, rabbit
from tests.tools_support import tool_server
from tests.web_support import WebBrowserClient, web_server

__all__ = ["conversation", "chat_rabbit", "rabbit"]


def test_governed_tool_uses_existing_durable_broker_path(
    conversation: dict[str, Any], chat_rabbit: Harness, tmp_path: Path
) -> None:
    async def scenario() -> None:
        async with (
            web_server(conversation, tmp_path, governance=True) as server,
            tool_server(tmp_path / "tool", UUID(OWNER), "personal") as remote,
            DurableStore.connect(conversation["chatpub"]) as pub,
            DurableStore.connect(conversation["chatworker"]) as work,
            DurableStore.connect(conversation["chatworker"]) as competing,
            DurableStore.connect(conversation["chatactivity"]) as history,
            BrokerSession.connect(chat_rabbit.dsn("chatpub")) as pub_broker,
            BrokerSession.connect(chat_rabbit.dsn("chatworker")) as work_broker,
            BrokerSession.connect(chat_rabbit.dsn("chatactivity")) as history_broker,
        ):
            user = WebBrowserClient(server["port"], server["cert"])
            await user.login(server["issuer"], server["credentials"]["both"])
            grant = ReviewedFixtureGrant(
                schema_version=1,
                owner_id=UUID(OWNER),
                domain="personal",
                grant_id=uuid4(),
                revision=1,
                previous_revision=0,
                endpoint=remote["endpoint"],
                enabled=True,
                reason="Synthetic broker fixture review",
            )
            assert (
                await user.call(
                    "POST", "/api/v1/tools/grant" + SCOPE, grant.model_dump(mode="json")
                )
            )[0] == 200
            _, page = await user.call("GET", "/api/v1/conversations" + SCOPE)
            conv = UUID(page["items"][0]["conversation"]["conversation_id"])
            request = {
                **request_value(conv),
                "fixture_arguments": {"schema_version": 1, "label": "Synthetic broker read"},
            }
            assert (await user.call("POST", "/api/v1/tool-requests" + SCOPE, request))[0] == 202
            consumer = Consumer(
                work_broker, work, {"control-gateway": chat_rabbit.users["chatpub"]}
            )
            activity = Consumer(
                history_broker, history, {"control-test-agent": chat_rabbit.users["chatworker"]}
            )
            try:
                await consumer.start()
                await activity.start()
                publisher = Publisher(
                    pub_broker, await pub_broker.channel(publisher=True), pub, chat_rabbit.auditor()
                )
                assert await publisher.publish_once() == 1
                await wait_for(
                    conversation,
                    (
                        "SELECT count(*) FROM markos_runtime.inbox WHERE "
                        "consumer_id='control-test-agent'"
                    ),
                    [(1,)],
                )
                transport = FixtureTransport(
                    FixtureConnection(
                        endpoint=remote["endpoint"],
                        owner_id=UUID(OWNER),
                        domain="personal",
                        ca_file=remote["cert"],
                        credential=remote["credential"],
                    ),
                    confirm_synthetic=True,
                )
                first = GovernedConversationWorker(
                    work, server["issuer"], transport=transport, confirm_synthetic=True
                )
                assert await first.run_once()
                assert rows(
                    conversation,
                    (
                        "SELECT count(*) FROM markos_runtime.execution_slots WHERE task_id IS"
                        " NOT NULL"
                    ),
                ) == [(0,)]
                queue = chat_rabbit.request(
                    "GET", "queues", chat_rabbit.vhost, "markos.personal.control-test-agent"
                )
                assert queue.get("messages_unacknowledged", 0) == 0
                approver = WebBrowserClient(server["port"], server["cert"])
                await approver.login(server["issuer"], server["credentials"]["approver"])
                _, page = await approver.call("GET", "/api/v1/approvals" + SCOPE)
                item = page["items"][0]
                decision = {
                    "schema_version": 1,
                    "intent_id": item["intent_id"],
                    "intent_sha256": item["intent_sha256"],
                    "expected_revision": item["revision"],
                    "decision": "approve",
                }
                assert (
                    await approver.call(
                        "POST",
                        "/api/v1/approvals/" + item["intent_id"] + "/decisions" + SCOPE,
                        decision,
                    )
                )[0] == 200
                second = GovernedConversationWorker(
                    competing, server["issuer"], transport=transport, confirm_synthetic=True
                )
                assert sorted(await asyncio.gather(first.run_once(), second.run_once())) == [
                    False,
                    True,
                ]
                completion = Publisher(
                    work_broker,
                    await work_broker.channel(publisher=True),
                    work,
                    chat_rabbit.auditor(),
                )
                assert await completion.publish_once() == 1
                await wait_for(
                    conversation,
                    (
                        "SELECT count(*) FROM markos_runtime.inbox WHERE "
                        "consumer_id='control-activity'"
                    ),
                    [(1,)],
                )
                assert remote["state"]["ledger"] == {"Synthetic broker read": 1}
                assert rows(
                    conversation,
                    ("SELECT count(*) FROM markos_control.messages WHERE author_kind='test-agent'"),
                ) == [(1,)]
            finally:
                await consumer.stop()
                await activity.stop()

    asyncio.run(scenario())
