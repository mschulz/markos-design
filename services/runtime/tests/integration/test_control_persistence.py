"""Actual disposable PostgreSQL; no HTTP identity issuer, broker chat or external tools."""

import asyncio
import hashlib
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import psycopg
import pytest
from psycopg import sql
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from markos_runtime.contracts.base import Domain
from markos_runtime.contracts.control import (
    AgentRevision,
    AgentStateRequest,
    ChatRequest,
    ConfigRevision,
)
from markos_runtime.control.store import (
    ControlConflict,
    ControlDenied,
    ControlLimited,
    ControlStore,
    SessionContext,
)
from markos_runtime.store.database import DurableStore, StorageError
from markos_runtime.store.migrations import migrate
from tests.integration.conftest import OTHER, OWNER
from tests.integration.test_storage import event

OWN = UUID(OWNER)
ELSE = UUID(OTHER)


@pytest.fixture
def control(database: dict[str, str]) -> dict[str, Any]:
    principals = {
        name: uuid4()
        for name in ("viewer", "operator", "approver", "administrator", "both", "other")
    }
    with psycopg.connect(database["migration"], autocommit=True) as conn:
        conn.execute("INSERT INTO markos_control.gateway_logins VALUES('m1b_gateway')")
        for name, principal in principals.items():
            roles = (
                ["administrator", "operator"]
                if name == "both"
                else ["operator" if name == "other" else name]
            )
            conn.execute(
                "INSERT INTO markos_control.principals VALUES(%s,'synthetic-issuer',%s,false)",
                (principal, name),
            )
            conn.execute(
                "INSERT INTO markos_control.memberships VALUES(%s,%s,'personal',1,%s)",
                (principal, ELSE if name == "other" else OWN, roles),
            )
        conn.execute(
            "INSERT INTO markos_control.memberships "
            "VALUES(%s,%s,'research',1,ARRAY['operator','administrator'])",
            (principals["both"], OWN),
        )
    return {**database, "principals": principals}


async def session(
    store: ControlStore, who: str = "both", *, kind: str = "browser"
) -> SessionContext:
    opened = await store.open_session(
        issuer="synthetic-issuer",
        subject=who,
        provider_session_id=uuid4(),
        lookup_hash=hashlib.sha256(uuid4().bytes).hexdigest(),
        kind=kind,  # type: ignore[arg-type]
        provider_expires_at=datetime.now(UTC) + timedelta(hours=1),
    )
    return opened.context


async def prepare(
    store: ControlStore, ctx: SessionContext, owner: UUID = OWN, domain: Domain = "personal"
) -> UUID:
    agent = AgentRevision(
        schema_version=1,
        owner_id=owner,
        domain=domain,
        agent_id="control-test-agent",
        revision=1,
        description="Synthetic deterministic agent",
    )
    saved = await store.save_agent(ctx, agent, expected_revision=0)
    assert not saved.enabled
    active = await store.set_agent_state(
        ctx,
        owner,
        domain,
        AgentStateRequest(
            schema_version=1, expected_revision=1, enabled=True, reason="Synthetic state only"
        ),
    )
    await store.save_configuration(
        ctx,
        ConfigRevision(
            schema_version=1,
            owner_id=owner,
            domain=domain,
            revision=1,
            previous_revision=0,
            agent=active.manifest,
            reason="Synthetic configuration",
        ),
    )
    conversation = uuid4()
    await store.create_conversation(
        ctx, owner, domain, conversation, "conversation:" + conversation.hex
    )
    return conversation


def request(
    conversation: UUID,
    *,
    key: str = "message:one",
    text: str = "Synthetic hello",
    revision: int = 2,
    domain: Domain = "personal",
) -> ChatRequest:
    return ChatRequest(
        schema_version=1,
        domain=domain,
        conversation_id=conversation,
        operation_key=key,
        agent_id="control-test-agent",
        expected_agent_revision=revision,
        text=text,
    )


def counts(data: dict[str, Any]) -> tuple[int, ...]:
    with psycopg.connect(data["migration"]) as conn:
        result = []
        for table in ("messages", "message_intents", "activity", "idempotency", "audit"):
            row = conn.execute(
                sql.SQL("SELECT count(*) FROM markos_control.{}").format(sql.Identifier(table))
            ).fetchone()
            assert row is not None
            result.append(int(row[0]))
        return tuple(result)


