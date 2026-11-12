import json
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import pytest
from pydantic import ValidationError

from markos_runtime.contracts.schedules import ScheduleDefinition
from markos_runtime.execution.scheduler import occurrence_event, plan_schedule
from markos_runtime.store.database import StorageError, StoreIdentity

OWNER = UUID("11111111-1111-4111-8111-111111111111")
START = datetime(2026, 1, 1, tzinfo=UTC)


def definition(**changes: Any) -> ScheduleDefinition:
    doc: dict[str, Any] = dict(
        schema_version=1,
        schedule_id="55555555-5555-4555-8555-555555555555",
        version=1,
        owner_id=str(OWNER),
        domain="personal",
        kind="interval",
        start_at=START.isoformat(),
        timezone="Australia/Brisbane",
        interval_seconds=60,
        missed_policy="catch_up",
        catch_up_limit=2,
    )
    doc.update(changes)
    return ScheduleDefinition.model_validate_json(json.dumps(doc))


@pytest.mark.parametrize(
    "changes",
    [
        dict(schema_version=True),
        dict(version=True),
        dict(interval_seconds=0),
        dict(catch_up_limit=101),
        dict(timezone="not/a-zone"),
        dict(missed_policy="guess"),
        dict(gap="ignore"),
        dict(kind="daily"),
        dict(hour=1),
        dict(max_age_seconds=30),
        dict(start_at="2026-01-01T00:00:00"),
        dict(start_at="2026-01-01T01:00:00+01:00"),
        dict(approval="approved"),
        dict(task="send.email"),
    ],
)
def test_schedule_fails_closed(changes: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        definition(**changes)


def test_catchup_is_bounded_and_retains_overdue_cursor() -> None:
    d = definition()
    first = plan_schedule(d, START, START + timedelta(minutes=5))
    assert first.selected == (START, START + timedelta(minutes=1)) and first.pending
    assert first.covered == 2 and first.skipped == 0
    second = plan_schedule(d, first.next_due, START + timedelta(minutes=5))
    assert second.selected[0] == START + timedelta(minutes=2)


@pytest.mark.parametrize("policy,created,skipped", [("coalesce", 1, 5), ("skip", 1, 5)])
def test_coalesce_and_skip_audit_entire_covered_range(
    policy: str, created: int, skipped: int
) -> None:
    plan = plan_schedule(definition(missed_policy=policy), START, START + timedelta(minutes=5))
    assert len(plan.selected) == created and plan.skipped == skipped and plan.covered == 6
    assert plan.next_due == START + timedelta(minutes=6)
    if plan.selected:
        assert plan.selected[0] == START + timedelta(minutes=5)


def test_age_window_skips_old_occurrences_with_audit_counts() -> None:
    plan = plan_schedule(definition(max_age_seconds=60), START, START + timedelta(minutes=5))
    assert plan.selected == (START + timedelta(minutes=4), START + timedelta(minutes=5))
    assert plan.skipped == 4 and not plan.pending


def test_years_of_debt_is_chunked_without_unbounded_planning() -> None:
    plan = plan_schedule(definition(missed_policy="skip"), START, START + timedelta(days=3650))
    assert plan.covered == 44641 and plan.pending and not plan.selected


@pytest.mark.parametrize(
    "fold,expected",
    [("first", "2026-11-01T05:30:00+00:00"), ("second", "2026-11-01T06:30:00+00:00")],
)
def test_daily_fold_occurs_once_with_explicit_choice(fold: str, expected: str) -> None:
    start = datetime(2026, 10, 31, 5, 30, tzinfo=UTC)
    d = definition(
        kind="daily",
        interval_seconds=None,
        hour=1,
        minute=30,
        fold=fold,
        timezone="America/New_York",
        start_at=start.isoformat(),
    )
    due = d.next_after(start)
    assert due.isoformat() == expected
    assert d.next_after(due) == datetime(2026, 11, 2, 6, 30, tzinfo=UTC)


def test_daily_gap_shifts_forward_then_returns_to_wall_time() -> None:
    start = datetime(2026, 3, 7, 7, 30, tzinfo=UTC)
    d = definition(
        kind="daily",
        interval_seconds=None,
        hour=2,
        minute=30,
        max_age_seconds=259200,
        timezone="America/New_York",
        start_at=start.isoformat(),
    )
    due = d.next_after(start)
    assert due == datetime(2026, 3, 8, 7, 30, tzinfo=UTC)
    assert d.next_after(due) == datetime(2026, 3, 9, 6, 30, tzinfo=UTC)
    plan = plan_schedule(d, start, d.next_after(due))
    assert len(plan.selected) == 2 and plan.pending


def test_interval_is_elapsed_utc_across_dst() -> None:
    start = datetime(2026, 11, 1, 5, 30, tzinfo=UTC)
    d = definition(timezone="America/New_York", interval_seconds=3600, start_at=start.isoformat())
    assert d.next_after(start) == datetime(2026, 11, 1, 6, 30, tzinfo=UTC)


def test_occurrence_identity_survives_restart_and_versions_are_distinct() -> None:
    identity = StoreIdentity("foundation-generator", OWNER, "personal", True, False)
    a = occurrence_event(definition(), START, identity)
    assert a.to_wire() == occurrence_event(definition(), START, identity).to_wire()
    b = occurrence_event(definition(version=2), START, identity)
    assert a.event_id != b.event_id and a.operation_key != b.operation_key


def test_no_future_or_naive_plan_clock() -> None:
    with pytest.raises(StorageError):
        plan_schedule(definition(), START, START - timedelta(seconds=1))
    with pytest.raises(StorageError):
        plan_schedule(definition(), START, datetime(2026, 1, 1))


def test_skip_policy_runs_only_within_explicit_grace() -> None:
    d = definition(missed_policy="skip")
    assert plan_schedule(d, START, START + timedelta(seconds=4)).selected == (START,)
    late = plan_schedule(d, START, START + timedelta(seconds=6))
    assert late.selected == () and late.skipped == 1
