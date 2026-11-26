"""M1-D actual PostgreSQL acceptance with an explicit synthetic issuer; no real data."""

import asyncio
import hashlib
import json
from collections.abc import Awaitable, Callable
from typing import Any
from uuid import UUID, uuid4

import psycopg
import pytest
from psycopg import sql
from psycopg.conninfo import make_conninfo

from markos_runtime.api.application import create_app
from markos_runtime.api.identity import FixtureIssuer, SecretCodec, opaque
from markos_runtime.contracts.control import ChatRequest
from markos_runtime.contracts.wire import parse_event
from markos_runtime.control.api_config import SyntheticConversationApiConfig
from markos_runtime.control.conversation import ConversationWorker
from markos_runtime.control.store import ControlStore, SessionContext
from markos_runtime.store.database import DurableStore, StorageError
from tests.control_api.test_api import ORIGIN, SCOPE, Client
from tests.integration.conftest import OTHER, OWNER
from tests.integration.test_control_persistence import prepare, session


@pytest.fixture
def conversation(control: dict[str, Any]) -> dict[str, Any]:
    result = dict(control)
    for prefix, owner, domain in [
        ("", OWNER, "personal"),
        ("other_", OTHER, "personal"),
        ("research_", OWNER, "research"),
    ]:
        for role, service, publish, consume, execute in [
            ("chatpub", "control-gateway", True, False, False),
            ("chatworker", "control-test-agent", True, True, True),
            ("chatactivity", "control-activity", False, True, False),
        ]:
            name = "m1d_" + prefix + role
            with psycopg.connect(control["admin"], autocommit=True) as admin:
                if not admin.execute("SELECT 1 FROM pg_roles WHERE rolname=%s", (name,)).fetchone():
                    admin.execute(
                        sql.SQL(
                            "CREATE ROLE {} LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE "
                            "NOBYPASSRLS PASSWORD 'synthetic-test-only'"
                        ).format(sql.Identifier(name))
                    )
                admin.execute(
                    sql.SQL("GRANT markos_runtime_client TO {}").format(sql.Identifier(name))
                )
            with psycopg.connect(control["migration"], autocommit=True) as conn:
                conn.execute(
                    "INSERT INTO markos_runtime.service_identities "
                    "(login_role,service_id,owner_id,domain,can_publish,can_consume,"
                    "can_execute,can_replay) "
                    "VALUES(%s,%s,%s,%s,%s,%s,%s,%s)",
                    (name, service, owner, domain, publish, consume, execute, publish),
                )
            result[prefix + role] = make_conninfo(
                control["gateway"], user=name, password="synthetic-test-only"
            )
    return result


async def scenario_api(
    data: dict[str, Any],
    action: Callable[[Client, FixtureIssuer, ControlStore, UUID, SessionContext], Awaitable[None]],
) -> None:
    issuer = FixtureIssuer()
    credential = opaque()
    issuer.add_account("both", credential)
    profile = SyntheticConversationApiConfig(
        schema_version=1, api_origin=ORIGIN, conversation_profile="deterministic-conversation-v1"
    )
    async with ControlStore.connect(data["gateway"]) as store:
        conversation_id = await prepare(store, await session(store))
        client = Client(
            create_app(profile, store, issuer, session_key=b"s" * 32, confirm_synthetic=True)
        )
        raw = await client.login(issuer, credential)
        sid, secret = raw.split(".")
        ctx = SessionContext(UUID(sid), SecretCodec(b"s" * 32).lookup("session", secret))
        await action(client, issuer, store, conversation_id, ctx)


def message_path(conversation_id: UUID) -> str:
    return "/api/v1/conversations/" + str(conversation_id) + "/messages"


def request_value(conversation_id: UUID, key: str = "message:one") -> dict[str, Any]:
    return {
        "schema_version": 1,
        "domain": "personal",
        "conversation_id": str(conversation_id),
        "operation_key": key,
        "agent_id": "control-test-agent",
        "expected_agent_revision": 2,
        "text": "Synthetic hello",
    }