def test_persisted_registry_conversation_message_and_held_intent(control: dict[str, Any]) -> None:
    async def scenario() -> None:
        async with ControlStore.connect(control["gateway"]) as store:
            ctx = await session(store)
            conv = await prepare(store, ctx)
            staged = await store.stage_message(ctx, OWN, request(conv))
            assert staged.state == "held" and isinstance(staged.message_id, UUID)
            assert (
                staged.sequence == 1 and staged.agent_revision == 2 and staged.config_revision == 1
            )
            assert (await store.read_messages(ctx, OWN, "personal", conv))[
                0
            ].body == "Synthetic hello"
            assert (await store.read_activity(ctx, OWN, "personal"))[
                0
            ].object_id == staged.message_id
            assert (await store.read_conversations(ctx, OWN, "personal"))[
                0
            ].conversation.conversation_id == conv
            assert (await store.read_configuration(ctx, OWN, "personal")).revision == 1  # type: ignore[union-attr]
            assert len(await store.read_agents(ctx, OWN, "research")) == 0
            # The persistence slice cannot publish or execute control work.
            with psycopg.connect(control["migration"]) as conn:
                assert conn.execute("SELECT count(*) FROM markos_runtime.events").fetchone() == (0,)
                assert conn.execute("SELECT count(*) FROM markos_runtime.outbox").fetchone() == (0,)
                assert conn.execute(
                    "SELECT state,intended_consumer FROM markos_control.message_intents"
                ).fetchone() == ("held", "control-test-agent")

    asyncio.run(scenario())


@pytest.mark.parametrize("role", ["viewer", "approver", "administrator"])
def test_nonoperator_roles_cannot_create_conversations(control: dict[str, Any], role: str) -> None:
    async def scenario() -> None:
        async with ControlStore.connect(control["gateway"]) as store:
            ctx = await session(store, role)
            assert await store.read_agents(ctx, OWN, "personal") == ()
            with pytest.raises(ControlDenied):
                await store.create_conversation(ctx, OWN, "personal", uuid4(), "deny:1")

    asyncio.run(scenario())


@pytest.mark.parametrize("role", ["viewer", "approver", "operator"])
def test_nonadministrator_roles_cannot_change_registry(control: dict[str, Any], role: str) -> None:
    async def scenario() -> None:
        async with ControlStore.connect(control["gateway"]) as store:
            ctx = await session(store, role)
            agent = AgentRevision(
                schema_version=1,
                owner_id=OWN,
                domain="personal",
                agent_id="control-test-agent",
                revision=1,
                description="Synthetic",
            )
            with pytest.raises(ControlDenied):
                await store.save_agent(ctx, agent, expected_revision=0)

    asyncio.run(scenario())


@pytest.mark.parametrize("target", ["publisher", "consumer", "unmapped", "admin", "migration"])
def test_gateway_binding_rejects_runtime_and_privileged_logins(
    control: dict[str, Any], target: str
) -> None:
    async def scenario() -> None:
        with pytest.raises(StorageError):
            async with ControlStore.connect(control[target]):
                pass

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "table",
    [
        "principals",
        "memberships",
        "sessions",
        "agents",
        "agent_history",
        "configuration_history",
        "messages",
        "message_intents",
        "activity",
        "audit",
        "gateway_logins",
    ],
)
def test_gateway_cannot_bypass_functions_or_edit_history(
    control: dict[str, Any], table: str
) -> None:
    with psycopg.connect(control["gateway"], autocommit=True) as conn:
        for query in [
            sql.SQL("SELECT * FROM markos_control.{}").format(sql.Identifier(table)),
            sql.SQL("DELETE FROM markos_control.{}").format(sql.Identifier(table)),
        ]:
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                conn.execute(query)
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute("SELECT markos_control.session_context(NULL,NULL)")
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute("SELECT markos_runtime.fence_restored_state()")


