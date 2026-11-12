"""Scoped transactional storage primitives; no broker, worker loop or implicit DSN."""

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from hashlib import sha256
from typing import Any, Literal, Self, cast
from uuid import UUID

import psycopg
from psycopg.conninfo import conninfo_to_dict
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool, PoolTimeout

from markos_runtime.contracts.base import Domain
from markos_runtime.contracts.events import EventEnvelope
from markos_runtime.contracts.review import ReviewReference

Row = dict[str, Any]
Status = Literal["created", "duplicate", "conflict", "expired"]


class StorageError(ValueError):
    """Stable public storage failure, never a DSN, SQL statement or payload."""


class StorageRejected(StorageError):
    """Permanent authenticated handoff rejection, eligible for durable review."""


@dataclass(frozen=True)
class Receipt:
    status: Status
    task_id: UUID | None = None


@dataclass(frozen=True)
class OutboxClaim:
    event_id: UUID
    event: EventEnvelope
    fence: int
    attempts: int = 0
    consumers: tuple[str, ...] = ()


@dataclass(frozen=True)
class StoreIdentity:
    service_id: str
    owner_id: UUID
    domain: Domain
    can_publish: bool
    can_consume: bool


@dataclass(frozen=True)
class ReviewClaim:
    reference: ReviewReference
    fence: int
    attempts: int


class _SafeConnection(psycopg.AsyncConnection[Row]):
    @classmethod
    async def connect(cls, conninfo: str = "", **kwargs: Any) -> Self:
        try:
            return await super().connect(conninfo, **kwargs)
        except psycopg.Error:
            # Pool background workers log connection errors: redact at that boundary.
            raise psycopg.OperationalError("database connection unavailable") from None


