"""Explicit non-admin control persistence; a trusted future gateway must verify identity first."""

import asyncio
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal, Self
from uuid import UUID

import psycopg
from psycopg.conninfo import conninfo_to_dict
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from psycopg_pool import AsyncConnectionPool, PoolTimeout

from markos_runtime.contracts.base import Domain
from markos_runtime.contracts.control import (
    Activity,
    AgentRevision,
    AgentStateRequest,
    ChatAdmission,
    ChatRequest,
    ChatStatus,
    ConfigRevision,
    Conversation,
    Message,
)
from markos_runtime.store.database import StorageError, _SafeConnection


class ControlDenied(StorageError):
    pass


class ControlNotFound(ControlDenied):
    pass


class ControlConflict(StorageError):
    pass


class ControlLimited(StorageError):
    pass


@dataclass(frozen=True)
class SessionContext:
    session_id: UUID
    lookup_hash: str = field(repr=False)


@dataclass(frozen=True)
class OpenedSession:
    context: SessionContext
    principal_id: UUID
    epoch: UUID
    expires_at: datetime


@dataclass(frozen=True)
class StoredAgent:
    manifest: AgentRevision
    enabled: bool


@dataclass(frozen=True)
class StagedMessage:
    message_id: UUID
    event_id: UUID
    sequence: int
    state: Literal["held"]
    agent_revision: int
    config_revision: int


@dataclass(frozen=True)
class ConversationItem:
    conversation: Conversation
    cursor: int