async def admit(client: Client, conversation_id: UUID, key: str = "message:one") -> dict[str, Any]:
    status, result = await client.call(
        "POST", message_path(conversation_id) + SCOPE, request_value(conversation_id, key)
    )
    assert status == 202, result
    return result  # type: ignore[no-any-return]


async def handoff(data: dict[str, Any], receipt: dict[str, Any]) -> None:
    async with DurableStore.connect(data["chatpub"]) as publisher:
        claims = await publisher.claim_outbox(limit=1)
        claim = next(c for c in claims if str(c.event_id) == receipt["event_id"])
        assert claim.wire is not None and b"Synthetic hello" not in claim.wire
        async with DurableStore.connect(data["chatworker"]) as target:
            outcome = await target.handoff(claim.event, wire=claim.wire)
            assert outcome.status in {"created", "duplicate"}
        assert await publisher.record_publication(claim.event_id, claim.fence, epoch=claim.epoch)


def rows(data: dict[str, Any], query: str, values: tuple[object, ...] = ()) -> list[Any]:
    with psycopg.connect(data["migration"]) as conn:
        return list(conn.execute(query, values).fetchall())


def mutate(data: dict[str, Any], query: str, values: tuple[object, ...] = ()) -> None:
    with psycopg.connect(data["migration"], autocommit=True) as conn:
        conn.execute(query, values)


def test_atomic_admission_idempotency_and_exact_reply(conversation: dict[str, Any]) -> None:
    async def action(
        client: Client, issuer: FixtureIssuer, store: ControlStore, conv: UUID, ctx: SessionContext
    ) -> None:
        first = await admit(client, conv)
        assert await admit(client, conv) == first
        assert (
            await client.call(
                "POST", message_path(conv) + SCOPE, {**request_value(conv), "text": "changed"}
            )
        )[0] == 409
        assert rows(conversation, "SELECT count(*) FROM markos_control.messages") == [(1,)]
        assert rows(conversation, "SELECT consumer_id FROM markos_runtime.event_consumers") == [
            ("control-test-agent",)
        ]
        await handoff(conversation, first)
        async with (
            DurableStore.connect(conversation["chatworker"]) as one,
            DurableStore.connect(conversation["chatworker"]) as two,
        ):
            a, b = (
                ConversationWorker(one, issuer, confirm_synthetic=True),
                ConversationWorker(two, issuer, confirm_synthetic=True),
            )
            outcomes = await asyncio.gather(a.run_once(), b.run_once())
            assert sorted(outcomes) == [False, True]
            assert not await a.run_once()
        status, page = await client.call("GET", message_path(conv) + SCOPE)
        assert status == 200 and [m["sequence"] for m in page["items"]] == [1, 2]
        reply = page["items"][1]
        assert (
            reply["body"]
            == "Synthetic acknowledgement: " + hashlib.sha256(b"Synthetic hello").hexdigest()
        )
        assert reply["author_kind"] == "test-agent" and reply["in_reply_to"] == first["message_id"]
        assert rows(conversation, "SELECT kind FROM markos_control.activity ORDER BY sequence") == [
            ("queued",),
            ("working",),
            ("succeeded",),
        ]
        assert rows(conversation, "SELECT count(*) FROM markos_runtime.events") == [(2,)]
        assert rows(conversation, "SELECT count(*) FROM markos_runtime.outbox") == [(2,)]
        async with DurableStore.connect(conversation["chatworker"]) as completion_publisher:
            claim = (await completion_publisher.claim_outbox())[0]
            event = parse_event(claim.wire or b"")
            assert event.event_type == "control.chat.completed"
            assert (
                str(event.causation_id)
                == first["event_id"]
                == str(event.correlation_id)
                == str(event.trace_id)
            )
            async with DurableStore.connect(conversation["chatactivity"]) as activity:
                assert (await activity.handoff(event, wire=claim.wire)).status == "created"
                assert (await activity.handoff(event, wire=claim.wire)).status == "duplicate"
        assert rows(
            conversation,
            "SELECT state FROM markos_runtime.tasks WHERE consumer_id='control-activity'",
        ) == [("completed",)]
        assert rows(conversation, "SELECT count(*) FROM markos_control.messages") == [(2,)]

    asyncio.run(scenario_api(conversation, action))