class DurableStore:
    def __init__(self, pool: AsyncConnectionPool[_SafeConnection]) -> None:
        self._pool = pool

    @classmethod
    @asynccontextmanager
    async def connect(cls, dsn: str, *, max_connections: int = 4) -> AsyncIterator[Self]:
        """Explicit opt-in only; preflight does not import or call this method."""
        if type(max_connections) is not int or not 1 <= max_connections <= 4:
            raise StorageError("invalid storage connection budget")
        try:
            settings = conninfo_to_dict(dsn)
            if not all(settings.get(key) for key in ("host", "dbname", "user")):
                raise StorageError("explicit database host, name and login required")
        except psycopg.Error:
            raise StorageError("invalid explicit database configuration") from None
        pool: AsyncConnectionPool[_SafeConnection] = AsyncConnectionPool(
            dsn,
            connection_class=_SafeConnection,
            min_size=0,
            max_size=max_connections,
            timeout=5,
            open=False,
            kwargs={"autocommit": True, "row_factory": dict_row, "connect_timeout": 5},
            # Fail promptly; _SafeConnection redacts background connection errors.
            reconnect_timeout=0,
        )
        try:
            await pool.open()
            async with pool.connection() as connection:
                cursor = await connection.execute(
                    "SELECT r.rolsuper OR r.rolbypassrls OR r.rolcreaterole OR "
                    "pg_has_role(current_user,n.nspowner,'MEMBER') AS unsafe "
                    "FROM pg_roles r CROSS JOIN pg_namespace n "
                    "WHERE r.rolname=session_user AND n.nspname='markos_runtime'"
                )
                role = await cursor.fetchone()
                if role is None or role["unsafe"]:
                    raise StorageError("non-admin storage identity required")
            yield cls(pool)
        except (psycopg.Error, PoolTimeout):
            raise StorageError("storage operation failed") from None
        finally:
            await pool.close()

    @asynccontextmanager
    async def _transaction(self) -> AsyncIterator[psycopg.AsyncConnection[Row]]:
        try:
            async with self._pool.connection() as connection:
                try:
                    async with connection.transaction():
                        await connection.execute("SET LOCAL statement_timeout='5s'")
                        await connection.execute("SET LOCAL lock_timeout='1s'")
                        yield connection
                except asyncio.CancelledError:
                    # A timed-out handoff must not return a suspect commit connection to the pool.
                    await connection.close()
                    raise
        except (psycopg.Error, PoolTimeout) as error:
            if isinstance(error, psycopg.Error) and error.sqlstate == "M0001":
                raise StorageRejected("handoff not authorized or registered") from None
            raise StorageError("storage operation failed") from None

    async def append(self, event: EventEnvelope) -> Receipt:
        """Atomically register event, intended consumers and outbox, or record conflict."""
        body = event.to_wire()
        async with self._transaction() as connection:
            cursor = await connection.execute(
                "SELECT markos_runtime.append_event(%s) AS status", (body,)
            )
            row = await cursor.fetchone()
            assert row is not None
            return Receipt(cast(Status, row["status"]))

    async def handoff(self, event: EventEnvelope) -> Receipt:
        """A receipt is returned after commit; it is not a broker acknowledgement."""
        digest = sha256(event.to_wire()).hexdigest()
        async with self._transaction() as connection:
            cursor = await connection.execute(
                "SELECT * FROM markos_runtime.handoff(%s,%s)", (event.event_id, digest)
            )
            row = await cursor.fetchone()
            assert row is not None
            return Receipt(cast(Status, row["status"]), row["task_id"])

    async def claim_outbox(
        self, *, limit: int = 10, lease_seconds: int = 60
    ) -> tuple[OutboxClaim, ...]:
        if type(limit) is not int or type(lease_seconds) is not int:
            raise StorageError("invalid claim budget")
        async with self._transaction() as connection:
            cursor = await connection.execute(
                "SELECT * FROM markos_runtime.claim_outbox(%s,%s)", (limit, lease_seconds)
            )
            rows = await cursor.fetchall()
            return tuple(
                OutboxClaim(
                    row["event_id"],
                    EventEnvelope.from_wire(bytes(row["wire"])),
                    row["fence"],
                    row["attempts"],
                    tuple(row["consumers"]),
                )
                for row in rows
            )

    async def record_publication(self, event_id: UUID, fence: int) -> bool:
        """Future publisher calls only after positive confirm/no return; no AMQP here."""
        if type(fence) is not int or fence < 1:
            raise StorageError("invalid publication fence")
        async with self._transaction() as connection:
            cursor = await connection.execute(
                "SELECT markos_runtime.record_publication(%s,%s) AS recorded", (event_id, fence)
            )
            row = await cursor.fetchone()
            assert row is not None
            return bool(row["recorded"])

    async def identity(self) -> StoreIdentity:
        async with self._transaction() as connection:
            row = await (
                await connection.execute("SELECT * FROM markos_runtime.runtime_identity()")
            ).fetchone()
            if row is None:
                raise StorageError("unregistered storage identity")
            return StoreIdentity(
                row["service_id"],
                row["owner_id"],
                row["domain"],
                row["can_publish"],
                row["can_consume"],
            )

    async def retry_claim(self, claim: OutboxClaim, delay: int, code: str) -> bool:
        async with self._transaction() as connection:
            row = await (
                await connection.execute(
                    "SELECT markos_runtime.retry_outbox(%s,%s,%s,%s) AS ok",
                    (claim.event_id, claim.fence, delay, code),
                )
            ).fetchone()
            assert row is not None
            return bool(row["ok"])

    async def review_delivery(self, body: bytes, reason: str) -> UUID:
        async with self._transaction() as connection:
            row = await (
                await connection.execute(
                    "SELECT markos_runtime.review_delivery(%s,%s,%s) AS id",
                    (sha256(body).hexdigest(), reason, len(body)),
                )
            ).fetchone()
            assert row is not None
            return cast(UUID, row["id"])

    async def claim_reviews(self, *, limit: int = 10) -> tuple[ReviewClaim, ...]:
        async with self._transaction() as connection:
            rows = await (
                await connection.execute(
                    "SELECT * FROM markos_runtime.claim_reviews(%s,60)", (limit,)
                )
            ).fetchall()
            return tuple(
                ReviewClaim(
                    ReviewReference(
                        schema_version=1,
                        review_id=row["review_id"],
                        owner_id=row["owner_id"],
                        domain=row["domain"],
                        body_hash=row["body_hash"],
                        reason=row["reason"],
                    ),
                    row["fence"],
                    row["attempts"],
                )
                for row in rows
            )

    async def finish_review(self, claim: ReviewClaim, delay: int | None = None) -> bool:
        async with self._transaction() as connection:
            row = await (
                await connection.execute(
                    "SELECT markos_runtime.finish_review(%s,%s,%s) AS ok",
                    (claim.reference.review_id, claim.fence, delay),
                )
            ).fetchone()
            assert row is not None
            return bool(row["ok"])

    async def hold_for_review(self, claim: OutboxClaim, reason: str) -> bool:
        async with self._transaction() as connection:
            row = await (
                await connection.execute(
                    "SELECT markos_runtime.hold_for_review(%s,%s,%s) AS ok",
                    (claim.event_id, claim.fence, reason),
                )
            ).fetchone()
            assert row is not None
            return bool(row["ok"])
