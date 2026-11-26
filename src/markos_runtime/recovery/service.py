"""Scoped recovery/retention API. PostgreSQL owns clocks, holds and authorization."""

from dataclasses import dataclass
from hashlib import sha256
from typing import cast
from uuid import UUID

from markos_runtime.contracts.replay import ReplayRequest
from markos_runtime.store.database import DurableStore, StorageError


@dataclass(frozen=True)
class CleanupDecision:
    event_id: UUID
    action: str


class Recovery:
    def __init__(self, store: DurableStore) -> None:
        self.store = store

    async def observe_synthetic_source(
        self, source_id: UUID, body: bytes, services: tuple[str, ...], *, accessible: bool = True
    ) -> None:
        # A synthetic observation only: hashes caller-provided fixture bytes, stores none.
        # This does not assert a production provider was contacted or authorized.
        if type(body) is not bytes or len(body) > 65536 or type(accessible) is not bool:
            raise StorageError("invalid synthetic observation")
        async with self.store._transaction() as connection:
            await connection.execute(
                "SELECT markos_runtime.observe_source(%s,%s,%s,%s)",
                (source_id, sha256(body).hexdigest(), accessible, list(services)),
            )

    async def approve(self, event_id: UUID, *, seconds: int = 300) -> UUID:
        if type(seconds) is not int or not 1 <= seconds <= 3600:
            raise StorageError("invalid replay permit lifetime")
        async with self.store._transaction() as connection:
            row = await (
                await connection.execute(
                    "SELECT markos_runtime.approve_replay(%s,%s) AS id", (event_id, seconds)
                )
            ).fetchone()
            assert row is not None
            return cast(UUID, row["id"])

    async def request(self, request: ReplayRequest) -> str:
        safe = ReplayRequest.model_validate_json(request.model_dump_json())
        async with self.store._transaction() as connection:
            row = await (
                await connection.execute(
                    "SELECT markos_runtime.request_replay(%s,%s) AS status",
                    (safe.event_id, safe.permit_id),
                )
            ).fetchone()
            assert row is not None
            return str(row["status"])

    async def reconcile_once(self, *, limit: int = 10) -> int:
        if type(limit) is not int or not 1 <= limit <= 10:
            raise StorageError("invalid recovery batch")
        async with self.store._transaction() as connection:
            row = await (
                await connection.execute("SELECT markos_runtime.reconcile(%s) AS count", (limit,))
            ).fetchone()
            assert row is not None
            return int(row["count"])

    async def delete_event(self, event_id: UUID) -> None:
        async with self.store._transaction() as connection:
            await connection.execute("SELECT markos_runtime.delete_event(%s)", (event_id,))

    async def delete_source(self, source_id: UUID) -> None:
        async with self.store._transaction() as connection:
            await connection.execute("SELECT markos_runtime.delete_source(%s)", (source_id,))

    async def resolve_reviews(self, event_id: UUID) -> None:
        async with self.store._transaction() as connection:
            await connection.execute("SELECT markos_runtime.resolve_event_reviews(%s)", (event_id,))

    async def cleanup_once(
        self, *, limit: int = 100, dry_run: bool = True
    ) -> tuple[CleanupDecision, ...]:
        if type(limit) is not int or not 1 <= limit <= 100 or type(dry_run) is not bool:
            raise StorageError("invalid cleanup budget")
        async with self.store._transaction() as connection:
            rows = await (
                await connection.execute(
                    "SELECT * FROM markos_runtime.cleanup_retention(%s,%s)", (limit, dry_run)
                )
            ).fetchall()
            return tuple(CleanupDecision(row["event_id"], row["action"]) for row in rows)
