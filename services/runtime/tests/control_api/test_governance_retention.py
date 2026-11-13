"""Exact synthetic maintenance boundaries; no production data or runtime clock override."""

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import psycopg
import pytest
from psycopg import sql
from psycopg.types.json import Jsonb

from markos_runtime.store.database import StorageError
from tests.control_api.test_conversation import conversation, request_value, rows
from tests.control_api.test_governance import OWN, claim, complete, dispatch, ready_flow

__all__ = ["conversation"]


def policy_clock(data: dict[str, Any], now: datetime) -> None:
    with psycopg.connect(data["migration"], autocommit=True) as conn:
        conn.execute(
            sql.SQL(
                "CREATE OR REPLACE FUNCTION markos_runtime.recovery_now() "
                "RETURNS timestamptz LANGUAGE sql VOLATILE SET search_path=pg_catalog "
                "AS {}"
            ).format(sql.Literal("SELECT '" + now.isoformat() + "'::timestamptz"))
        )


def cleanup(data: dict[str, Any]) -> dict[str, Any]:
    with psycopg.connect(data["migration"], autocommit=True) as conn:
        row = conn.execute("SELECT markos_control.cleanup_tools()").fetchone()
        assert row is not None
        return dict(row[0])


def test_seven_and_365_day_boundaries_and_tombstone(conversation: dict[str, Any]) -> None:
    async def scenario() -> None:
        async with ready_flow(conversation) as flow:
            conv = rows(conversation, "SELECT conversation_id FROM markos_control.chat_runs")[0][0]
            await complete(flow)
            terminal = datetime(2030, 1, 1, tzinfo=UTC)
            with psycopg.connect(conversation["migration"], autocommit=True) as conn:
                conn.execute("UPDATE markos_control.tool_intents SET terminal_at=%s", (terminal,))
                conn.execute(
                    "UPDATE markos_control.tool_dispatches SET finished_at=%s", (terminal,)
                )
            policy_clock(conversation, terminal + timedelta(days=7) - timedelta(microseconds=1))
            assert cleanup(conversation)["payload_copies_purged"] == 0
            policy_clock(conversation, terminal + timedelta(days=7))
            assert cleanup(conversation)["payload_copies_purged"] == 1
            assert rows(conversation, "SELECT document FROM markos_control.tool_intents") == [
                (None,)
            ]
            assert rows(conversation, "SELECT arguments FROM markos_control.tool_plans") == [
                (None,)
            ]
            assert rows(conversation, "SELECT result FROM markos_control.tool_dispatches") == [
                (None,)
            ]
            policy_clock(conversation, terminal + timedelta(days=365) - timedelta(microseconds=1))
            assert cleanup(conversation)["terminal_intents_purged"] == 0
            policy_clock(conversation, terminal + timedelta(days=365))
            assert cleanup(conversation)["terminal_intents_purged"] == 1
            assert rows(conversation, "SELECT count(*) FROM markos_control.tool_intents") == [(0,)]
            assert rows(conversation, "SELECT count(*) FROM markos_control.tool_tombstones") == [
                (1,)
            ]
            request = {
                **request_value(conv, "message:governed"),
                "fixture_arguments": {"schema_version": 1, "label": "Synthetic scoped read"},
            }
            ctx = flow["ctx"]
            answer = await flow["store"]._call(
                "admit_governed_message",
                ctx.session_id,
                ctx.lookup_hash,
                OWN,
                "personal",
                Jsonb(request),
            )
            assert answer == {"denied": "retired"}

    asyncio.run(scenario())


@pytest.mark.parametrize("protected", ["pending", "hold", "unknown"])
def test_pending_unresolved_and_holds_protect_minimal_evidence(
    conversation: dict[str, Any], protected: str
) -> None:
    async def scenario() -> None:
        async with ready_flow(conversation, decide=protected != "pending") as flow:
            if protected == "hold":
                await complete(flow)
            elif protected == "unknown":
                _, identity, answer = await dispatch(flow)
                assert await flow["worker"]._call(
                    "finish_tool", *identity, UUID(answer["dispatch_id"]), None, None, True
                )
            ancient = datetime(2030, 1, 1, tzinfo=UTC)
            with psycopg.connect(conversation["migration"], autocommit=True) as conn:
                if protected != "pending":
                    conn.execute(
                        "UPDATE markos_control.tool_intents SET terminal_at=%s", (ancient,)
                    )
                conn.execute("UPDATE markos_control.tool_dispatches SET started_at=%s", (ancient,))
                if protected == "hold":
                    conn.execute(
                        "INSERT INTO markos_control.tool_holds VALUES(%s,%s,'synthetic-review',%s)",
                        (UUID(flow["item"]["intent_id"]), OWN, ancient),
                    )
            policy_clock(conversation, ancient + timedelta(days=400))
            result = cleanup(conversation)
            assert result["payload_copies_purged"] == result["terminal_intents_purged"] == 0
            assert rows(
                conversation,
                "SELECT count(*) FROM markos_control.tool_intents WHERE document IS NOT NULL",
            ) == [(1,)]
            assert result["unresolved_alerts"] == (1 if protected == "unknown" else 0)
            assert result["holds_due"] == (1 if protected == "hold" else 0)

    asyncio.run(scenario())


def test_rate_deferral_reclaims_fresh_lease_without_changing_approved_intent(
    conversation: dict[str, Any],
) -> None:
    async def scenario() -> None:
        async with ready_flow(conversation) as flow:
            _, old_identity = await claim(flow["worker"])
            with psycopg.connect(conversation["migration"], autocommit=True) as conn:
                conn.execute(
                    (
                        "INSERT INTO markos_control.tool_rates "
                        "VALUES(%s,'personal',0,clock_timestamp())"
                    ),
                    (OWN,),
                )
            item = flow["item"]
            denied = await flow["worker"]._call(
                "dispatch_tool",
                *old_identity,
                UUID(item["intent_id"]),
                item["intent_sha256"],
                flow["grant"]["endpoint"],
            )
            assert denied == {"denied": "budget"}
            assert rows(conversation, "SELECT count(*) FROM markos_control.tool_dispatches") == [
                (0,)
            ]
            await flow["worker"]._call("defer_tool", *old_identity)
            assert rows(
                conversation,
                ("SELECT count(*) FROM markos_runtime.execution_slots WHERE task_id IS NOT NULL"),
            ) == [(0,)]
            assert await flow["worker"]._call("claim_governed_chat", uuid4(), 30) is None
            with psycopg.connect(conversation["migration"], autocommit=True) as conn:
                conn.execute("UPDATE markos_control.tool_rates SET tokens=2,at=clock_timestamp()")
            current, identity, answer = await dispatch(flow)
            assert "dispatch_id" in answer and current["fence"] > old_identity[1]
            with pytest.raises(StorageError):
                await flow["worker"]._call(
                    "dispatch_tool",
                    *old_identity,
                    UUID(item["intent_id"]),
                    item["intent_sha256"],
                    flow["grant"]["endpoint"],
                )
            # The immutable approved document is unchanged; only its fresh execution mapping moves.
            assert rows(conversation, "SELECT digest FROM markos_control.tool_intents") == [
                (item["intent_sha256"],)
            ]

    asyncio.run(scenario())
