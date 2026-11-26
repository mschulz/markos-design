"""Fixed synthetic conversation executor. No model, tool, file, shell or provider adapter."""

import asyncio
from contextlib import suppress
from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING, Any
from uuid import UUID, uuid4

from markos_runtime.store.database import DurableStore, StorageError

if TYPE_CHECKING:
    from markos_runtime.api.identity import FixtureIssuer


@dataclass(frozen=True)
class ChatClaim:
    task_id: UUID
    event_id: UUID
    fence: int
    attempt: int
    epoch: UUID
    lease_until: datetime
    message_sha256: str
    subject: str = field(repr=False)
    provider_session_id: UUID
    provider_expires_at: datetime


class ConversationWorker:
    def __init__(
        self,
        store: DurableStore,
        issuer: "FixtureIssuer",
        *,
        confirm_synthetic: bool = False,
        timeout_seconds: float = 10,
    ) -> None:
        from markos_runtime.api.identity import FixtureIssuer

        if (
            confirm_synthetic is not True
            or not isinstance(issuer, FixtureIssuer)
            or not 0 < timeout_seconds <= 10
        ):
            raise StorageError("explicit fixed synthetic conversation profile required")
        self.store, self.issuer = store, issuer
        self.executor_id = uuid4()
        self.timeout_seconds = timeout_seconds
        self.running = False
        self.failed = False

    async def _call(self, function: str, *args: object) -> Any:
        # Fixed private callsites only; never an HTTP/model-selected function.
        import psycopg

        async with self.store._transaction() as conn:
            query = psycopg.sql.SQL("SELECT markos_runtime.{}({}) AS value").format(
                psycopg.sql.Identifier(function),
                psycopg.sql.SQL(",").join(psycopg.sql.Placeholder() for _ in args),
            )
            row = await (await conn.execute(query, args)).fetchone()
            assert row is not None
            return row["value"]

    async def claim(self, *, lease_seconds: int = 30) -> ChatClaim | None:
        if type(lease_seconds) is not int or not 1 <= lease_seconds <= 30:
            raise StorageError("invalid conversation lease")
        row = await self._call("claim_chat", self.executor_id, lease_seconds)
        if row is None:
            return None
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

    async def finish(
        self, claim: ChatClaim, reply: str | None, *, failure: str | None = None
    ) -> bool:
        return bool(
            await self._call(
                "finish_chat",
                claim.task_id,
                claim.fence,
                self.executor_id,
                claim.epoch,
                reply,
                failure,
            )
        )

    async def _current(self, claim: ChatClaim) -> bool:
        lease = await self._call("chat_auth_claim")
        if lease is None:
            raise StorageError("identity capacity unavailable")
        try:
            async with asyncio.timeout(5):
                return await self.issuer.session_active(claim.subject, claim.provider_session_id)
        except Exception:
            raise StorageError("fixture authority unavailable") from None
        finally:
            await self._call("chat_auth_release", UUID(str(lease)))

    async def _execute(self, claim: ChatClaim) -> str:
        # Fixed pure operation; no registry lookup or arbitrary executable/plugin injection.
        await asyncio.sleep(0)
        return "Synthetic acknowledgement: " + claim.message_sha256

    async def run_once(self) -> bool:
        if self.running or self.failed:
            raise StorageError("conversation worker unavailable")
        self.running = True
        claim: ChatClaim | None = None
        task: asyncio.Task[str] | None = None
        try:
            claim = await self.claim()
            if claim is None:
                return False
            if not await self._current(claim):
                return await self.finish(claim, None, failure="authority")
            task = asyncio.create_task(self._execute(claim))
            done, _ = await asyncio.wait({task}, timeout=self.timeout_seconds)
            if not done:
                task.cancel()
                await self.finish(claim, None, failure="timeout")
                await self._drain(task)
                return False
            try:
                reply = task.result()
            except Exception:
                return await self.finish(claim, None, failure="execution")
            if not await self._current(claim):
                return await self.finish(claim, None, failure="authority")
            return await self.finish(claim, reply)
        except asyncio.CancelledError:
            # Cancellation during process shutdown leaves a fenced lease for takeover.
            # It never blindly marks a possibly committed completion as failed.
            if task is not None:
                task.cancel()
                await self._drain(task)
            raise
        except (StorageError, TimeoutError):
            if claim is not None:
                with suppress(StorageError):
                    await self.finish(claim, None, failure="dependency")
            return False
        finally:
            if task is not None and not task.done():
                task.cancel()
                await self._drain(task)
            self.running = False

    async def _drain(self, task: asyncio.Task[str]) -> None:
        _, pending = await asyncio.wait({task}, timeout=2)
        if pending:
            self.failed = True
        else:
            with suppress(asyncio.CancelledError, Exception):
                task.result()