@pytest.mark.parametrize(
    "table",
    ["chat_runs", "outbox", "event_consumers", "events", "activity", "audit", "idempotency"],
)
def test_admission_rollback_includes_every_durable_record(
    conversation: dict[str, Any], table: str
) -> None:
    async def action(
        client: Client, issuer: FixtureIssuer, store: ControlStore, conv: UUID, ctx: SessionContext
    ) -> None:
        schema = (
            "markos_runtime"
            if table in {"outbox", "event_consumers", "events"}
            else "markos_control"
        )
        mutate(
            conversation,
            "CREATE FUNCTION markos_control.fail_insert() RETURNS trigger "
            "LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'synthetic "
            "fault';END $$",
        )
        mutate(
            conversation,
            f"CREATE TRIGGER test_fault BEFORE INSERT ON {schema}.{table} "
            "FOR EACH ROW EXECUTE FUNCTION markos_control.fail_insert()",
        )
        status, _ = await client.call("POST", message_path(conv) + SCOPE, request_value(conv))
        assert status == 503
        assert rows(conversation, "SELECT count(*) FROM markos_control.messages") == [(0,)]
        assert rows(conversation, "SELECT count(*) FROM markos_control.chat_runs") == [(0,)]
        assert rows(conversation, "SELECT count(*) FROM markos_runtime.events") == [(0,)]
        assert rows(
            conversation,
            "SELECT last_sequence FROM markos_control.conversations WHERE conversation_id=%s",
            (conv,),
        ) == [(0,)]
        mutate(conversation, f"DROP TRIGGER test_fault ON {schema}.{table}")
        assert (await admit(client, conv))["sequence"] == 1

    asyncio.run(scenario_api(conversation, action))


@pytest.mark.parametrize(
    "change",
    ["membership", "disabled-principal", "session", "provider", "agent", "config", "restore"],
)
def test_current_authority_blocks_reply(conversation: dict[str, Any], change: str) -> None:
    async def action(
        client: Client, issuer: FixtureIssuer, store: ControlStore, conv: UUID, ctx: SessionContext
    ) -> None:
        first = await admit(client, conv)
        await handoff(conversation, first)
        async with DurableStore.connect(conversation["chatworker"]) as target:
            worker = ConversationWorker(target, issuer, confirm_synthetic=True)
            claim = await worker.claim()
            assert claim
            if change == "membership":
                mutate(
                    conversation,
                    "DELETE FROM markos_control.memberships WHERE principal_id=%s AND "
                    "domain='personal'",
                    (conversation["principals"]["both"],),
                )
            elif change == "disabled-principal":
                mutate(
                    conversation,
                    "UPDATE markos_control.principals SET disabled=true WHERE subject='both'",
                )
            elif change == "session":
                await store.revoke_session(ctx)
            elif change == "provider":
                issuer.revoke(claim.provider_session_id)
            elif change == "agent":
                mutate(
                    conversation,
                    "UPDATE markos_control.agents SET enabled=false WHERE domain='personal'",
                )
            elif change == "config":
                mutate(
                    conversation,
                    "UPDATE markos_control.configurations SET revision=revision+1 "
                    "WHERE domain='personal'",
                )
            else:
                mutate(conversation, "SELECT markos_runtime.fence_restored_state()")
            if change == "provider":
                assert not await worker._current(claim)
                assert not await worker.finish(claim, None, failure="authority")
            else:
                assert not await worker.finish(
                    claim, "Synthetic acknowledgement: " + claim.message_sha256
                )
        assert rows(
            conversation,
            "SELECT count(*) FROM markos_control.messages WHERE author_kind='test-agent'",
        ) == [(0,)]
        assert rows(conversation, "SELECT count(*) FROM markos_runtime.task_results") == [(0,)]

    asyncio.run(scenario_api(conversation, action))