class ControlStore:
    def __init__(self, pool: AsyncConnectionPool[_SafeConnection]) -> None:
        self._pool = pool

    @classmethod
    @asynccontextmanager
    async def connect(cls, dsn: str, *, max_connections: int = 4) -> AsyncIterator[Self]:
        if type(max_connections) is not int or not 1 <= max_connections <= 4:
            raise StorageError("invalid control connection budget")
        try:
            info = conninfo_to_dict(dsn)
            if not all(info.get(k) for k in ("host", "dbname", "user")):
                raise StorageError("explicit control database target required")
        except psycopg.Error:
            raise StorageError("invalid control database target") from None
        pool: AsyncConnectionPool[_SafeConnection] = AsyncConnectionPool(
            dsn,
            connection_class=_SafeConnection,
            min_size=0,
            max_size=max_connections,
            timeout=5,
            open=False,
            reconnect_timeout=0,
            kwargs={"autocommit": True, "row_factory": dict_row, "connect_timeout": 5},
        )
        try:
            await pool.open()
            store = cls(pool)
            await store._call("gateway")
            yield store
        except (psycopg.Error, PoolTimeout):
            raise StorageError("control database unavailable") from None
        finally:
            await pool.close()

    async def _call(self, function: str, *args: object) -> Any:
        # Function names are fixed private call sites below, never HTTP or model input.
        try:
            async with self._pool.connection() as conn:
                try:
                    async with conn.transaction():
                        await conn.execute("SET LOCAL TIME ZONE 'UTC'")
                        await conn.execute("SET LOCAL statement_timeout='5s'")
                        await conn.execute("SET LOCAL lock_timeout='1s'")
                        query = psycopg.sql.SQL("SELECT markos_control.{}({}) AS value").format(
                            psycopg.sql.Identifier(function),
                            psycopg.sql.SQL(",").join(psycopg.sql.Placeholder() for _ in args),
                        )
                        row = await (await conn.execute(query, args)).fetchone()
                        assert row is not None
                        return row["value"]
                except asyncio.CancelledError:
                    await conn.close()
                    raise
        except (psycopg.Error, PoolTimeout) as error:
            if isinstance(error, psycopg.Error):
                if error.sqlstate == "P0002":
                    raise ControlNotFound("control object unavailable") from None
                if error.sqlstate == "42501":
                    raise ControlDenied("control context or object unavailable") from None
                if error.sqlstate in {"40001", "23505"}:
                    raise ControlConflict("control revision or operation conflict") from None
                if error.sqlstate == "54000":
                    raise ControlLimited("control admission budget exhausted") from None
            raise StorageError("control operation failed") from None

    @staticmethod
    def _context(context: SessionContext, owner: UUID, domain: Domain) -> tuple[object, ...]:
        return context.session_id, context.lookup_hash, owner, domain

    async def open_session(
        self,
        *,
        issuer: str,
        subject: str,
        provider_session_id: UUID,
        lookup_hash: str,
        kind: Literal["browser", "cli"],
        provider_expires_at: datetime,
    ) -> OpenedSession:
        """Persist the future trusted verifier's binding; does NOT verify a JWT/PKCE flow."""
        row = await self._call(
            "open_session",
            issuer,
            subject,
            provider_session_id,
            lookup_hash,
            kind,
            provider_expires_at,
        )
        return OpenedSession(
            SessionContext(UUID(row["session_id"]), lookup_hash),
            UUID(row["principal_id"]),
            UUID(row["epoch"]),
            datetime.fromisoformat(row["expires_at"]),
        )

    async def rotate_session(
        self,
        context: SessionContext,
        *,
        issuer: str,
        subject: str,
        provider_session_id: UUID,
        lookup_hash: str,
        kind: Literal["browser", "cli"],
        provider_expires_at: datetime,
    ) -> OpenedSession:
        row = await self._call(
            "rotate_session",
            context.session_id,
            context.lookup_hash,
            issuer,
            subject,
            provider_session_id,
            lookup_hash,
            kind,
            provider_expires_at,
        )
        return OpenedSession(
            SessionContext(UUID(row["session_id"]), lookup_hash),
            UUID(row["principal_id"]),
            UUID(row["epoch"]),
            datetime.fromisoformat(row["expires_at"]),
        )

    async def refresh_session(
        self, context: SessionContext, provider_expires_at: datetime
    ) -> OpenedSession:
        row = await self._call(
            "refresh_session", context.session_id, context.lookup_hash, provider_expires_at
        )
        return OpenedSession(
            context,
            UUID(row["principal_id"]),
            UUID(row["epoch"]),
            datetime.fromisoformat(row["expires_at"]),
        )

    async def revoke_session(self, context: SessionContext) -> bool:
        return bool(await self._call("revoke_session", context.session_id, context.lookup_hash))

    async def save_agent(
        self, context: SessionContext, agent: AgentRevision, *, expected_revision: int
    ) -> StoredAgent:
        checked = AgentRevision.from_wire(agent.to_wire())
        row = await self._call(
            "save_agent",
            *self._context(context, checked.owner_id, checked.domain),
            Jsonb(checked.model_dump(mode="json")),
            expected_revision,
        )
        return StoredAgent(
            AgentRevision.model_validate_json(json.dumps(row["manifest"])), row["enabled"]
        )

    async def set_agent_state(
        self,
        context: SessionContext,
        owner: UUID,
        domain: Domain,
        request: AgentStateRequest,
    ) -> StoredAgent:
        checked = AgentStateRequest.from_wire(request.to_wire())
        row = await self._call(
            "set_agent_state",
            *self._context(context, owner, domain),
            checked.expected_revision,
            checked.enabled,
            checked.reason,
        )
        return StoredAgent(
            AgentRevision.model_validate_json(json.dumps(row["manifest"])), row["enabled"]
        )

    async def save_configuration(
        self, context: SessionContext, config: ConfigRevision
    ) -> ConfigRevision:
        checked = ConfigRevision.from_wire(config.to_wire())
        row = await self._call(
            "save_configuration",
            *self._context(context, checked.owner_id, checked.domain),
            Jsonb(checked.model_dump(mode="json")),
        )
        return ConfigRevision.model_validate_json(json.dumps(row))

    async def read_configuration(
        self, context: SessionContext, owner: UUID, domain: Domain
    ) -> ConfigRevision | None:
        row = await self._call("read_configuration", *self._context(context, owner, domain))
        return ConfigRevision.model_validate_json(json.dumps(row)) if row is not None else None

    async def read_agents(
        self, context: SessionContext, owner: UUID, domain: Domain
    ) -> tuple[StoredAgent, ...]:
        rows = await self._call("read_agents", *self._context(context, owner, domain))
        return tuple(
            StoredAgent(AgentRevision.model_validate_json(json.dumps(r["manifest"])), r["enabled"])
            for r in rows
        )

    async def create_conversation(
        self,
        context: SessionContext,
        owner: UUID,
        domain: Domain,
        conversation_id: UUID,
        operation_key: str,
    ) -> Conversation:
        row = await self._call(
            "create_conversation",
            *self._context(context, owner, domain),
            conversation_id,
            operation_key,
        )
        return Conversation.model_validate_json(json.dumps(row))

    async def stage_message(
        self, context: SessionContext, owner: UUID, request: ChatRequest
    ) -> StagedMessage:
        checked = ChatRequest.from_wire(request.to_wire())
        row = await self._call(
            "stage_message",
            *self._context(context, owner, checked.domain),
            Jsonb(checked.model_dump(mode="json")),
        )
        if row["state"] != "held":
            raise StorageError("unexpected control staging state")
        return StagedMessage(
            UUID(row["message_id"]),
            UUID(row["event_id"]),
            row["sequence"],
            "held",
            row["agent_revision"],
            row["config_revision"],
        )

    async def read_conversations(
        self,
        context: SessionContext,
        owner: UUID,
        domain: Domain,
        *,
        after: int = 0,
        limit: int = 50,
    ) -> tuple[ConversationItem, ...]:
        rows = await self._call(
            "read_conversations", *self._context(context, owner, domain), after, limit
        )
        result = []
        for row in rows:
            cursor = row.pop("cursor")
            result.append(
                ConversationItem(Conversation.model_validate_json(json.dumps(row)), cursor)
            )
        return tuple(result)

    async def read_messages(
        self,
        context: SessionContext,
        owner: UUID,
        domain: Domain,
        conversation_id: UUID,
        *,
        after: int = 0,
        limit: int = 50,
    ) -> tuple[Message, ...]:
        rows = await self._call(
            "read_messages", *self._context(context, owner, domain), conversation_id, after, limit
        )
        return tuple(Message.model_validate_json(json.dumps(row)) for row in rows)

    async def read_activity(
        self,
        context: SessionContext,
        owner: UUID,
        domain: Domain,
        *,
        after: int = 0,
        limit: int = 50,
    ) -> tuple[Activity, ...]:
        rows = await self._call(
            "read_activity", *self._context(context, owner, domain), after, limit
        )
        return tuple(Activity.model_validate_json(json.dumps(row)) for row in rows)

    async def admit_message(
        self, context: SessionContext, owner: UUID, request: ChatRequest
    ) -> ChatAdmission:
        checked = ChatRequest.from_wire(request.to_wire())
        row = await self._call(
            "admit_message",
            *self._context(context, owner, checked.domain),
            Jsonb(checked.model_dump(mode="json")),
        )
        return ChatAdmission.model_validate_json(json.dumps({"schema_version": 1, **row}))

    async def chat_status(
        self,
        context: SessionContext,
        owner: UUID,
        domain: Domain,
        conversation_id: UUID,
        message_id: UUID,
        *,
        cancel: bool = False,
    ) -> ChatStatus:
        row = await self._call(
            "cancel_chat" if cancel else "chat_status",
            *self._context(context, owner, domain),
            conversation_id,
            message_id,
        )
        return ChatStatus.model_validate_json(json.dumps({"schema_version": 1, **row}))