def test_cross_owner_domain_pool_reuse_and_forged_session_settings(control: dict[str, Any]) -> None:
    async def scenario() -> None:
        async with ControlStore.connect(control["gateway"], max_connections=1) as store:
            own = await session(store)
            other = await session(store, "other")
            conv = await prepare(store, own)
            await store.stage_message(own, OWN, request(conv))
            scopes: list[tuple[SessionContext, UUID, Domain]] = [
                (other, OWN, "personal"),
                (own, ELSE, "personal"),
                (own, OWN, "work"),
            ]
            for ctx, owner, domain in scopes:
                with pytest.raises(ControlDenied):
                    await store.read_messages(ctx, owner, domain, conv)
            assert await store.read_conversations(other, ELSE, "personal") == ()
            assert (await store.read_messages(own, OWN, "personal", conv))[
                0
            ].body == "Synthetic hello"
            with psycopg.connect(control["gateway"], autocommit=True) as conn:
                conn.execute("SET markos.owner_id='99999999-9999-4999-8999-999999999999'")
                with pytest.raises(psycopg.errors.InsufficientPrivilege):
                    conn.execute(
                        "SELECT markos_control.read_agents(%s,%s,%s,'personal')",
                        (own.session_id, own.lookup_hash, ELSE),
                    )

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "change",
    [
        "revoked",
        "idle",
        "absolute",
        "provider",
        "principal",
        "membership",
        "hash",
        "gateway",
        "missing-epoch",
    ],
)
def test_session_and_membership_revalidated_on_every_call(
    control: dict[str, Any], change: str
) -> None:
    async def scenario() -> None:
        async with ControlStore.connect(control["gateway"]) as store:
            ctx = await session(store)
            assert await store.read_agents(ctx, OWN, "personal") == ()
            with psycopg.connect(control["migration"], autocommit=True) as conn:
                if change == "revoked":
                    conn.execute(
                        "UPDATE markos_control.sessions SET revoked_at=clock_timestamp() "
                        "WHERE session_id=%s",
                        (ctx.session_id,),
                    )
                elif change == "idle":
                    conn.execute(
                        "UPDATE markos_control.sessions SET "
                        "last_seen_at=clock_timestamp()-interval '31 minutes' WHERE "
                        "session_id=%s",
                        (ctx.session_id,),
                    )
                elif change in {"absolute", "provider"}:
                    field = "expires_at" if change == "absolute" else "provider_expires_at"
                    conn.execute(
                        sql.SQL(
                            "UPDATE markos_control.sessions SET "
                            "created_at=clock_timestamp()-interval '2 "
                            "hours',{}=clock_timestamp()-interval '1 second' WHERE "
                            "session_id=%s"
                        ).format(sql.Identifier(field)),
                        (ctx.session_id,),
                    )
                elif change == "principal":
                    conn.execute(
                        "UPDATE markos_control.principals SET disabled=true WHERE principal_id=%s",
                        (control["principals"]["both"],),
                    )
                elif change == "missing-epoch":
                    conn.execute("DELETE FROM markos_runtime.runtime_epoch")
                elif change == "gateway":
                    conn.execute(
                        "DELETE FROM markos_control.gateway_logins WHERE login_role='m1b_gateway'"
                    )
                elif change == "membership":
                    conn.execute(
                        "DELETE FROM markos_control.memberships WHERE principal_id=%s AND "
                        "domain='personal'",
                        (control["principals"]["both"],),
                    )
            if change == "hash":
                ctx = SessionContext(ctx.session_id, "f" * 64)
            with pytest.raises(ControlDenied):
                await store.read_agents(ctx, OWN, "personal")

    asyncio.run(scenario())


def test_session_hash_only_storage_budget_and_logout(control: dict[str, Any]) -> None:
    async def scenario() -> None:
        async with ControlStore.connect(control["gateway"]) as store:
            contexts = [await session(store) for _ in range(10)]
            with pytest.raises(ControlLimited):
                await session(store)
            assert await store.revoke_session(contexts[0])
            assert not await store.revoke_session(contexts[0])
            new = await session(store)
            assert await store.read_agents(new, OWN, "personal") == ()
            with psycopg.connect(control["migration"]) as conn:
                row = conn.execute(
                    "SELECT lookup_hash,expires_at-created_at FROM "
                    "markos_control.sessions WHERE session_id=%s",
                    (new.session_id,),
                ).fetchone()
                assert row and len(row[0]) == 64 and row[1] <= timedelta(hours=1)
            cli = await session(store, "operator", kind="cli")
            with psycopg.connect(control["migration"]) as conn:
                assert conn.execute(
                    "SELECT expires_at-created_at<=interval '5 minutes' FROM "
                    "markos_control.sessions WHERE session_id=%s",
                    (cli.session_id,),
                ).fetchone() == (True,)
            assert "lookup_hash" not in repr(new)

    asyncio.run(scenario())


