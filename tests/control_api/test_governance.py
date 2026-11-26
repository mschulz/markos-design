"""Synthetic governance on scoped PostgreSQL; no real provider or credential."""

import asyncio
import json
from contextlib import asynccontextmanager
from typing import Any
from uuid import UUID, uuid4

import psycopg
import pytest
from psycopg.types.json import Jsonb

from markos_runtime.contracts.control import CallIntent, digest_document
from markos_runtime.contracts.governance import (
    FixtureResult,
    GovernedChatRequest,
    ReviewedFixtureGrant,
)
from markos_runtime.control.conversation import ConversationWorker
from markos_runtime.control.store import (
    ControlConflict,
    ControlDenied,
    ControlStore,
    SessionContext,
)
from markos_runtime.store.database import DurableStore, StorageError
from tests.control_api.test_conversation import conversation, handoff, request_value, rows
from tests.integration.conftest import OWNER
from tests.integration.test_control_persistence import prepare, session

__all__ = ["conversation"]
OWN = UUID(OWNER)


async def install_grant(
    store: ControlStore,
    ctx: SessionContext,
    *,
    endpoint: str = "https://127.0.0.1:18443/mcp",
    standing: bool = False,
) -> dict[str, Any]:
    value = ReviewedFixtureGrant(
        schema_version=1,
        owner_id=OWN,
        domain="personal",
        grant_id=uuid4(),
        revision=1,
        previous_revision=0,
        endpoint=endpoint,
        require_approval=not standing,
        enabled=True,
        reason="Explicit synthetic fixture review",
    )
    return dict(
        await store._call(
            "save_tool_grant", ctx.session_id, ctx.lookup_hash, Jsonb(value.model_dump(mode="json"))
        )
    )


async def admit_tool(
    data: dict[str, Any], store: ControlStore, ctx: SessionContext, *, key: str = "message:governed"
) -> dict[str, Any]:
    conv = (await store.read_conversations(ctx, OWN, "personal"))[0].conversation.conversation_id
    value = GovernedChatRequest.from_wire(
        json.dumps(
            {
                **request_value(conv, key),
                "fixture_arguments": {"schema_version": 1, "label": "Synthetic scoped read"},
            }
        ).encode()
    )
    answer = dict(
        await store._call(
            "admit_governed_message",
            ctx.session_id,
            ctx.lookup_hash,
            OWN,
            "personal",
            Jsonb(value.model_dump(mode="json")),
        )
    )
    await handoff(data, answer)
    return answer


async def claim(worker: ConversationWorker) -> tuple[dict[str, Any], tuple[object, ...]]:
    value = await worker._call("claim_governed_chat", worker.executor_id, 30)
    assert value is not None
    return value, (UUID(value["task_id"]), value["fence"], worker.executor_id, UUID(value["epoch"]))


async def approval(store: ControlStore, ctx: SessionContext) -> dict[str, Any]:
    items = await store._call(
        "read_tool_records", ctx.session_id, ctx.lookup_hash, OWN, "personal", "approvals", 0, 100
    )
    assert len(items) == 1
    value = dict(items[0])
    typed = CallIntent.from_wire(json.dumps(value["intent"]).encode())
    assert typed.intent_sha256() == value["intent_sha256"]
    return value


async def approve(store: ControlStore, ctx: SessionContext, value: dict[str, Any]) -> Any:
    return await store._call(
        "decide_tool",
        ctx.session_id,
        ctx.lookup_hash,
        OWN,
        "personal",
        UUID(value["intent_id"]),
        Jsonb(
            {
                "schema_version": 1,
                "intent_id": value["intent_id"],
                "intent_sha256": value["intent_sha256"],
                "expected_revision": value["revision"],
                "decision": "approve",
            }
        ),
    )


