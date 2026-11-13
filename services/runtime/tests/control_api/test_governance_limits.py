"""Exact shared storage/attempt admission bounds across scoped synthetic fixtures."""

import asyncio
from typing import Any
from uuid import UUID, uuid4

import psycopg
import pytest
from psycopg.types.json import Jsonb

from markos_runtime.api.identity import FixtureIssuer
from markos_runtime.contracts.control import digest_document
from markos_runtime.control.conversation import ConversationWorker
from markos_runtime.control.store import ControlStore
from markos_runtime.store.database import DurableStore
from tests.control_api.test_conversation import conversation, request_value, rows
from tests.control_api.test_governance import (
    OWN,
    admit_tool,
    approve,
    claim,
    install_grant,
    ready_flow,
)
from tests.integration.test_control_persistence import prepare, session

__all__ = ["conversation"]


def pressure(data: dict[str, Any], count: int, mode: str, *, same_scope: bool = False) -> None:
    """Migration identity seeds counted durable rows; never a client provisioning path.

    Waiting rows retain a current scoped human/session/configuration. Unresolved rows
    belong to terminal chat runs and have no live task or execution lease.
    """
    with psycopg.connect(data["migration"], autocommit=True) as conn:
        base = conn.execute(
            "SELECT actor_id,session_id,epoch,agent_revision,config_revision FROM"
            " markos_control.chat_runs LIMIT 1"
        ).fetchone()
        assert base is not None
        for index in range(count):
            owner = (
                OWN if same_scope else UUID(int=0xA0000000000040008000000000000000 + index // 10)
            )
            event, message, conv, intent, task = [uuid4() for _ in range(5)]
            conn.execute(
                (
                    "INSERT INTO markos_control.memberships "
                    "VALUES(%s,%s,'personal',1,ARRAY['operator','administrator']) ON "
                    "CONFLICT DO NOTHING"
                ),
                (base[0], owner),
            )
            conn.execute(
                (
                    "INSERT INTO markos_control.agents SELECT %s,domain,agent_id,revision"
                    ",enabled,jsonb_set(manifest,'{owner_id}',to_jsonb(%s::text)) FROM "
                    "markos_control.agents WHERE owner_id=%s AND domain='personal' ON "
                    "CONFLICT DO NOTHING"
                ),
                (owner, str(owner), OWN),
            )
            conn.execute(
                (
                    "INSERT INTO markos_control.configurations SELECT %s,domain,revision,"
                    "jsonb_set(jsonb_set(document,'{owner_id}',to_jsonb(%s::text)),'{agen"
                    "t,owner_id}',to_jsonb(%s::text)) FROM markos_control.configurations "
                    "WHERE owner_id=%s AND domain='personal' ON CONFLICT DO NOTHING"
                ),
                (owner, str(owner), str(owner), OWN),
            )
            conn.execute(
                (
                    "INSERT INTO markos_control.tool_grants SELECT %s,domain,grant_id,rev"
                    "ision,jsonb_set(document,'{owner_id}',to_jsonb(%s::text)),enabled "
                    "FROM markos_control.tool_grants WHERE owner_id=%s AND "
                    "domain='personal' ON CONFLICT DO NOTHING"
                ),
                (owner, str(owner), OWN),
            )
            conn.execute(
                (
                    "INSERT INTO markos_control.conversations(owner_id,domain,conversatio"
                    "n_id,created_by,ordinal) VALUES(%s,'personal',%s,%s,%s)"
                ),
                (owner, conv, base[0], 1000 + index),
            )
            conn.execute(
                (
                    "INSERT INTO markos_control.messages(owner_id,domain,conversation_id,"
                    "message_id,sequence,body,body_sha256,author_id) "
                    "VALUES(%s,'personal',%s,%s,1,'Synthetic "
                    "pressure',encode(sha256(convert_to('Synthetic "
                    "pressure','UTF8')),'hex'),%s)"
                ),
                (owner, conv, message, base[0]),
            )
            conn.execute(
                (
                    "INSERT INTO markos_control.chat_runs(owner_id,domain,message_id,even"
                    "t_id,conversation_id,actor_id,session_id,epoch,agent_revision,config"
                    "_revision,state,receipt) "
                    "VALUES(%s,'personal',%s,%s,%s,%s,%s,%s,%s,%s,%s,'{}')"
                ),
                (owner, message, event, conv, *base, "failed" if mode == "reserved" else "working"),
            )
            arguments = {"schema_version": 1, "label": "Synthetic pressure"}
            conn.execute(
                (
                    "INSERT INTO markos_control.tool_plans(owner_id,domain,event_id,argum"
                    "ents,arguments_sha256,state,intent_id) "
                    "VALUES(%s,'personal',%s,%s,%s,%s,%s)"
                ),
                (
                    owner,
                    event,
                    Jsonb(arguments),
                    digest_document(arguments),
                    "unknown"
                    if mode == "reserved"
                    else "queued"
                    if mode == "ready"
                    else "awaiting",
                    None if mode == "ready" else intent,
                ),
            )
            if mode == "ready":
                continue
            grant = conn.execute(
                (
                    "SELECT grant_id,revision FROM markos_control.tool_grants WHERE "
                    "owner_id=%s AND domain='personal'"
                ),
                (owner,),
            ).fetchone()
            assert grant is not None
            conn.execute(
                (
                    "INSERT INTO markos_control.tool_intents(intent_id,owner_id,domain,ev"
                    "ent_id,task_id,principal_id,epoch,task_fence,grant_id,grant_revision"
                    ",policy_revision,document,digest,status,expires_at) VALUES(%s,%s,'pe"
                    "rsonal',%s,%s,%s,%s,1,%s,%s,%s,'{}',%s,%s,clock_timestamp()+interval"
                    " '15 minutes')"
                ),
                (
                    intent,
                    owner,
                    event,
                    task,
                    base[0],
                    base[2],
                    *grant,
                    base[4],
                    "0" * 64,
                    "consumed" if mode == "reserved" else "awaiting",
                ),
            )
            if mode == "reserved":
                conn.execute(
                    (
                        "INSERT INTO markos_control.tool_dispatches(dispatch_id,operation_id,"
                        "intent_id,owner_id,domain,epoch,task_id,fence,executor_id,state,leas"
                        "e_until) VALUES(%s,%s,%s,%s,'personal',%s,%s,1,%s,'unknown',clock_ti"
                        "mestamp()-interval '1 second')"
                    ),
                    (uuid4(), uuid4(), intent, owner, base[2], task, uuid4()),
                )


@pytest.mark.parametrize("bound,seed", [("scope", 19), ("global", 99)])
def test_reserved_outcome_capacity_exact_and_one_over(
    conversation: dict[str, Any], bound: str, seed: int
) -> None:
    async def scenario() -> None:
        async with ready_flow(conversation, decide=False) as flow:
            # Stage both runs before approving either, so each gets an independent immutable intent.
            await admit_tool(conversation, flow["store"], flow["ctx"], key="message:second")
            _, identity = await claim(flow["worker"])
            await flow["worker"]._call("wait_tool", *identity)
            ctx = flow["ctx"]
            items = await flow["store"]._call(
                "read_tool_records",
                ctx.session_id,
                ctx.lookup_hash,
                OWN,
                "personal",
                "approvals",
                0,
                100,
            )
            for item in items:
                await approve(flow["store"], flow["approver"], item)
            pressure(conversation, seed, "reserved", same_scope=bound == "scope")
            first, identity = await claim(flow["worker"])
            _, second_identity = await claim(flow["worker"])
            context = await flow["worker"]._call("tool_context", *identity)
            item = context["intent"]
            answer = await flow["worker"]._call(
                "dispatch_tool",
                *identity,
                UUID(item["intent_id"]),
                context["intent_sha256"],
                flow["grant"]["endpoint"],
            )
            assert "dispatch_id" in answer
            identity = second_identity
            context = await flow["worker"]._call("tool_context", *identity)
            answer = await flow["worker"]._call(
                "dispatch_tool",
                *identity,
                UUID(context["intent"]["intent_id"]),
                context["intent_sha256"],
                flow["grant"]["endpoint"],
            )
            assert answer == {"denied": "budget"}
            assert rows(
                conversation, "SELECT count(*) FROM markos_control.tool_dispatches WHERE reserved"
            ) == [(seed + 1,)]

    asyncio.run(scenario())


@pytest.mark.parametrize("bound,seed", [("scope", 10), ("global", 100)])
def test_approval_capacity_keeps_execution_slots_free(
    conversation: dict[str, Any], bound: str, seed: int
) -> None:
    async def scenario() -> None:
        async with (
            ControlStore.connect(conversation["gateway"]) as store,
            DurableStore.connect(conversation["chatworker"]) as runtime,
        ):
            ctx = await session(store)
            await prepare(store, ctx)
            await install_grant(store, ctx)
            await admit_tool(conversation, store, ctx)
            pressure(conversation, seed, "waiting", same_scope=bound == "scope")
            worker = ConversationWorker(runtime, FixtureIssuer(), confirm_synthetic=True)
            assert await worker._call("claim_governed_chat", worker.executor_id, 30) is None
            assert rows(
                conversation,
                "SELECT count(*) FROM markos_runtime.execution_slots WHERE task_id IS NOT NULL",
            ) == [(0,)]
            with psycopg.connect(conversation["migration"], autocommit=True) as conn:
                conn.execute(
                    "UPDATE markos_control.tool_intents SET status='rejected' WHERE "
                    "intent_id=(SELECT intent_id FROM markos_control.tool_intents LIMIT "
                    "1)"
                )
            _, identity = await claim(worker)
            assert await worker._call("wait_tool", *identity)
            assert rows(
                conversation,
                (
                    "SELECT count(*) FROM markos_control.tool_intents WHERE status IN "
                    "('awaiting','approved')"
                ),
            ) == [(seed,)]

    asyncio.run(scenario())


def test_ready_backlog_preserves_idempotent_receipt_at_capacity(
    conversation: dict[str, Any],
) -> None:
    async def scenario() -> None:
        async with ControlStore.connect(conversation["gateway"]) as store:
            ctx = await session(store)
            conv = await prepare(store, ctx)
            await install_grant(store, ctx)
            request = {
                **request_value(conv, "message:ready"),
                "fixture_arguments": {"schema_version": 1, "label": "Synthetic pressure"},
            }
            original = await store._call(
                "admit_governed_message",
                ctx.session_id,
                ctx.lookup_hash,
                OWN,
                "personal",
                Jsonb(request),
            )
            pressure(conversation, 99, "ready")
            assert (
                await store._call(
                    "admit_governed_message",
                    ctx.session_id,
                    ctx.lookup_hash,
                    OWN,
                    "personal",
                    Jsonb(request),
                )
                == original
            )
            denied = await store._call(
                "admit_governed_message",
                ctx.session_id,
                ctx.lookup_hash,
                OWN,
                "personal",
                Jsonb({**request, "operation_key": "message:overflow"}),
            )
            assert denied == {"denied": "budget"}
            assert rows(
                conversation, "SELECT count(*) FROM markos_control.tool_plans WHERE state='queued'"
            ) == [(100,)]

    asyncio.run(scenario())