def test_lease_takeover_and_late_result_preserve_one_reply(conversation: dict[str, Any]) -> None:
    async def action(
        client: Client, issuer: FixtureIssuer, store: ControlStore, conv: UUID, ctx: SessionContext
    ) -> None:
        first = await admit(client, conv)
        await handoff(conversation, first)
        async with (
            DurableStore.connect(conversation["chatworker"]) as one,
            DurableStore.connect(conversation["chatworker"]) as two,
        ):
            a, b = (
                ConversationWorker(one, issuer, confirm_synthetic=True),
                ConversationWorker(two, issuer, confirm_synthetic=True),
            )
            old = await a.claim()
            assert old
            mutate(
                conversation,
                "UPDATE markos_runtime.tasks SET "
                "lease_until=clock_timestamp()-interval '1 second' WHERE "
                "task_id=%s",
                (old.task_id,),
            )
            mutate(
                conversation,
                "UPDATE markos_runtime.execution_slots SET "
                "lease_until=clock_timestamp()-interval '1 second' WHERE "
                "task_id=%s",
                (old.task_id,),
            )
            new = await b.claim()
            assert new and new.fence > old.fence
            reply = "Synthetic acknowledgement: " + new.message_sha256
            assert not await a.finish(old, reply)
            assert await b.finish(new, reply)
            assert not await b.finish(new, reply)
        assert rows(
            conversation,
            "SELECT count(*) FROM markos_control.messages WHERE author_kind='test-agent'",
        ) == [(1,)]
        assert rows(conversation, "SELECT count(*) FROM markos_runtime.events") == [(2,)]

    asyncio.run(scenario_api(conversation, action))


@pytest.mark.parametrize("running", [False, True])
def test_cancel_is_durable_idempotent_and_fences_completion(
    conversation: dict[str, Any], running: bool
) -> None:
    async def action(
        client: Client, issuer: FixtureIssuer, store: ControlStore, conv: UUID, ctx: SessionContext
    ) -> None:
        first = await admit(client, conv)
        async with DurableStore.connect(conversation["chatworker"]) as target:
            worker = ConversationWorker(target, issuer, confirm_synthetic=True)
            claim = None
            if running:
                await handoff(conversation, first)
                claim = await worker.claim()
                assert claim
            path = message_path(conv) + "/" + first["message_id"] + "/cancel" + SCOPE
            for _ in range(2):
                status, result = await client.call("POST", path, {"schema_version": 1})
                assert status == 200 and result["state"] == "cancelled"
            if claim:
                assert not await worker.finish(
                    claim, "Synthetic acknowledgement: " + claim.message_sha256
                )
            assert not await worker.run_once()
        assert rows(
            conversation,
            "SELECT count(*) FROM markos_control.messages WHERE author_kind='test-agent'",
        ) == [(0,)]
        assert rows(
            conversation, "SELECT kind FROM markos_control.activity WHERE kind='failed'"
        ) == [("failed",)]

    asyncio.run(scenario_api(conversation, action))