def test_durable_approval_wait_exact_dispatch_and_completion(conversation: dict[str, Any]) -> None:
    async def scenario() -> None:
        async with (
            ControlStore.connect(conversation["gateway"]) as store,
            DurableStore.connect(conversation["chatworker"]) as runtime,
        ):
            ctx = await session(store)
            await prepare(store, ctx)
            await install_grant(store, ctx)
            await admit_tool(conversation, store, ctx)
            from markos_runtime.api.identity import FixtureIssuer

            worker = ConversationWorker(runtime, FixtureIssuer(), confirm_synthetic=True)
            assert await worker.claim() is None
            first, identity = await claim(worker)
            await worker._call("wait_tool", *identity)
            assert rows(
                conversation,
                "SELECT count(*) FROM markos_runtime.execution_slots WHERE task_id IS NOT NULL",
            ) == [(0,)]
            assert rows(conversation, "SELECT state FROM markos_runtime.tasks") == [("review",)]
            value = await approval(store, ctx)
            assert value["status"] == "awaiting"
            approver = await session(store, "approver")
            assert (await approve(store, approver, value))["status"] == "approved"
            resumed, identity = await claim(worker)
            assert resumed["fence"] > first["fence"]
            dispatch = await worker._call(
                "dispatch_tool",
                *identity,
                UUID(value["intent_id"]),
                value["intent_sha256"],
                "https://127.0.0.1:18443/mcp",
            )
            assert "dispatch_id" in dispatch
            denied = await worker._call(
                "dispatch_tool",
                *identity,
                UUID(value["intent_id"]),
                value["intent_sha256"],
                "https://127.0.0.1:18443/mcp",
            )
            assert denied == {"denied": "authority"}
            result = FixtureResult(
                schema_version=1,
                owner_id=OWN,
                domain="personal",
                kind="synthetic",
                label="Synthetic scoped read",
            ).model_dump(mode="json")
            assert await worker._call(
                "finish_tool",
                *identity,
                UUID(dispatch["dispatch_id"]),
                Jsonb(result),
                digest_document(result),
                False,
            )
            assert await worker._call(
                "finish_chat",
                *identity,
                "Synthetic acknowledgement: " + resumed["message_sha256"],
                None,
            )
            assert rows(
                conversation, "SELECT state,reserved FROM markos_control.tool_dispatches"
            ) == [("succeeded", False)]
            assert rows(
                conversation,
                "SELECT count(*) FROM markos_control.messages WHERE author_kind='test-agent'",
            ) == [(1,)]
            assert rows(
                conversation, "SELECT outcome FROM markos_control.tool_audit ORDER BY sequence"
            ) == [
                ("requested",),
                ("requested",),
                ("awaiting",),
                ("approved",),
                ("dispatched",),
                ("denied",),
                ("succeeded",),
            ]

    asyncio.run(scenario())


@asynccontextmanager
async def ready_flow(data: dict[str, Any], *, standing: bool = False, decide: bool = True) -> Any:
    from markos_runtime.api.identity import FixtureIssuer

    async with (
        ControlStore.connect(data["gateway"]) as store,
        DurableStore.connect(data["chatworker"]) as runtime,
    ):
        ctx = await session(store)
        await prepare(store, ctx)
        grant = await install_grant(store, ctx, standing=standing)
        receipt = await admit_tool(data, store, ctx)
        worker = ConversationWorker(runtime, FixtureIssuer(), confirm_synthetic=True)
        initial, first_identity = await claim(worker)
        await worker._call("wait_tool", *first_identity)
        item = await approval(store, ctx)
        approver = await session(store, "approver")
        if decide and not standing:
            await approve(store, approver, item)
        yield {
            "store": store,
            "runtime": runtime,
            "ctx": ctx,
            "worker": worker,
            "item": item,
            "approver": approver,
            "initial": initial,
            "first_identity": first_identity,
            "receipt": receipt,
            "grant": grant,
        }


async def dispatch(
    flow: dict[str, Any],
) -> tuple[dict[str, Any], tuple[object, ...], dict[str, Any]]:
    current, identity = await claim(flow["worker"])
    item = flow["item"]
    answer = await flow["worker"]._call(
        "dispatch_tool",
        *identity,
        UUID(item["intent_id"]),
        item["intent_sha256"],
        flow["grant"]["endpoint"],
    )
    return current, identity, dict(answer)


async def complete(flow: dict[str, Any]) -> None:
    current, identity, answer = await dispatch(flow)
    result = FixtureResult(
        schema_version=1,
        owner_id=OWN,
        domain="personal",
        kind="synthetic",
        label="Synthetic scoped read",
    ).model_dump(mode="json")
    assert await flow["worker"]._call(
        "finish_tool",
        *identity,
        UUID(answer["dispatch_id"]),
        Jsonb(result),
        digest_document(result),
        False,
    )
    assert await flow["worker"]._call(
        "finish_chat", *identity, "Synthetic acknowledgement: " + current["message_sha256"], None
    )


@pytest.mark.parametrize("who", ["viewer", "operator", "administrator"])
def test_only_current_approver_can_decide(conversation: dict[str, Any], who: str) -> None:
    async def scenario() -> None:
        async with ready_flow(conversation, decide=False) as flow:
            ctx = await session(flow["store"], who)
            with pytest.raises(ControlDenied):
                await approve(flow["store"], ctx, flow["item"])
            assert rows(conversation, "SELECT status FROM markos_control.tool_intents") == [
                ("awaiting",)
            ]

    asyncio.run(scenario())


