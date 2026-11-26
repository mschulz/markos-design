"""Replay carries identity and a permit reference, never effects or replacement bytes."""

from typing import Literal
from uuid import UUID

from pydantic import field_validator

from markos_runtime.contracts.base import ValueModel, exact_int


class ReplayRequest(ValueModel):
    schema_version: Literal[1]
    event_id: UUID
    permit_id: UUID | None = None
    suppress_effects: Literal[True] = True

    _integer_version = field_validator("schema_version", mode="before")(exact_int)

    @field_validator("suppress_effects", mode="before")
    @classmethod
    def effects_disabled(cls, value: object) -> object:
        if value is not True:
            raise ValueError("replay effects must remain suppressed")
        return value