@pytest.mark.parametrize(
    "table", ["messages", "events", "outbox", "activity", "task_results", "chat_runs"]
)
def test_reply_completion_transaction_rollback(conversation: dict[str, Any], table: str) -> None:
    async def action(
        client: Client, issuer: FixtureIssuer, store: ControlStore, conv: UUID, ctx: SessionContext
    ) -> None:
        receipt = await admit(client, conv)
        await handoff(conversation, receipt)
        async with DurableStore.connect(conversation["chatworker"]) as target:
            worker = ConversationWorker(target, issuer, confirm_synthetic=True)
            claim = await worker.claim()
            assert claim
            schema = (
                "markos_runtime"
                if table in {"events", "outbox", "task_results"}
                else "markos_control"
            )
            operation = "UPDATE" if table == "chat_runs" else "INSERT"
            mutate(
                conversation,
                "CREATE FUNCTION markos_control.fail_result() RETURNS trigger "
                "LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'synthetic "
                "completion fault';END $$",
            )
            mutate(
                conversation,
                f"CREATE TRIGGER fail_result BEFORE {operation} ON {schema}.{table} "
                "FOR EACH ROW EXECUTE FUNCTION markos_control.fail_result()",
            )
            reply = "Synthetic acknowledgement: " + claim.message_sha256
            with pytest.raises(StorageError):
                await worker.finish(claim, reply)
            assert rows(
                conversation,
                "SELECT count(*) FROM markos_control.messages WHERE author_kind='test-agent'",
            ) == [(0,)]
            assert rows(conversation, "SELECT count(*) FROM markos_runtime.events") == [(1,)]
            assert rows(conversation, "SELECT count(*) FROM markos_runtime.task_results") == [(0,)]
            assert rows(conversation, "SELECT state FROM markos_control.chat_runs") == [
                ("working",)
            ]
            mutate(conversation, f"DROP TRIGGER fail_result ON {schema}.{table}")
            assert await worker.finish(claim, reply)

    asyncio.run(scenario_api(conversation, action))


def test_lost_admission_and_completion_responses_do_not_repeat_work(
    conversation: dict[str, Any],
) -> None:
    class LoseResult(ConversationWorker):
        lost = False

        async def finish(
            self, claim: Any, reply: str | None, *, failure: str | None = None
        ) -> bool:
            result = await super().finish(claim, reply, failure=failure)
            if result and not self.lost:
                self.lost = True
                raise StorageError("synthetic response lost after commit")
            return result

    async def action(
        client: Client, issuer: FixtureIssuer, store: ControlStore, conv: UUID, ctx: SessionContext
    ) -> None:
        request = ChatRequest.from_wire(json.dumps(request_value(conv)).encode())
        original = await store.admit_message(ctx, UUID(OWNER), request)
        # Client retries without having received the first committed admission receipt.
        assert (await admit(client, conv))["event_id"] == str(original.event_id)
        receipt = await admit(client, conv)
        await handoff(conversation, receipt)
        async with DurableStore.connect(conversation["chatworker"]) as target:
            worker = LoseResult(target, issuer, confirm_synthetic=True)
            assert not await worker.run_once()
            assert not await worker.run_once()
        assert rows(conversation, "SELECT state FROM markos_control.chat_runs") == [("succeeded",)]
        assert rows(
            conversation,
            "SELECT count(*) FROM markos_control.messages WHERE author_kind='test-agent'",
        ) == [(1,)]
        assert await admit(client, conv) == receipt

    asyncio.run(scenario_api(conversation, action))


def test_pending_budget_is_shared_and_terminal_result_frees_capacity(
    conversation: dict[str, Any],
) -> None:
    from markos_runtime.control.store import ControlLimited

    async def action(
        client: Client, issuer: FixtureIssuer, store: ControlStore, conv: UUID, ctx: SessionContext
    ) -> None:
        async def submit(index: int) -> Any:
            request = ChatRequest.from_wire(
                json.dumps(request_value(conv, "message:" + str(index))).encode()
            )
            return await store.admit_message(ctx, UUID(OWNER), request)

        for index in range(9):
            await submit(index)
        results = await asyncio.gather(submit(9), submit(10), return_exceptions=True)
        assert sum(isinstance(value, ControlLimited) for value in results) == 1
        assert rows(conversation, "SELECT count(*) FROM markos_control.chat_runs") == [(10,)]
        first = rows(
            conversation, "SELECT receipt FROM markos_control.chat_runs ORDER BY created_at LIMIT 1"
        )[0][0]
        await handoff(conversation, first)
        async with DurableStore.connect(conversation["chatworker"]) as target:
            assert await ConversationWorker(target, issuer, confirm_synthetic=True).run_once()
        await submit(11)
        assert rows(conversation, "SELECT count(*) FROM markos_control.message_intents") == [(10,)]
        assert rows(conversation, "SELECT count(*) FROM markos_control.chat_runs") == [(11,)]

    asyncio.run(scenario_api(conversation, action))