def test_duplicate_requests_concurrency_and_changed_content_conflict(
    control: dict[str, Any],
) -> None:
    async def scenario() -> None:
        async with ControlStore.connect(control["gateway"]) as store:
            ctx = await session(store)
            conv = await prepare(store, ctx)
            receipts = await asyncio.gather(
                *(store.stage_message(ctx, OWN, request(conv)) for _ in range(8))
            )
            assert len({r.message_id for r in receipts}) == 1
            with pytest.raises(ControlConflict):
                await store.stage_message(ctx, OWN, request(conv, text="Changed synthetic content"))
            assert len(await store.read_messages(ctx, OWN, "personal", conv)) == 1
            assert len(await store.read_activity(ctx, OWN, "personal")) == 1

    asyncio.run(scenario())


@pytest.mark.parametrize("fault_table", ["message_intents", "activity", "idempotency", "audit"])
def test_staged_message_transaction_rolls_back_at_each_persistence_boundary(
    control: dict[str, Any], fault_table: str
) -> None:
    async def scenario() -> None:
        async with ControlStore.connect(control["gateway"]) as store:
            ctx = await session(store)
            conv = await prepare(store, ctx)
            before = counts(control)
            with psycopg.connect(control["migration"], autocommit=True) as conn:
                conn.execute(
                    "CREATE FUNCTION markos_control.test_fault() RETURNS trigger "
                    "LANGUAGE plpgsql AS $$BEGIN RAISE EXCEPTION 'synthetic write "
                    "fault';END$$"
                )
                conn.execute(
                    sql.SQL(
                        "CREATE TRIGGER test_fault BEFORE INSERT ON markos_control.{} FOR "
                        "EACH ROW EXECUTE FUNCTION markos_control.test_fault()"
                    ).format(sql.Identifier(fault_table))
                )
            with pytest.raises(StorageError):
                await store.stage_message(ctx, OWN, request(conv))
            assert counts(control) == before
            with psycopg.connect(control["migration"], autocommit=True) as conn:
                assert conn.execute(
                    "SELECT last_sequence FROM markos_control.conversations WHERE "
                    "conversation_id=%s",
                    (conv,),
                ).fetchone() == (0,)
                conn.execute(
                    sql.SQL("DROP TRIGGER test_fault ON markos_control.{}").format(
                        sql.Identifier(fault_table)
                    )
                )
                conn.execute("DROP FUNCTION markos_control.test_fault()")
            assert (await store.stage_message(ctx, OWN, request(conv))).sequence == 1

    asyncio.run(scenario())


def test_conversation_sequence_order_and_actor_scoped_operation_keys(
    control: dict[str, Any],
) -> None:
    async def scenario() -> None:
        async with ControlStore.connect(control["gateway"]) as store:
            ctx = await session(store)
            operator = await session(store, "operator")
            conv = await prepare(store, ctx)
            rows = await asyncio.gather(
                *(
                    store.stage_message(ctx, OWN, request(conv, key=f"ordered:{i}"))
                    for i in range(5)
                )
            )
            assert {r.sequence for r in rows} == set(range(1, 6))
            sixth = await store.stage_message(operator, OWN, request(conv, key="ordered:0"))
            assert sixth.sequence == 6
            page = await store.read_messages(ctx, OWN, "personal", conv, after=2, limit=2)
            assert [m.sequence for m in page] == [3, 4]

    asyncio.run(scenario())


