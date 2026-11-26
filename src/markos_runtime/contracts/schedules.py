"""Synthetic interval/daily schedules with explicit elapsed/wall-clock semantics."""

from datetime import UTC, datetime, timedelta
from typing import Literal, Self
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import Field, field_validator, model_validator

from markos_runtime.contracts.base import Domain, ValueModel


class ScheduleDefinition(ValueModel):
    schema_version: Literal[1]
    schedule_id: UUID
    version: int = Field(ge=1, le=2_147_483_647)
    owner_id: UUID
    domain: Domain
    kind: Literal["interval", "daily"]
    start_at: datetime
    timezone: str = Field(min_length=1, max_length=64)
    interval_seconds: int | None = Field(default=None, ge=60, le=2_678_400)
    hour: int | None = Field(default=None, ge=0, le=23)
    minute: int | None = Field(default=None, ge=0, le=59)
    fold: Literal["first", "second"] = "first"
    gap: Literal["shift_forward"] = "shift_forward"
    missed_policy: Literal["catch_up", "coalesce", "skip"]
    catch_up_limit: int = Field(default=10, ge=1, le=100)
    run_grace_seconds: int = Field(default=5, ge=0, le=59)
    max_age_seconds: int = Field(default=86400, ge=60, le=2_592_000)

    @field_validator("schema_version", mode="before")
    @classmethod
    def integer_version(cls, value: object) -> object:
        if type(value) is not int:
            raise ValueError("integer schema version required")
        return value

    @field_validator("timezone")
    @classmethod
    def iana_zone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError):
            raise ValueError("available IANA timezone required") from None
        return value

    @model_validator(mode="after")
    def valid_clock(self) -> Self:
        if self.start_at.utcoffset() != timedelta(0):
            raise ValueError("UTC schedule start required")
        if self.kind == "interval":
            if self.interval_seconds is None or self.hour is not None or self.minute is not None:
                raise ValueError("interval shape required")
        elif self.interval_seconds is not None or self.hour is None or self.minute is None:
            raise ValueError("daily shape required")
        elif self.wall_time(self.start_at) != self.start_at:
            raise ValueError("daily start must match wall-clock policy")
        return self

    def wall_time(self, day: datetime) -> datetime:
        """First/second fold; nonexistent time shifts forward by the DST gap."""
        zone = ZoneInfo(self.timezone)
        local = day.astimezone(zone)
        naive = datetime(local.year, local.month, local.day, self.hour or 0, self.minute or 0)
        first = naive.replace(tzinfo=zone, fold=0).astimezone(UTC)
        second = naive.replace(tzinfo=zone, fold=1).astimezone(UTC)
        valid = [t for t in (first, second) if t.astimezone(zone).replace(tzinfo=None) == naive]
        if valid:
            return min(valid) if self.fold == "first" else max(valid)
        return max(first, second)  # Round trip moves forward by the nonexistent gap.

    def next_after(self, due: datetime) -> datetime:
        if self.kind == "interval":
            assert self.interval_seconds is not None
            return due + timedelta(seconds=self.interval_seconds)
        # Noon is an unambiguous date carrier, including a spring-forward gap at the job time.
        local = due.astimezone(ZoneInfo(self.timezone))
        tomorrow = (local + timedelta(days=1)).replace(hour=12, minute=0, second=0, microsecond=0)
        return self.wall_time(tomorrow)
