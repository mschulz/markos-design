"""Fixed governed conversation worker. Durable waiting owns no execution/network resource."""

import asyncio
import json
import time
from contextlib import suppress
from typing import Any
from uuid import UUID

from psycopg.types.json import Jsonb

from markos_runtime.contracts.control import CallIntent, digest_document
from markos_runtime.contracts.governance import ReviewedFixtureGrant
from markos_runtime.control.conversation import ChatClaim, ConversationWorker
from markos_runtime.store.database import StorageError
from markos_runtime.tools.transport import FixtureTransport, ToolProtocolError


class GovernedConversationWorker(ConversationWorker):
    def __init__(self, *args: Any, transport: FixtureTransport, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        if not isinstance(transport, FixtureTransport):
            raise StorageError("fixed synthetic fixture transport required")
        self.transport = transport

    async def claim(self, *, lease_seconds: int = 30) -> ChatClaim | None:
        # Reuse the checked decoder, but select only this separately enabled durable profile.
        if type(lease_seconds) is not int or not 1 <= lease_seconds <= 30:
            raise StorageError("invalid conversation lease")
        row = await self._call("claim_governed_chat", self.executor_id, lease_seconds)
        if row is None:
            return None
        from datetime import datetime

        return ChatClaim(
            UUID(row["task_id"]),
            UUID(row["event_id"]),
            row["fence"],
            row["attempt"],
            UUID(row["epoch"]),
            datetime.fromisoformat(row["lease_until"]),
            row["message_sha256"],
            row["subject"],
            UUID(row["provider_session_id"]),
            datetime.fromisoformat(row["provider_expires_at"]),
        )

    def _identity(self, claim: ChatClaim) -> tuple[object, ...]:
        return claim.task_id, claim.fence, self.executor_id, claim.epoch

    async def run_once(self) -> bool:
        if self.running or self.failed:
            raise StorageError("governed worker unavailable")
        self.running = True
        claim, dispatch = None, None
        try:
            async with asyncio.timeout(self.timeout_seconds):
                deadline = time.monotonic() + self.timeout_seconds
                claim = await self.claim()
                if claim is None:
                    return False
                if not await self._current(claim):
                    return await self.finish(claim, None, failure="authority")
                context = await self._call("tool_context", *self._identity(claim))
                if context["state"] == "queued":
                    await self._call("wait_tool", *self._identity(claim))
                    return True
                if context["state"] == "succeeded":
                    return await self.finish(
                        claim, "Synthetic acknowledgement: " + claim.message_sha256
                    )
                intent = CallIntent.from_wire(json.dumps(context["intent"]).encode())
                grant = ReviewedFixtureGrant.from_wire(json.dumps(context["grant"]).encode())
                if (
                    intent.intent_sha256() != context["intent_sha256"]
                    or intent.epoch != claim.epoch
                    or grant.owner_id != self.transport.config.owner_id
                    or grant.domain != self.transport.config.domain
                    or grant.endpoint != self.transport.config.endpoint
                ):
                    raise ToolProtocolError()
                lease = await self._call("chat_auth_claim")
                if lease is None:
                    raise StorageError("control request capacity unavailable")
                try:
                    await self.transport.inspect(min(deadline, time.monotonic() + 5))
                except ToolProtocolError:
                    await self._call("invalidate_tool_grant", *self._identity(claim))
                    return False
                finally:
                    await self._call("chat_auth_release", UUID(str(lease)))
                if not await self._current(claim):
                    return await self.finish(claim, None, failure="authority")
                dispatch = await self._call(
                    "dispatch_tool",
                    *self._identity(claim),
                    intent.intent_id,
                    intent.intent_sha256(),
                    grant.endpoint,
                )
                if "denied" in dispatch:
                    if dispatch["denied"] == "budget":
                        await self._call("defer_tool", *self._identity(claim))
                    else:
                        await self.finish(claim, None, failure="authority")
                    return False
                result = await self.transport.call(
                    intent.arguments.label, UUID(dispatch["operation_id"]), deadline
                )
                if not await self._current(claim):
                    raise ToolProtocolError()
                saved = await self._call(
                    "finish_tool",
                    *self._identity(claim),
                    UUID(dispatch["dispatch_id"]),
                    Jsonb(result.model_dump(mode="json")),
                    digest_document(result.model_dump(mode="json")),
                    False,
                )
                if not saved:
                    return False
                return await self.finish(
                    claim, "Synthetic acknowledgement: " + claim.message_sha256
                )
        except asyncio.CancelledError:
            # A committed dispatch remains unresolved if shutdown interrupts result recording.
            raise
        except (StorageError, ToolProtocolError, TimeoutError):
            if claim is not None and dispatch and "dispatch_id" in dispatch:
                with suppress(StorageError):
                    await self._call(
                        "finish_tool",
                        *self._identity(claim),
                        UUID(dispatch["dispatch_id"]),
                        None,
                        None,
                        True,
                    )
            return False
        finally:
            self.running = False