@pytest.mark.parametrize(
    "fault",
    [
        "owner",
        "domain",
        "cursor",
        "cursor-resource",
        "path",
        "forged-actor",
        "config-version",
        "missing-object",
        "cancel-other-actor",
    ],
)
def test_conversation_http_scope_and_input_boundaries(
    conversation: dict[str, Any], fault: str
) -> None:
    async def action(
        client: Client, issuer: FixtureIssuer, store: ControlStore, conv: UUID, ctx: SessionContext
    ) -> None:
        first = await admit(client, conv)
        path = message_path(conv) + SCOPE
        method, value, expected = "GET", None, 403
        if fault == "owner":
            path = path.replace(OWNER, OTHER)
        elif fault == "domain":
            path = path.replace("personal", "work")
        elif fault == "cursor":
            path += "&cursor=forged"
            expected = 400
        elif fault == "cursor-resource":
            cursor = (await client.call("GET", path))[1]["next_cursor"]
            path = "/api/v1/conversations" + SCOPE + "&cursor=" + cursor
            expected = 400
        elif fault == "path":
            method, value, expected = "POST", request_value(uuid4()), 400
        elif fault == "forged-actor":
            method, value, expected = (
                "POST",
                {**request_value(conv), "actor_id": str(conversation["principals"]["both"])},
                400,
            )
        elif fault == "config-version":
            method, value, expected = (
                "POST",
                {**request_value(conv, "message:stale"), "expected_agent_revision": 1},
                409,
            )
        elif fault == "missing-object":
            path = message_path(uuid4()) + SCOPE
            expected = 404
        else:
            new_credential = opaque()
            issuer.add_account("operator", new_credential)
            await client.login(issuer, new_credential)
            method, path, value, expected = (
                "POST",
                message_path(conv) + "/" + first["message_id"] + "/cancel" + SCOPE,
                {"schema_version": 1},
                404,
            )
        assert (await client.call(method, path, value))[0] == expected
        assert rows(conversation, "SELECT count(*) FROM markos_control.messages") == [(1,)]

    asyncio.run(scenario_api(conversation, action))


def test_scoped_runtime_logins_and_old_noop_cannot_bypass_chat(
    conversation: dict[str, Any],
) -> None:
    from tests.integration.test_storage import event

    async def action(
        client: Client, issuer: FixtureIssuer, store: ControlStore, conv: UUID, ctx: SessionContext
    ) -> None:
        receipt = await admit(client, conv)
        async with DurableStore.connect(conversation["publisher"]) as legacy:
            await legacy.append(event())
        assert sorted(
            rows(
                conversation,
                "SELECT consumer_id FROM markos_runtime.event_consumers WHERE event_id<>%s",
                (UUID(receipt["event_id"]),),
            )
        ) == [("foundation-audit",), ("foundation-worker",)]
        for role in ("other_chatworker", "research_chatworker"):
            async with DurableStore.connect(conversation[role]) as scoped:
                assert (
                    await ConversationWorker(scoped, issuer, confirm_synthetic=True).claim() is None
                )
        await handoff(conversation, receipt)
        for role in ("gateway", "chatpub", "chatworker", "chatactivity", "publisher"):
            with psycopg.connect(conversation[role], autocommit=True) as conn:
                for function in (
                    "markos_control.chat_event(%s,%s,%s,%s)",
                    "markos_control.end_chat(%s,%s,%s,'failed','authority')",
                ):
                    args = (
                        (
                            UUID(OWNER),
                            "personal",
                            UUID(receipt["event_id"]),
                            UUID(receipt["message_id"]),
                        )
                        if "chat_event(" in function
                        else (UUID(OWNER), "personal", UUID(receipt["event_id"]))
                    )
                    with pytest.raises(psycopg.errors.InsufficientPrivilege):
                        conn.execute("SELECT " + function, args)
        async with DurableStore.connect(conversation["chatworker"]) as target:
            with pytest.raises(StorageError):
                await target.claim_task(uuid4())
            worker = ConversationWorker(target, issuer, confirm_synthetic=True)
            claim = await worker.claim()
            assert claim
            with (
                psycopg.connect(conversation["chatworker"], autocommit=True) as conn,
                pytest.raises(psycopg.errors.RaiseException),
            ):
                conn.execute(
                    "SELECT markos_runtime.renew_task_epoch(%s,%s,%s,300,%s)",
                    (claim.task_id, claim.fence, worker.executor_id, claim.epoch),
                )
            with pytest.raises(StorageError):
                await worker.finish(claim, "arbitrary output")
            assert await worker.finish(claim, "Synthetic acknowledgement: " + claim.message_sha256)

    asyncio.run(scenario_api(conversation, action))


