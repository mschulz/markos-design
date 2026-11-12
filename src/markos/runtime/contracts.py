"""Version-one synthetic event contract and explicit trust-boundary checks."""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from typing import Annotated, Literal, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

MAX_EVENT_BYTES = 65_536
Domain = Literal["personal", "research", "work"]
Environment = Literal["test", "development", "production"]
EventType = Literal["foundation.noop.requested"]
Identifier = Annotated[str, Field(pattern=r"^[a-z][a-z0-9]*(?:[._-][a-z0-9]+)*$", max_length=64)]
SourceReference = Annotated[
    str, Field(pattern=r"^source:[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
]


class ContractError(ValueError):
    """A redacted event or authorization error; never embeds a payload."""


class ContractModel(BaseModel):
    model_config = ConfigDict(
        strict=True, frozen=True, extra="forbid", hide_input_in_errors=True, allow_inf_nan=False,
        validate_default=True,
    )


class ServiceScope(ContractModel):
    """A trusted deployment identity, not authority supplied by an event."""

    owner_id: UUID
    domain: Domain
    service_id: Identifier

    @field_validator("owner_id")
    @classmethod
    def non_nil_owner(cls, value: UUID) -> UUID:
        if value.int == 0:
            raise ValueError("owner_id must not be nil")
        return value


class NoopPayload(ContractModel):
    """M0's only inline payload; real agent payloads need separate reviewed schemas."""

    operation_key: Annotated[
        str, Field(pattern=r"^[a-z0-9][a-z0-9._:-]*$", min_length=1, max_length=128)
    ]
    label: str = Field(default="", max_length=MAX_EVENT_BYTES)


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _reject_constant(value: str) -> object:
    raise ValueError("non-finite JSON number")


def validate_json_document(body: bytes, limit: int) -> None:
    """Bound raw input and reject duplicate keys/non-standard JSON before Pydantic."""
    if len(body) > limit:
        raise ContractError("JSON document exceeds byte limit")
    try:
        json.loads(body.decode("utf-8"), object_pairs_hook=_unique_object,
                   parse_constant=_reject_constant)
    except (ValueError, UnicodeError, RecursionError):
        raise ContractError("invalid JSON document") from None


class EventEnvelope(ContractModel):
    """Strict JSON v1 envelope; parse untrusted deliveries with from_bytes()."""

    model_config = ConfigDict(
        **ContractModel.model_config,
        json_schema_extra={
            "x-max-wire-bytes": MAX_EVENT_BYTES,
            "oneOf": [
                {"required": ["payload"], "properties": {
                    "payload": {"not": {"type": "null"}}, "payload_ref": {"type": "null"}}},
                {"required": ["payload_ref"], "properties": {
                    "payload_ref": {"not": {"type": "null"}}, "payload": {"type": "null"}}},
            ],
        },
    )

    event_id: UUID
    schema_version: Literal[1]
    event_type: EventType
    owner_id: UUID
    domain: Domain
    source: Identifier
    occurred_at: datetime
    correlation_id: UUID
    causation_id: UUID | None
    trace_id: UUID
    sensitivity: Literal["public", "internal", "confidential", "restricted"]
    expires_at: datetime | None = None
    payload: NoopPayload | None = None
    payload_ref: SourceReference | None = None

    @field_validator("schema_version", mode="before")
    @classmethod
    def integer_version(cls, value: object) -> object:
        if type(value) is not int:
            raise ValueError("schema_version must be an integer")
        return value

    @field_validator("event_id", "owner_id", "correlation_id", "trace_id")
    @classmethod
    def non_nil_id(cls, value: UUID) -> UUID:
        if value.int == 0:
            raise ValueError("identifier must not be nil")
        return value

    @field_validator("occurred_at", "expires_at")
    @classmethod
    def utc_only(cls, value: datetime | None) -> datetime | None:
        if value is not None and value.utcoffset() != timedelta(0):
            raise ValueError("timestamp must be timezone-aware UTC")
        return value

    @model_validator(mode="after")
    def consistent_payload_and_expiry(self) -> Self:
        if (self.payload is None) == (self.payload_ref is None):
            raise ValueError("exactly one payload or payload_ref is required")
        if self.expires_at is not None and self.expires_at <= self.occurred_at:
            raise ValueError("expiry must be after occurrence")
        return self

    @classmethod
    def from_bytes(cls, body: bytes) -> Self:
        validate_json_document(body, MAX_EVENT_BYTES)
        try:
            return cls.model_validate_json(body)
        except ValidationError:
            raise ContractError("invalid event envelope") from None

    def to_bytes(self) -> bytes:
        """Validate the final serialized body, including UTF-8 size, at publication."""
        body = self.model_dump_json(exclude_none=True).encode("utf-8")
        # Root causation is explicitly null, rather than omitted.
        document = json.loads(body)
        document["causation_id"] = str(self.causation_id) if self.causation_id else None
        body = json.dumps(document, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        self.from_bytes(body)
        return body

    @property
    def exchange(self) -> str:
        return f"markos.{self.domain}.events"

    @property
    def routing_key(self) -> str:
        return "v1.foundation.noop-requested"


def validate_publisher_scope(event: EventEnvelope, trusted: ServiceScope) -> None:
    if (event.owner_id, event.domain, event.source) != (
        trusted.owner_id, trusted.domain, trusted.service_id
    ):
        raise ContractError("publisher scope mismatch")


def require_unexpired(event: EventEnvelope, now: datetime) -> None:
    if now.utcoffset() != timedelta(0):
        raise ContractError("clock must be timezone-aware UTC")
    if event.expires_at is not None and event.expires_at <= now:
        raise ContractError("event expired")