def test_configuration_and_agent_expected_revision_races(control: dict[str, Any]) -> None:
    async def scenario() -> None:
        async with ControlStore.connect(control["gateway"]) as store:
            ctx = await session(store)
            await prepare(store, ctx)
            current = await store.read_configuration(ctx, OWN, "personal")
            assert current
            new = ConfigRevision(
                schema_version=1,
                owner_id=OWN,
                domain="personal",
                revision=2,
                previous_revision=1,
                agent=current.agent,
                reason="Concurrent update",
            )
            results = await asyncio.gather(
                *(store.save_configuration(ctx, new) for _ in range(2)), return_exceptions=True
            )
            assert sum(isinstance(r, ControlConflict) for r in results) == 1
            state = AgentStateRequest(
                schema_version=1, expected_revision=2, enabled=False, reason="Concurrent disable"
            )
            state_results = await asyncio.gather(
                *(store.set_agent_state(ctx, OWN, "personal", state) for _ in range(2)),
                return_exceptions=True,
            )
            assert sum(isinstance(r, ControlConflict) for r in state_results) == 1
            with psycopg.connect(control["migration"]) as conn:
                assert conn.execute(
                    "SELECT array_agg(revision ORDER BY revision) FROM markos_control.agent_history"
                ).fetchone() == ([1, 2, 3],)
                assert conn.execute(
                    "SELECT array_agg(revision ORDER BY revision) FROM "
                    "markos_control.configuration_history"
                ).fetchone() == ([1, 2],)

    asyncio.run(scenario())


def test_disable_and_stale_configuration_block_new_staging(control: dict[str, Any]) -> None:
    async def scenario() -> None:
        async with ControlStore.connect(control["gateway"]) as store:
            ctx = await session(store)
            conv = await prepare(store, ctx)
            first = await store.stage_message(ctx, OWN, request(conv))
            await store.set_agent_state(
                ctx,
                OWN,
                "personal",
                AgentStateRequest(
                    schema_version=1, expected_revision=2, enabled=False, reason="Disable"
                ),
            )
            assert await store.stage_message(ctx, OWN, request(conv)) == first
            with pytest.raises(ControlConflict):
                await store.stage_message(ctx, OWN, request(conv, key="new:disabled", revision=3))
            await store.set_agent_state(
                ctx,
                OWN,
                "personal",
                AgentStateRequest(
                    schema_version=1, expected_revision=3, enabled=True, reason="Re-enable"
                ),
            )
            with pytest.raises(ControlConflict):
                await store.stage_message(
                    ctx, OWN, request(conv, key="new:stale-config", revision=4)
                )

    asyncio.run(scenario())


def test_staging_scope_budget_and_cross_scope_isolation(control: dict[str, Any]) -> None:
    async def scenario() -> None:
        async with ControlStore.connect(control["gateway"]) as store:
            ctx = await session(store)
            conv = await prepare(store, ctx)
            for i in range(10):
                await store.stage_message(ctx, OWN, request(conv, key=f"bounded:{i}"))
            with pytest.raises(ControlLimited):
                await store.stage_message(ctx, OWN, request(conv, key="bounded:11"))
            research = await prepare(store, ctx, domain="research")
            assert (
                await store.stage_message(ctx, OWN, request(research, domain="research"))
            ).state == "held"
            assert len(await store.read_activity(ctx, OWN, "personal")) == 10
            assert len(await store.read_activity(ctx, OWN, "research")) == 1

    asyncio.run(scenario())


def test_restore_generation_invalidates_sessions_preserves_history_and_m0_fences(
    control: dict[str, Any],
) -> None:
    async def scenario() -> None:
        async with ControlStore.connect(control["gateway"]) as store:
            ctx = await session(store)
            conv = await prepare(store, ctx)
            staged = await store.stage_message(ctx, OWN, request(conv))
            async with DurableStore.connect(control["publisher"]) as runtime:
                await runtime.append(event())
                old = (await runtime.claim_outbox())[0]
                with psycopg.connect(control["migration"], autocommit=True) as conn:
                    conn.execute("SELECT markos_runtime.fence_restored_state()")
                assert not await runtime.record_publication(
                    old.event_id, old.fence, epoch=old.epoch
                )
            with pytest.raises(ControlDenied):
                await store.read_messages(ctx, OWN, "personal", conv)
            fresh = await session(store)
            assert (await store.read_messages(fresh, OWN, "personal", conv))[
                0
            ].message_id == staged.message_id
            with psycopg.connect(control["migration"]) as conn:
                assert conn.execute(
                    "SELECT epoch<>markos_runtime.current_epoch() FROM "
                    "markos_control.message_intents"
                ).fetchone() == (True,)

    asyncio.run(scenario())