def test_chat_and_noop_share_two_global_execution_slots(conversation: dict[str, Any]) -> None:
    from tests.integration.test_storage import event

    async def action(
        client: Client, issuer: FixtureIssuer, store: ControlStore, conv: UUID, ctx: SessionContext
    ) -> None:
        first = await admit(client, conv, "message:a")
        second = await admit(client, conv, "message:b")
        await handoff(conversation, first)
        await handoff(conversation, second)
        noop = event()
        async with (
            DurableStore.connect(conversation["publisher"]) as publisher,
            DurableStore.connect(conversation["consumer"]) as old,
            DurableStore.connect(conversation["chatworker"]) as target,
        ):
            await publisher.append(noop)
            await old.handoff(noop)
            old_executor = uuid4()
            legacy = await old.claim_task(old_executor)
            assert legacy
            worker = ConversationWorker(target, issuer, confirm_synthetic=True)
            claim = await worker.claim()
            assert claim
            another = ConversationWorker(target, issuer, confirm_synthetic=True)
            assert await another.claim() is None
            assert rows(
                conversation,
                "SELECT count(*) FROM markos_runtime.execution_slots "
                "WHERE lease_until>clock_timestamp()",
            ) == [(2,)]
            assert await old.finish_task(legacy, old_executor)
            assert await another.claim() is not None

    asyncio.run(scenario_api(conversation, action))


@pytest.mark.parametrize("fault", ["execution", "timeout", "upstream-unavailable"])
def test_fixed_worker_failures_are_terminal_without_automatic_handler_retry(
    conversation: dict[str, Any], fault: str
) -> None:
    class Fail(ConversationWorker):
        async def _execute(self, claim: Any) -> str:
            if fault == "execution":
                raise ValueError("DO_NOT_LOG_SYNTHETIC_PRIVATE_INPUT")
            if fault == "timeout":
                await asyncio.sleep(1)
            return await super()._execute(claim)

    async def action(
        client: Client, issuer: FixtureIssuer, store: ControlStore, conv: UUID, ctx: SessionContext
    ) -> None:
        receipt = await admit(client, conv)
        await handoff(conversation, receipt)
        if fault == "upstream-unavailable":

            async def unavailable(subject: str, session_id: UUID) -> bool:
                raise ValueError("synthetic unavailable authority")

            issuer.session_active = unavailable  # type: ignore[method-assign]
        async with DurableStore.connect(conversation["chatworker"]) as target:
            worker = Fail(target, issuer, confirm_synthetic=True, timeout_seconds=0.01)
            assert not await worker.run_once()
            assert not await worker.run_once()
        assert rows(conversation, "SELECT state,reason FROM markos_control.chat_runs") == [
            ("failed", "dependency" if fault == "upstream-unavailable" else fault)
        ]
        assert rows(
            conversation,
            "SELECT count(*) FROM markos_control.messages WHERE author_kind='test-agent'",
        ) == [(0,)]
        assert rows(
            conversation,
            "SELECT count(*) FROM markos_runtime.execution_slots WHERE task_id IS NOT NULL",
        ) == [(0,)]

    asyncio.run(scenario_api(conversation, action))


