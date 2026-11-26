"""Test-only ControlStore calls: direct session and staging SQL without the HTTPS API.

The product reaches these SQL functions only through the API's verified paths.
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Literal
from uuid import UUID

from psycopg.types.json import Jsonb

from markos_runtime.contracts.control import ChatRequest
from markos_runtime.control.store import ControlStore, SessionContext
from markos_runtime.store.database import StorageError


@dataclass(frozen=True)
class OpenedSession:
    context: SessionContext
    principal_id: UUID
    epoch: UUID
    expires_at: datetime


@dataclass(frozen=True)
class StagedMessage:
    message_id: UUID
    event_id: UUID
    sequence: int
    state: Literal["held"]
    agent_revision: int
    config_revision: int


class HarnessStore(ControlStore):
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