def test_migration_upgrade_preserves_populated_m0_and_checksums(
    control: dict[str, Any], tmp_path: Path
) -> None:
    async def scenario() -> None:
        async with DurableStore.connect(control["publisher"]) as runtime:
            await runtime.append(event())
        with psycopg.connect(control["migration"], autocommit=True) as conn:
            conn.execute("DROP SCHEMA markos_control CASCADE")
            conn.execute("DELETE FROM markos_runtime.schema_migrations WHERE version=6")
        directory = Path(__file__).resolve().parents[4] / "infrastructure/migrations"
        async with await psycopg.AsyncConnection.connect(
            control["migration"], autocommit=True, row_factory=dict_row
        ) as conn:
            await migrate(conn, directory)
            await migrate(conn, directory)
        with psycopg.connect(control["migration"]) as conn:
            assert conn.execute("SELECT count(*) FROM markos_runtime.events").fetchone() == (1,)
            assert conn.execute(
                "SELECT count(*) FROM markos_runtime.schema_migrations"
            ).fetchone() == (6,)

    asyncio.run(scenario())


def test_rotation_is_atomic_and_refresh_cannot_extend_absolute_lifetime(
    control: dict[str, Any],
) -> None:
    async def scenario() -> None:
        async with ControlStore.connect(control["gateway"]) as store:
            old = await session(store)
            new_hash = hashlib.sha256(uuid4().bytes).hexdigest()
            renewed = await store.rotate_session(
                old,
                issuer="synthetic-issuer",
                subject="both",
                provider_session_id=uuid4(),
                lookup_hash=new_hash,
                kind="browser",
                provider_expires_at=datetime.now(UTC) + timedelta(hours=1),
            )
            with pytest.raises(ControlDenied):
                await store.read_agents(old, OWN, "personal")
            assert await store.read_agents(renewed.context, OWN, "personal") == ()
            refreshed = await store.refresh_session(
                renewed.context, datetime.now(UTC) + timedelta(days=1)
            )
            with psycopg.connect(control["migration"]) as conn:
                row = conn.execute(
                    "SELECT expires_at-created_at FROM markos_control.sessions WHERE session_id=%s",
                    (refreshed.context.session_id,),
                ).fetchone()
                assert row == (timedelta(hours=8),)
            cli = await session(store, "operator", kind="cli")
            with pytest.raises(ControlDenied):
                await store.refresh_session(cli, datetime.now(UTC) + timedelta(hours=1))
            # A uniqueness failure must not revoke the existing session.
            with pytest.raises(ControlConflict):
                await store.rotate_session(
                    renewed.context,
                    issuer="synthetic-issuer",
                    subject="both",
                    provider_session_id=uuid4(),
                    lookup_hash=new_hash,
                    kind="browser",
                    provider_expires_at=datetime.now(UTC) + timedelta(hours=1),
                )
            assert await store.read_agents(renewed.context, OWN, "personal") == ()
            with pytest.raises(ControlDenied):
                await store.rotate_session(
                    renewed.context,
                    issuer="synthetic-issuer",
                    subject="administrator",
                    provider_session_id=uuid4(),
                    lookup_hash=hashlib.sha256(uuid4().bytes).hexdigest(),
                    kind="browser",
                    provider_expires_at=datetime.now(UTC) + timedelta(hours=1),
                )

    asyncio.run(scenario())


@pytest.mark.parametrize("mutation", ["tool", "unknown-field", "scope", "version", "enabled"])
def test_sql_registry_functions_independently_reject_bypassed_python_validation(
    control: dict[str, Any], mutation: str
) -> None:
    async def scenario() -> None:
        async with ControlStore.connect(control["gateway"]) as store:
            ctx = await session(store)
            agent = AgentRevision(
                schema_version=1,
                owner_id=OWN,
                domain="personal",
                agent_id="control-test-agent",
                revision=1,
                description="Synthetic",
            ).model_dump(mode="json")
            if mutation == "tool":
                agent["required_tools"] = ["shell"]
            elif mutation == "unknown-field":
                agent["command"] = "forbidden"
            elif mutation == "scope":
                agent["owner_id"] = OTHER
            elif mutation == "version":
                agent["schema_version"] = True
            elif mutation == "enabled":
                agent["initially_enabled"] = True
            with (
                psycopg.connect(control["gateway"], autocommit=True) as conn,
                pytest.raises(psycopg.Error),
            ):
                conn.execute(
                    "SELECT markos_control.save_agent(%s,%s,%s,'personal',%s,0)",
                    (ctx.session_id, ctx.lookup_hash, OWN, Jsonb(agent)),
                )
            assert await store.read_agents(ctx, OWN, "personal") == ()

    asyncio.run(scenario())


