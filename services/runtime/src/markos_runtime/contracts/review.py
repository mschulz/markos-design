"""Versioned operational reference, never original body bytes or an agent task."""

from typing import Annotated, Literal
from uuid import UUID

from pydantic import Field, field_validator

from markos_runtime.contracts.base import Domain, ValueModel


class ReviewReference(ValueModel):
    schema_version: Literal[1]
    review_id: UUID
    owner_id: UUID
    domain: Domain
    body_hash: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    reason: Literal["invalid", "unauthorized", "conflict", "expired", "oversize"]

    @field_validator("body_hash")
    @classmethod
    def digest(cls, value: str) -> str:
        if len(value) != 64 or any(c not in "0123456789abcdef" for c in value):
            raise ValueError("invalid digest")
        return value

    @field_validator("schema_version", mode="before")
    @classmethod
    def version(cls, value: object) -> object:
        if type(value) is not int:
            raise ValueError("integer version required")
        return value