@pytest.mark.parametrize("fault", ["digest", "revision", "id"])
def test_decisions_bind_exact_immutable_intent(conversation: dict[str, Any], fault: str) -> None:
    async def scenario() -> None:
        async with ready_flow(conversation, decide=False) as flow:
            item = dict(flow["item"])
            if fault == "digest":
                item["intent_sha256"] = "0" * 64
            elif fault == "revision":
                item["revision"] = 10
            else:
                item["intent_id"] = str(uuid4())
            with pytest.raises((ControlConflict, StorageError)):
                await approve(flow["store"], flow["approver"], item)
            assert rows(conversation, "SELECT status FROM markos_control.tool_intents") == [
                ("awaiting",)
            ]

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "change",
    [
        "agent",
        "configuration",
        "request-session",
        "requester",
        "membership",
        "approver",
        "expiry",
        "grant",
    ],
)
def test_current_authority_change_blocks_dispatch(
    conversation: dict[str, Any], change: str
) -> None:
    async def scenario() -> None:
        async with ready_flow(conversation) as flow:
            _, identity = await claim(flow["worker"])
            with psycopg.connect(conversation["migration"], autocommit=True) as conn:
                if change == "agent":
                    conn.execute("UPDATE markos_control.agents SET enabled=false")
                elif change == "configuration":
                    conn.execute("UPDATE markos_control.configurations SET revision=revision+1")
                elif change == "request-session":
                    await flow["store"].revoke_session(flow["ctx"])
                elif change == "requester":
                    conn.execute(
                        "UPDATE markos_control.principals SET disabled=true WHERE principal_id=%s",
                        (conversation["principals"]["both"],),
                    )
                elif change == "membership":
                    conn.execute(
                        "DELETE FROM markos_control.memberships WHERE principal_id=%s",
                        (conversation["principals"]["both"],),
                    )
                elif change == "approver":
                    conn.execute(
                        "DELETE FROM markos_control.memberships WHERE principal_id=%s",
                        (conversation["principals"]["approver"],),
                    )
                elif change == "expiry":
                    conn.execute(
                        "UPDATE markos_control.tool_intents "
                        "SET expires_at=clock_timestamp()-interval '1 second'"
                    )
                else:
                    grant = {
                        **flow["grant"],
                        "revision": 2,
                        "previous_revision": 1,
                        "enabled": False,
                    }
                    ctx = flow["ctx"]
                    await flow["store"]._call(
                        "save_tool_grant", ctx.session_id, ctx.lookup_hash, Jsonb(grant)
                    )
            try:
                answer = await flow["worker"]._call(
                    "dispatch_tool",
                    *identity,
                    UUID(flow["item"]["intent_id"]),
                    flow["item"]["intent_sha256"],
                    flow["grant"]["endpoint"],
                )
                assert "denied" in answer
            except StorageError:
                pass
            assert rows(conversation, "SELECT count(*) FROM markos_control.tool_dispatches") == [
                (0,)
            ]

    asyncio.run(scenario())


@pytest.mark.parametrize("fault", ["digest", "destination"])
def test_dispatch_rejects_changed_binding(conversation: dict[str, Any], fault: str) -> None:
    async def scenario() -> None:
        async with ready_flow(conversation) as flow:
            _, identity = await claim(flow["worker"])
            item = flow["item"]
            answer = await flow["worker"]._call(
                "dispatch_tool",
                *identity,
                UUID(item["intent_id"]),
                "0" * 64 if fault == "digest" else item["intent_sha256"],
                "https://127.0.0.1:18444/mcp"
                if fault == "destination"
                else flow["grant"]["endpoint"],
            )
            assert answer == {"denied": "authority"}
            assert rows(conversation, "SELECT status FROM markos_control.tool_intents") == [
                ("approved",)
            ]

    asyncio.run(scenario())


def test_competing_dispatches_consume_approval_once(conversation: dict[str, Any]) -> None:
    async def scenario() -> None:
        async with ready_flow(conversation) as flow:
            _, identity = await claim(flow["worker"])
            item = flow["item"]

            async def attempt() -> Any:
                return await flow["worker"]._call(
                    "dispatch_tool",
                    *identity,
                    UUID(item["intent_id"]),
                    item["intent_sha256"],
                    flow["grant"]["endpoint"],
                )

            outcomes = await asyncio.gather(attempt(), attempt())
            assert sum("dispatch_id" in value for value in outcomes) == 1
            assert rows(
                conversation,
                "SELECT count(*) FROM markos_control.tool_audit WHERE outcome='dispatched'",
            ) == [(1,)]

    asyncio.run(scenario())


