"""Cancellation facts are durable, bounded and available only through scoped readers."""

import asyncio
import json
from typing import Any
from uuid import UUID

import psycopg
import pytest
from psycopg.types.json import Jsonb

from markos_runtime.contracts.governance import GovernedApproval
from markos_runtime.control.store import ControlDenied
from tests.control_api.test_conversation import conversation, mutate
from tests.control_api.test_governance import approve, claim, ready_flow
from tests.integration.conftest import OTHER, OWNER
from tests.integration.test_control_persistence import session

__all__ = ["conversation"]
OWN = UUID(OWNER)


async def read(flow: dict[str, Any]) -> dict[str, Any]:
    ctx = flow["approver"]
    item = await flow["store"]._call(
        "read_tool_object",
        ctx.session_id,
        ctx.lookup_hash,
        OWN,
        "personal",
        "approvals",
        UUID(flow["item"]["intent_id"]),
    )
    typed = GovernedApproval.from_wire(json.dumps(item).encode())
    assert typed.intent_sha256 == flow["item"]["intent_sha256"]
    return dict(item)


@pytest.mark.parametrize(
    "cause,reason",
    [
        ("sign-out", "requester-signed-out"),
        ("expiry", "sign-in-expired"),
        ("membership", "access-changed"),
        ("agent", "settings-changed"),
        ("grant", "access-changed"),
        ("restore", "preview-restored"),
        ("withdraw", "approval-withdrawn"),
        ("cancel", "request-cancelled"),
        ("service", "test-service-check-failed"),
    ],
)
def test_scoped_cancellation_reason_is_captured_at_transition(
    conversation: dict[str, Any], cause: str, reason: str
) -> None:
    async def scenario() -> None:
        async with ready_flow(conversation, decide=False) as flow:
            store, ctx = flow["store"], flow["ctx"]
            if cause == "sign-out":
                await store.revoke_session(ctx)
            elif cause == "expiry":
                mutate(
                    conversation,
                    "UPDATE markos_control.sessions "
                    "SET created_at=clock_timestamp()-interval '2 hours',"
                    "expires_at=clock_timestamp()-interval '1 second' WHERE session_id=%s",
                    (ctx.session_id,),
                )
            elif cause == "membership":
                mutate(
                    conversation,
                    "DELETE FROM markos_control.memberships WHERE principal_id="
                    "(SELECT principal_id FROM markos_control.sessions WHERE session_id=%s) "
                    "AND owner_id=%s AND domain='personal'",
                    (ctx.session_id, OWN),
                )
            elif cause == "agent":
                mutate(conversation, "UPDATE markos_control.agents SET enabled=false")
            elif cause == "grant":
                mutate(conversation, "UPDATE markos_control.tool_grants SET enabled=false")
            elif cause == "restore":
                mutate(conversation, "SELECT markos_runtime.fence_restored_state()")
                flow["approver"] = await session(store, "approver")
            elif cause == "withdraw":
                await approve(store, flow["approver"], flow["item"])
                current = await read(flow)
                approver = flow["approver"]
                await store._call(
                    "decide_tool",
                    approver.session_id,
                    approver.lookup_hash,
                    OWN,
                    "personal",
                    UUID(current["intent_id"]),
                    Jsonb(
                        {
                            "schema_version": 1,
                            "intent_id": current["intent_id"],
                            "intent_sha256": current["intent_sha256"],
                            "expected_revision": current["revision"],
                            "decision": "revoke",
                        }
                    ),
                )
            elif cause == "service":
                await approve(store, flow["approver"], flow["item"])
                _, identity = await claim(flow["worker"])
                await flow["worker"]._call("invalidate_tool_grant", *identity)
            else:
                receipt = flow["receipt"]
                await store._call(
                    "cancel_chat",
                    ctx.session_id,
                    ctx.lookup_hash,
                    OWN,
                    "personal",
                    (await store.read_conversations(ctx, OWN, "personal"))[
                        0
                    ].conversation.conversation_id,
                    UUID(receipt["message_id"]),
                )
            item = await read(flow)
            assert item["status"] == "revoked" and item["cancellation_reason"] == reason
            approver = flow["approver"]
            page = await store._call(
                "read_tool_records",
                approver.session_id,
                approver.lookup_hash,
                OWN,
                "personal",
                "approvals",
                0,
                100,
            )
            assert page[0]["cancellation_reason"] == reason
            # Subsequent facts cannot rewrite the recorded explanation.
            mutate(
                conversation,
                "UPDATE markos_control.sessions "
                "SET revoked_at=coalesce(revoked_at,clock_timestamp()) "
                "WHERE session_id=%s",
                (ctx.session_id,),
            )
            mutate(conversation, "UPDATE markos_control.agents SET enabled=false")
            assert (await read(flow))["cancellation_reason"] == reason
            other = await session(store, "other")
            with pytest.raises(ControlDenied):
                await store._call(
                    "read_tool_object",
                    other.session_id,
                    other.lookup_hash,
                    OWN,
                    "personal",
                    "approvals",
                    UUID(item["intent_id"]),
                )
            assert (
                await store._call(
                    "read_tool_records",
                    other.session_id,
                    other.lookup_hash,
                    UUID(OTHER),
                    "personal",
                    "approvals",
                    0,
                    100,
                )
                == []
            )

    asyncio.run(scenario())


def test_unknown_reason_is_not_invented_and_old_readers_cannot_bypass_scope(
    conversation: dict[str, Any],
) -> None:
    async def scenario() -> None:
        async with ready_flow(conversation, decide=False) as flow:
            mutate(
                conversation,
                "UPDATE markos_control.tool_intents "
                "SET status='revoked',terminal_at=clock_timestamp()",
            )
            item = await read(flow)
            assert item["cancellation_reason"] == "unknown"
            with (
                psycopg.connect(conversation["gateway"]) as conn,
                pytest.raises(psycopg.errors.InsufficientPrivilege),
            ):
                conn.execute(
                    "SELECT markos_control.read_tool_records_m1f(%s,%s,%s,%s,%s,%s,%s)",
                    (
                        flow["approver"].session_id,
                        flow["approver"].lookup_hash,
                        OWN,
                        "personal",
                        "approvals",
                        0,
                        100,
                    ),
                )

    asyncio.run(scenario())
