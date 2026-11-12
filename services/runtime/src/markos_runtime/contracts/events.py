"""Synthetic v1 events and task handoff requests; no storage/transport adapters."""

from datetime import datetime, timedelta
from typing import Annotated, Literal, Self
from uuid import UUID

from pydantic import Field, field_validator, model_validator

from markos_runtime.contracts.base import (
    MAX_EVENT_BYTES,
    ContractError,
    Domain,
    EventType,
    Name,
    OperationKey,
    ValueModel,
    parse_document,
)


class NoopPayload(ValueModel):
    label: str = Field(default="", max_length=MAX_EVENT_BYTES)


class SourceReference(ValueModel):
    """Opaque cloud-source identity; not a URL/path or an access grant."""

    source_id: UUID
    owner_id: UUID
    domain: Domain
    sha256: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]


class EventEnvelope(ValueModel):
    model_config = {
        **ValueModel.model_config,
        "json_schema_extra": {
            "x-max-wire-bytes": MAX_EVENT_BYTES,
            "oneOf": [
                {
                    "properties": {"payload": {"type": "object"}, "payload_ref": {"type": "null"}},
                    "required": ["payload"],
                },
                {
                    "properties": {"payload_ref": {"type": "object"}, "payload": {"type": "null"}},
                    "required": ["payload_ref"],
                },
            ],
        },
    }
    schema_version: Literal[1]
    event_id: UUID
    event_type: EventType
    owner_id: UUID
    domain: Domain
    source: Name
    occurred_at: datetime
    correlation_id: UUID
    causation_id: UUID | None  # Required explicit null for a root event.
    trace_id: UUID
    sensitivity: Literal["public", "internal", "confidential", "restricted"]
    operation_key: OperationKey
    expires_at: datetime | None = None
    payload: NoopPayload | None = None
    payload_ref: SourceReference | None = None

    @field_validator("schema_version", mode="before")
    @classmethod
    def exact_version(cls, value: object) -> object:
        if type(value) is not int:
            raise ValueError("integer schema version required")
        return value

    @field_validator("occurred_at", "expires_at")
    @classmethod
    def utc_timestamp(cls, value: datetime | None) -> datetime | None:
        if value is not None and value.utcoffset() != timedelta(0):
            raise ValueError("timezone-aware UTC required")
        return value

    @model_validator(mode="after")
    def payload_and_time(self) -> Self:
        if (self.payload is None) == (self.payload_ref is None):
            raise ValueError("exactly one payload representation required")
        if self.payload_ref and (self.owner_id, self.domain) != (
            self.payload_ref.owner_id,
            self.payload_ref.domain,
        ):
            raise ValueError("source reference scope mismatch")
        if self.expires_at is not None and self.expires_at <= self.occurred_at:
            raise ValueError("invalid expiry")
        return self

    @classmethod
    def from_wire(cls, body: bytes) -> Self:
        return parse_document(cls, body, MAX_EVENT_BYTES)

    def to_wire(self) -> bytes:
        body = self.model_dump_json().encode("utf-8")
        self.from_wire(body)  # Revalidate even after unsafe model_copy/model_construct use.
        return body

    def require_live(self, now: datetime) -> None:
        if now.utcoffset() != timedelta(0):
            raise ContractError("UTC clock required")
        if self.expires_at is not None and now >= self.expires_at:
            raise ContractError("event expired")


class TaskRequest(ValueModel):
    """Requested durable handoff, not proof that a DB transaction has committed."""

    task_id: UUID
    event_id: UUID
    owner_id: UUID
    domain: Domain
    consumer_id: Name
    event_type: EventType
    operation_key: OperationKey