@pytest.mark.parametrize("during_dispatch", [False, True])
def test_restore_invalidates_pending_authority_and_preserves_unknown(
    conversation: dict[str, Any], during_dispatch: bool
) -> None:
    async def scenario() -> None:
        async with ready_flow(conversation) as flow:
            if during_dispatch:
                await dispatch(flow)
            with psycopg.connect(conversation["migration"], autocommit=True) as conn:
                conn.execute("SELECT markos_runtime.fence_restored_state()")
            assert await flow["worker"]._call("claim_governed_chat", uuid4(), 30) is None
            expected = "consumed" if during_dispatch else "revoked"
            assert rows(conversation, "SELECT status FROM markos_control.tool_intents") == [
                (expected,)
            ]
            if during_dispatch:
                assert rows(
                    conversation, "SELECT state,reserved FROM markos_control.tool_dispatches"
                ) == [("unknown", True)]
            assert rows(
                conversation,
                "SELECT count(*) FROM markos_control.tool_slots WHERE dispatch_id IS NOT NULL",
            ) == [(0,)]

    asyncio.run(scenario())


def test_expired_dispatch_becomes_unknown_without_repeating(conversation: dict[str, Any]) -> None:
    async def scenario() -> None:
        async with ready_flow(conversation) as flow:
            _, identity, answer = await dispatch(flow)
            with psycopg.connect(conversation["migration"], autocommit=True) as conn:
                conn.execute(
                    "UPDATE markos_control.tool_dispatches "
                    "SET lease_until=clock_timestamp()-interval '1 second'"
                )
            ctx = flow["ctx"]
            await flow["store"]._call(
                "read_tool_records",
                ctx.session_id,
                ctx.lookup_hash,
                OWN,
                "personal",
                "dispatches",
                0,
                100,
            )
            assert rows(
                conversation, "SELECT state,reserved FROM markos_control.tool_dispatches"
            ) == [("unknown", True)]
            assert await flow["worker"]._call("claim_governed_chat", uuid4(), 30) is None
            with pytest.raises(StorageError):
                await flow["worker"]._call(
                    "finish_tool", *identity, UUID(answer["dispatch_id"]), None, None, True
                )

    asyncio.run(scenario())


def test_standing_grant_is_exact_and_has_one_attempt(conversation: dict[str, Any]) -> None:
    async def scenario() -> None:
        async with ready_flow(conversation, standing=True) as flow:
            assert flow["item"]["status"] == "approved" and flow["item"]["approver_id"] is None
            await complete(flow)
            assert rows(conversation, "SELECT count(*) FROM markos_control.tool_dispatches") == [
                (1,)
            ]

    asyncio.run(scenario())


@pytest.mark.parametrize("role", ["gateway", "chatworker", "unmapped"])
def test_runtime_roles_cannot_rewrite_audit_or_cleanup(
    conversation: dict[str, Any], role: str
) -> None:
    with psycopg.connect(conversation[role], autocommit=True) as conn:
        for query in [
            "DELETE FROM markos_control.tool_audit",
            "UPDATE markos_control.tool_grants SET enabled=true",
            "SELECT markos_control.cleanup_tools()",
            "SELECT markos_runtime.fence_restored_state()",
        ]:
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                conn.execute(query)


@pytest.mark.parametrize("change", ["arguments", "ordinary-profile"])
def test_same_operation_cannot_change_governance_intent(
    conversation: dict[str, Any], change: str
) -> None:
    async def scenario() -> None:
        async with ready_flow(conversation) as flow:
            conv = rows(conversation, "SELECT conversation_id FROM markos_control.chat_runs")[0][0]
            value = request_value(conv, "message:governed")
            ctx = flow["ctx"]
            with pytest.raises(ControlConflict):
                if change == "ordinary-profile":
                    await flow["store"]._call(
                        "admit_message",
                        ctx.session_id,
                        ctx.lookup_hash,
                        OWN,
                        "personal",
                        Jsonb(value),
                    )
                else:
                    value["fixture_arguments"] = {
                        "schema_version": 1,
                        "label": "Changed private arguments",
                    }
                    await flow["store"]._call(
                        "admit_governed_message",
                        ctx.session_id,
                        ctx.lookup_hash,
                        OWN,
                        "personal",
                        Jsonb(value),
                    )
            assert rows(conversation, "SELECT count(*) FROM markos_control.chat_runs") == [(1,)]

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "stamp", ["2030-01-01T00:00:00Z", "2030-01-01T00:00:00.100000Z", "2030-01-01T00:00:00.123456Z"]
)
def test_sql_timestamp_binding_matches_contract_utc_serialization(
    conversation: dict[str, Any], stamp: str
) -> None:
    with psycopg.connect(conversation["migration"]) as conn:
        assert conn.execute(
            "SELECT markos_control.tool_timestamp(%s::timestamptz)", (stamp,)
        ).fetchone() == (stamp,)
