"""Bounded pure planning and atomic DB schedule/event/outbox transitions."""

import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import NAMESPACE_URL, UUID, uuid5

from psycopg.types.json import Jsonb

from markos_runtime.contracts.events import EventEnvelope, NoopPayload
from markos_runtime.contracts.schedules import ScheduleDefinition
from markos_runtime.store.database import DurableStore, StorageError, StoreIdentity


@dataclass(frozen=True)
class SchedulePlan:
    selected: tuple[datetime, ...]
    next_due: datetime
    covered: int
    skipped: int
    pending: bool


def plan_schedule(definition: ScheduleDefinition, cursor: datetime, now: datetime) -> SchedulePlan:
    if cursor.utcoffset() != timedelta(0) or now.utcoffset() != timedelta(0) or cursor > now:
        raise StorageError("invalid scheduling clock")
    # A single scan covers at most 31 elapsed days, even after years of downtime.
    horizon = min(now, cursor + timedelta(days=31))
    if definition.kind == "interval":
        assert definition.interval_seconds is not None
        step = timedelta(seconds=definition.interval_seconds)
        count = (horizon - cursor) // step + 1
        live_start = max(
            0, -(-(now - timedelta(seconds=definition.max_age_seconds) - cursor) // step)
        )
        live_start = min(count, live_start)
        if definition.missed_policy == "skip":
            latest = cursor + (count - 1) * step
            selected: tuple[datetime, ...] = (
                (latest,) if now - latest <= timedelta(seconds=definition.run_grace_seconds) else ()
            )
            processed = count
        elif definition.missed_policy == "coalesce":
            selected = (cursor + (count - 1) * step,) if live_start < count else ()
            processed = count
        else:
            processed = min(count, live_start + definition.catch_up_limit)
            selected = tuple(cursor + i * step for i in range(live_start, processed))
        next_due = cursor + processed * step
    else:
        times = []
        due = cursor
        while due <= horizon:
            times.append(due)
            due = definition.next_after(due)
        count = len(times)
        live = [t for t in times if t >= now - timedelta(seconds=definition.max_age_seconds)]
        if definition.missed_policy == "skip":
            selected = (
                (times[-1],)
                if now - times[-1] <= timedelta(seconds=definition.run_grace_seconds)
                else ()
            )
            next_due, processed = due, count
        elif definition.missed_policy == "coalesce":
            selected, next_due, processed = tuple(live[-1:]), due, count
        else:
            selected = tuple(live[: definition.catch_up_limit])
            next_due = definition.next_after(selected[-1]) if selected else due
            processed = sum(t < next_due for t in times)
    return SchedulePlan(selected, next_due, processed, processed - len(selected), next_due <= now)


def occurrence_event(
    definition: ScheduleDefinition, due: datetime, identity: StoreIdentity
) -> EventEnvelope:
    # Stable across scheduler replicas/restarts. Owner/domain are part of the namespace.
    key = (
        f"{identity.owner_id}:{identity.domain}:{identity.service_id}:"
        f"{definition.schedule_id}:{definition.version}:{due.isoformat()}"
    )
    ident = uuid5(NAMESPACE_URL, key)
    return EventEnvelope(
        schema_version=1,
        event_id=ident,
        event_type="foundation.noop.requested",
        owner_id=identity.owner_id,
        domain=identity.domain,
        source=identity.service_id,
        occurred_at=due.astimezone(UTC),
        correlation_id=ident,
        causation_id=None,
        trace_id=ident,
        sensitivity="internal",
        operation_key=(
            f"schedule:{definition.schedule_id.hex}:v{definition.version}:"
            f"{int((due - datetime(1970, 1, 1, tzinfo=UTC)) // timedelta(microseconds=1))}"
        ),
        payload=NoopPayload(label="scheduled synthetic noop"),
    )


class Scheduler:
    def __init__(self, store: DurableStore) -> None:
        self.store = store

    async def register(self, definition: ScheduleDefinition) -> None:
        # Revalidate unsafe copies and serialize strict UUID/time values through JSON mode.
        document = ScheduleDefinition.model_validate_json(definition.model_dump_json())
        async with self.store._transaction() as connection:
            await connection.execute(
                "SELECT markos_runtime.register_schedule(%s)",
                (Jsonb(json.loads(document.model_dump_json())),),
            )

    async def set_enabled(self, schedule_id: UUID, version: int, enabled: bool) -> bool:
        if type(version) is not int or type(enabled) is not bool:
            raise StorageError("invalid schedule change")
        async with self.store._transaction() as connection:
            row = await (
                await connection.execute(
                    "SELECT markos_runtime.enable_schedule(%s,%s,%s) AS ok",
                    (schedule_id, version, enabled),
                )
            ).fetchone()
            assert row is not None
            return bool(row["ok"])

    async def scan_once(self, *, limit: int = 10) -> int:
        if type(limit) is not int or not 1 <= limit <= 10:
            raise StorageError("invalid scan budget")
        identity = await self.store.identity()
        count = 0
        # Locks and DB clock survive until atomic occurrence/event/outbox/cursor commit.
        # Only bounded CPU planning happens here; no broker or handler calls.
        async with self.store._transaction() as connection:
            rows = await (
                await connection.execute("SELECT * FROM markos_runtime.due_schedules(%s)", (limit,))
            ).fetchall()
            for row in rows:
                definition = ScheduleDefinition.model_validate_json(json.dumps(row["definition"]))
                plan = plan_schedule(definition, row["next_due"], row["db_now"])
                entries = [
                    {
                        "due_at": due.isoformat(),
                        "wire": occurrence_event(definition, due, identity).to_wire().decode(),
                    }
                    for due in plan.selected
                ]
                await connection.execute(
                    "SELECT markos_runtime.advance_schedule(%s,%s,%s,%s,%s,%s)",
                    (
                        definition.schedule_id,
                        definition.version,
                        row["next_due"],
                        plan.next_due,
                        Jsonb(entries),
                        Jsonb(
                            {
                                "policy": definition.missed_policy,
                                "covered": plan.covered,
                                "skipped": plan.skipped,
                                "created": len(entries),
                                "pending": plan.pending,
                                "age_limit_seconds": definition.max_age_seconds,
                            }
                        ),
                    ),
                )
                count += len(entries)
        return count