@pytest.mark.parametrize("mutation", ["actor", "oversize", "kind", "domain"])
def test_sql_message_function_rejects_forged_authority_and_oversize(
    control: dict[str, Any], mutation: str
) -> None:
    async def scenario() -> None:
        async with ControlStore.connect(control["gateway"]) as store:
            ctx = await session(store)
            conv = await prepare(store, ctx)
            value = request(conv).model_dump(mode="json")
            if mutation == "actor":
                value["actor_id"] = str(control["principals"]["administrator"])
            elif mutation == "oversize":
                value["text"] = "é" * 8193
            elif mutation == "kind":
                value["agent_id"] = "shell"
            elif mutation == "domain":
                value["domain"] = "research"
            before = counts(control)
            with (
                psycopg.connect(control["gateway"], autocommit=True) as conn,
                pytest.raises(psycopg.Error),
            ):
                conn.execute(
                    "SELECT markos_control.stage_message(%s,%s,%s,'personal',%s)",
                    (ctx.session_id, ctx.lookup_hash, OWN, Jsonb(value)),
                )
            assert counts(control) == before

    asyncio.run(scenario())


def test_response_pages_bound_rows_bytes_and_filter_before_pagination(
    control: dict[str, Any],
) -> None:
    async def scenario() -> None:
        async with ControlStore.connect(control["gateway"]) as store:
            ctx = await session(store)
            conv = await prepare(store, ctx)
            with psycopg.connect(control["migration"], autocommit=True) as conn:
                for seq in range(1, 21):
                    conn.execute(
                        "INSERT INTO markos_control.messages(owner_id,domain,conversation_"
                        "id,sequence,body,body_sha256,author_id) "
                        "VALUES(%s,'personal',%s,%s,%s,%s,%s)",
                        (
                            OWN,
                            conv,
                            seq,
                            "é" * 8192,
                            hashlib.sha256(("é" * 8192).encode()).hexdigest(),
                            control["principals"]["both"],
                        ),
                    )
            page = await store.read_messages(ctx, OWN, "personal", conv, limit=100)
            assert 1 <= len(page) < 20
            assert (
                len(
                    json.dumps(
                        [m.model_dump(mode="json") for m in page], ensure_ascii=False
                    ).encode()
                )
                <= 262144
            )
            rest = await store.read_messages(
                ctx, OWN, "personal", conv, after=page[-1].sequence, limit=100
            )
            assert [m.sequence for m in (*page, *rest)] == list(range(1, 21))
            with pytest.raises(StorageError):
                await store.read_messages(ctx, OWN, "personal", conv, limit=101)
            with pytest.raises(StorageError):
                await store.read_activity(ctx, OWN, "personal", after=-1)

    asyncio.run(scenario())


def test_global_session_budget_is_atomic_and_old_generations_do_not_reserve_capacity(
    control: dict[str, Any],
) -> None:
    with psycopg.connect(control["migration"], autocommit=True) as conn:
        for i in range(100):
            principal = uuid4()
            conn.execute(
                "INSERT INTO markos_control.principals VALUES(%s,'synthetic-issuer',%s,false)",
                (principal, f"capacity-{i}"),
            )
            conn.execute(
                "INSERT INTO markos_control.sessions(principal_id,provider_session"
                "_id,lookup_hash,kind,epoch,created_at,last_seen_at,expires_at,pro"
                "vider_expires_at) VALUES(%s,%s,%s,'browser',markos_runtime.curren"
                "t_epoch(),clock_timestamp(),clock_timestamp(),clock_timestamp()+i"
                "nterval '1 hour',clock_timestamp()+interval '1 hour')",
                (principal, uuid4(), hashlib.sha256(uuid4().bytes).hexdigest()),
            )

    async def scenario() -> None:
        async with ControlStore.connect(control["gateway"]) as store:
            with pytest.raises(ControlLimited):
                await session(store)
            with psycopg.connect(control["migration"], autocommit=True) as conn:
                conn.execute("SELECT markos_runtime.fence_restored_state()")
            ctx = await session(store)
            assert await store.read_agents(ctx, OWN, "personal") == ()

    asyncio.run(scenario())