def test_held_proposal_requires_current_explicit_promotion_and_restore_cannot_reuse_it(
    conversation: dict[str, Any],
) -> None:
    from markos_runtime.control.store import ControlConflict

    async def action(
        client: Client, issuer: FixtureIssuer, store: ControlStore, conv: UUID, ctx: SessionContext
    ) -> None:
        value = ChatRequest.from_wire(json.dumps(request_value(conv, "message:held")).encode())
        staged = await store.stage_message(ctx, UUID(OWNER), value)
        assert rows(conversation, "SELECT count(*) FROM markos_runtime.events") == [(0,)]
        promoted = await store.admit_message(ctx, UUID(OWNER), value)
        assert promoted.event_id == staged.event_id
        other = ChatRequest.from_wire(json.dumps(request_value(conv, "message:old-epoch")).encode())
        await store.stage_message(ctx, UUID(OWNER), other)
        mutate(conversation, "SELECT markos_runtime.fence_restored_state()")
        fresh = await session(store)
        with pytest.raises(ControlConflict):
            await store.admit_message(fresh, UUID(OWNER), other)
        assert rows(conversation, "SELECT count(*) FROM markos_runtime.events") == [(1,)]

    asyncio.run(scenario_api(conversation, action))


def test_identity_operation_budget_is_shared_with_api(conversation: dict[str, Any]) -> None:
    async def action(
        client: Client, issuer: FixtureIssuer, store: ControlStore, conv: UUID, ctx: SessionContext
    ) -> None:
        receipt = await admit(client, conv)
        await handoff(conversation, receipt)
        async with DurableStore.connect(conversation["chatworker"]) as target:
            worker = ConversationWorker(target, issuer, confirm_synthetic=True)
            claim = await worker.claim()
            assert claim
            leases = [await store._call("api_auth_claim") for _ in range(4)]
            assert all(leases)
            with pytest.raises(StorageError):
                await worker._current(claim)
            for lease in leases:
                await store._call("api_auth_release", UUID(str(lease)))
            assert await worker._current(claim)

    asyncio.run(scenario_api(conversation, action))


@pytest.mark.parametrize("when", ["before-claim", "before-completion"])
def test_changed_message_checksum_cannot_detach_execution_from_its_event(
    conversation: dict[str, Any], when: str
) -> None:
    async def action(
        client: Client, issuer: FixtureIssuer, store: ControlStore, conv: UUID, ctx: SessionContext
    ) -> None:
        receipt = await admit(client, conv)
        await handoff(conversation, receipt)
        async with DurableStore.connect(conversation["chatworker"]) as target:
            worker = ConversationWorker(target, issuer, confirm_synthetic=True)
            claim = await worker.claim() if when == "before-completion" else None
            # Privileged fixture corruption simulates inconsistent restored content.
            # Runtime identities cannot write these records.
            mutate(
                conversation,
                "UPDATE markos_control.messages SET body='changed synthetic text', "
                "body_sha256=encode(sha256(convert_to('changed synthetic text',"
                "'UTF8')),'hex') "
                "WHERE author_kind='human'",
            )
            if claim:
                assert not await worker.finish(
                    claim, "Synthetic acknowledgement: " + claim.message_sha256
                )
            else:
                assert await worker.claim() is None
        assert rows(
            conversation,
            "SELECT count(*) FROM markos_control.messages WHERE author_kind='test-agent'",
        ) == [(0,)]

    asyncio.run(scenario_api(conversation, action))
