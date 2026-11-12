import json
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import pytest
from pydantic import ValidationError

from markos.runtime.contracts import (
    MAX_EVENT_BYTES,
    ContractError,
    EventEnvelope,
    NoopPayload,
    ServiceScope,
    require_unexpired,
    validate_publisher_scope,
)


def decode(document: dict[str, Any]) -> EventEnvelope:
    return EventEnvelope.from_bytes(json.dumps(document).encode())


def test_valid_event_roundtrip_and_routing(event_document: dict[str, Any]) -> None:
    event = decode(event_document)
    assert EventEnvelope.from_bytes(event.to_bytes()) == event
    assert json.loads(event.to_bytes())["causation_id"] is None
    assert event.exchange == "markos.personal.events"
    assert event.routing_key == "v1.foundation.noop-requested"
    assert event.owner_id == UUID(event_document["owner_id"])


@pytest.mark.parametrize("field", ["event_id", "owner_id", "domain", "source", "schema_version",
                                  "occurred_at", "causation_id", "sensitivity"])
def test_missing_required_fields_fail(field: str, event_document: dict[str, Any]) -> None:
    del event_document[field]
    with pytest.raises(ContractError, match="invalid event envelope"):
        decode(event_document)


@pytest.mark.parametrize(("field", "value"), [
    ("schema_version", True), ("schema_version", "1"), ("schema_version", 2),
    ("owner_id", "00000000-0000-0000-0000-000000000000"), ("owner_id", "not-uuid"),
    ("domain", "shared"), ("source", "../publisher"),
    ("event_type", "email.received"), ("sensitivity", "secret"),
    ("occurred_at", "2026-11-12T00:00:00"), ("occurred_at", "2026-11-12T01:00:00+01:00"),
    ("expires_at", "2026-11-12T00:00:00Z"), ("unexpected", "do-not-echo-this-value"),
])
def test_strict_fields_fail_without_echoing_input(
    field: str, value: object, event_document: dict[str, Any]
) -> None:
    event_document[field] = value
    with pytest.raises(ContractError) as raised:
        decode(event_document)
    assert "do-not-echo" not in str(raised.value)


def test_payload_reference_is_exclusive_and_opaque(event_document: dict[str, Any]) -> None:
    reference = "source:55555555-5555-4555-8555-555555555555"
    event_document["payload_ref"] = reference
    with pytest.raises(ContractError):
        decode(event_document)
    del event_document["payload"]
    assert decode(event_document).payload_ref == reference
    event_document["payload_ref"] = "https://storage.invalid/private?token=secret"
    with pytest.raises(ContractError):
        decode(event_document)
    del event_document["payload_ref"]
    with pytest.raises(ContractError):
        decode(event_document)


@pytest.mark.parametrize("body", [
    b'{"schema_version":1,"schema_version":1}', b'{"payload":{"x":1,"x":2}}',
    b'{"payload":NaN}', b'{"payload":Infinity}', b'\xff', b'{broken',
])
def test_invalid_or_ambiguous_json_fails(body: bytes) -> None:
    with pytest.raises(ContractError):
        EventEnvelope.from_bytes(body)


def test_utf8_wire_boundary_includes_envelope(event_document: dict[str, Any]) -> None:
    event_document["payload"]["label"] = ""
    event = decode(event_document)
    room = MAX_EVENT_BYTES - len(event.to_bytes())
    full = event.model_copy(update={"payload": NoopPayload(operation_key="synthetic:noop:1",
                                                         label="a" * room)})
    assert len(full.to_bytes()) == MAX_EVENT_BYTES
    assert EventEnvelope.from_bytes(full.to_bytes()) == full
    with pytest.raises(ContractError, match="byte limit"):
        EventEnvelope.from_bytes(full.to_bytes() + b" ")
    oversized = event.model_copy(update={"payload": NoopPayload(operation_key="synthetic:noop:1",
                                                              label="é" * room)})
    with pytest.raises(ContractError, match="byte limit"):
        oversized.to_bytes()


def test_publication_checks_owner_domain_and_source(event_document: dict[str, Any]) -> None:
    event = decode(event_document)
    trusted = ServiceScope(owner_id=event.owner_id, domain="personal", service_id=event.source)
    validate_publisher_scope(event, trusted)
    for changes in ({"owner_id": UUID(int=12)}, {"domain": "work"}, {"source": "other"}):
        with pytest.raises(ContractError, match="scope mismatch"):
            validate_publisher_scope(event.model_copy(update=changes), trusted)


def test_expiry_is_checked_using_injected_utc_clock(event_document: dict[str, Any]) -> None:
    event_document["expires_at"] = "2026-11-13T00:00:00Z"
    event = decode(event_document)
    require_unexpired(event, datetime(2026, 11, 12, 1, tzinfo=UTC))
    with pytest.raises(ContractError, match="expired"):
        require_unexpired(event, datetime(2026, 11, 13, tzinfo=UTC))
    with pytest.raises(ContractError, match="clock"):
        require_unexpired(event, datetime(2026, 11, 12))


def test_models_are_frozen_and_python_inputs_are_strict(event_document: dict[str, Any]) -> None:
    event = decode(event_document)
    with pytest.raises(ValidationError):
        event.source = "other"
    with pytest.raises(ValidationError):
        ServiceScope(owner_id=event_document["owner_id"], domain="personal", service_id="publisher")