def test_pending_membership_revocation_prevents_later_staging(control: dict[str, Any]) -> None:
    async def scenario() -> None:
        async with ControlStore.connect(control["gateway"]) as store:
            ctx = await session(store)
            conv = await prepare(store, ctx)
            async with await psycopg.AsyncConnection.connect(
                control["migration"], autocommit=True
            ) as conn:
                async with conn.transaction():
                    await conn.execute(
                        "UPDATE markos_control.memberships SET "
                        "roles=ARRAY['viewer'],revision=revision+1 WHERE principal_id=%s "
                        "AND domain='personal'",
                        (control["principals"]["both"],),
                    )
                    waiting = asyncio.create_task(store.stage_message(ctx, OWN, request(conv)))
                    await asyncio.sleep(0.1)
                with pytest.raises(ControlDenied):
                    await waiting
            with psycopg.connect(control["migration"]) as conn:
                assert conn.execute("SELECT count(*) FROM markos_control.messages").fetchone() == (
                    0,
                )

    asyncio.run(scenario())


def test_cross_session_last_slot_admission_race(control: dict[str, Any]) -> None:
    async def scenario() -> None:
        async with (
            ControlStore.connect(control["gateway"]) as first,
            ControlStore.connect(control["gateway"]) as second,
        ):
            admin = await session(first)
            operator = await session(second, "operator")
            conv = await prepare(first, admin)
            for i in range(9):
                await first.stage_message(admin, OWN, request(conv, key=f"race:{i}"))
            results = await asyncio.gather(
                first.stage_message(admin, OWN, request(conv, key="race:first")),
                second.stage_message(operator, OWN, request(conv, key="race:second")),
                return_exceptions=True,
            )
            assert sum(isinstance(r, ControlLimited) for r in results) == 1
            assert len(await first.read_messages(admin, OWN, "personal", conv)) == 10

    asyncio.run(scenario())


def test_global_held_work_budget_is_independent_of_scope_budget(control: dict[str, Any]) -> None:
    async def scenario() -> None:
        async with ControlStore.connect(control["gateway"]) as store:
            ctx = await session(store)
            for i in range(10):
                owner = uuid4()
                with psycopg.connect(control["migration"], autocommit=True) as conn:
                    conn.execute(
                        "INSERT INTO markos_control.memberships "
                        "VALUES(%s,%s,'personal',1,ARRAY['operator','administrator'])",
                        (control["principals"]["both"], owner),
                    )
                conv = await prepare(store, ctx, owner)
                for j in range(10):
                    await store.stage_message(ctx, owner, request(conv, key=f"global:{i}:{j}"))
            own = await prepare(store, ctx)
            before = counts(control)
            with pytest.raises(ControlLimited):
                await store.stage_message(ctx, OWN, request(own))
            assert counts(control) == before

    asyncio.run(scenario())


def test_temporary_table_shadow_cannot_replace_session_or_membership_authority(
    control: dict[str, Any],
) -> None:
    async def scenario() -> None:
        async with ControlStore.connect(control["gateway"]) as store:
            ctx = await session(store)
            with psycopg.connect(control["gateway"], autocommit=True) as conn:
                conn.execute("CREATE TEMP TABLE sessions(session_id uuid,lookup_hash text)")
                conn.execute(
                    "CREATE TEMP TABLE memberships(principal_id uuid,owner_id "
                    "uuid,domain text,roles text[])"
                )
                assert conn.execute(
                    "SELECT markos_control.read_agents(%s,%s,%s,'personal')",
                    (ctx.session_id, ctx.lookup_hash, OWN),
                ).fetchone() == ([],)
                with pytest.raises(psycopg.errors.InsufficientPrivilege):
                    conn.execute(
                        "SELECT markos_control.read_agents(%s,%s,%s,'personal')",
                        (ctx.session_id, ctx.lookup_hash, ELSE),
                    )

    asyncio.run(scenario())
