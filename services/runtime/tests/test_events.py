import json
from datetime import UTC, datetime
from typing import Any

import pytest
from pydantic import ValidationError

from markos_runtime.contracts import ContractError, EventEnvelope
from markos_runtime.contracts.base import MAX_EVENT_BYTES


def wire(document: dict[str, Any]) -> bytes:
    return json.dumps(document, ensure_ascii=False, separators=(",", ":")).encode()


def test_round_trip_and_root_causation(event_document: dict[str, Any]) -> None:
    event = EventEnvelope.from_wire(wire(event_document))
    assert EventEnvelope.from_wire(event.to_wire()) == event
    assert json.loads(event.to_wire())["causation_id"] is None
    with pytest.raises(ValidationError):
        event.domain = "work"


@pytest.mark.parametrize(
    "field,value",
    [
        ("schema_version", True),
        ("schema_version", "1"),
        ("schema_version", 1.0),
        ("schema_version", 2),
        ("domain", "admin"),
        ("owner_id", "not-a-uuid"),
        ("event_id", "00000000-0000-0000-0000-000000000000"),
        ("causation_id", "00000000-0000-0000-0000-000000000000"),
        ("owner_id", "00000000-0000-0000-0000-000000000000"),
        ("event_type", "email.ingested"),
        ("source", "../../vault"),
        ("occurred_at", "2026-11-12T00:00:00"),
        ("occurred_at", "2026-11-12T01:00:00+01:00"),
        ("occurred_at", 123),
        ("expires_at", "2026-11-11T00:00:00Z"),
        ("sensitivity", "secret"),
        ("operation_key", ""),
        ("operation_key", "a" * 129),
        ("extra", "DO_NOT_ECHO_PRIVATE_INPUT"),
        ("payload", {"label": 12}),
        ("payload", {"label": "x", "extra": True}),
    ],
)
def test_strict_rejections(event_document: dict[str, Any], field: str, value: object) -> None:
    event_document[field] = value
    with pytest.raises(ContractError) as error:
        EventEnvelope.from_wire(wire(event_document))
    assert "DO_NOT_ECHO" not in str(error.value)
    assert error.value.__suppress_context__


@pytest.mark.parametrize("field", ["owner_id", "trace_id", "schema_version", "causation_id"])
def test_required_fields(event_document: dict[str, Any], field: str) -> None:
    event_document.pop(field)
    with pytest.raises(ContractError):
        EventEnvelope.from_wire(wire(event_document))


@pytest.mark.parametrize("change", ["neither", "both", "wrong-owner", "wrong-domain", "path"])
def test_payload_reference_boundaries(event_document: dict[str, Any], change: str) -> None:
    ref: dict[str, Any] = {
        "source_id": "55555555-5555-4555-8555-555555555555",
        "owner_id": event_document["owner_id"],
        "domain": "personal",
        "sha256": "a" * 64,
    }
    if change != "both":
        event_document.pop("payload")
    if change != "neither":
        event_document["payload_ref"] = ref
    if change == "wrong-owner":
        ref["owner_id"] = "99999999-9999-4999-8999-999999999999"
    elif change == "wrong-domain":
        ref["domain"] = "work"
    elif change == "path":
        event_document["payload_ref"] = "/private/notes/file.md"
    with pytest.raises(ContractError):
        EventEnvelope.from_wire(wire(event_document))


def test_source_reference_is_opaque_and_scoped(event_document: dict[str, Any]) -> None:
    event_document.pop("payload")
    event_document["payload_ref"] = {
        "source_id": "55555555-5555-4555-8555-555555555555",
        "owner_id": event_document["owner_id"],
        "domain": "personal",
        "sha256": "a" * 64,
    }
    assert EventEnvelope.from_wire(wire(event_document)).payload_ref is not None


@pytest.mark.parametrize("suffix", [b',"domain":"work"}', b',"payload":{"label":NaN}}'])
def test_duplicate_fields_and_non_json_constants(
    event_document: dict[str, Any], suffix: bytes
) -> None:
    with pytest.raises(ContractError):
        EventEnvelope.from_wire(wire(event_document)[:-1] + suffix)


def test_nested_duplicate_and_non_utf8(event_document: dict[str, Any]) -> None:
    for body in [b'{"payload":{"label":"a","label":"b"}}', b"\xff", b"{" * 2000]:
        with pytest.raises(ContractError):
            EventEnvelope.from_wire(body)


@pytest.mark.parametrize("unicode", [False, True])
def test_exact_wire_byte_limit(event_document: dict[str, Any], unicode: bool) -> None:
    event_document["payload"] = {"label": "é" * 100 if unicode else ""}
    body = wire(event_document)
    event_document["payload"]["label"] += "x" * (MAX_EVENT_BYTES - len(body))
    body = wire(event_document)
    assert len(body) == MAX_EVENT_BYTES
    event = EventEnvelope.from_wire(body)
    assert event.event_type == "foundation.noop.requested"
    with pytest.raises(ContractError, match="byte limit"):
        EventEnvelope.from_wire(body + b" ")
    event_document["payload"]["label"] += "x"
    with pytest.raises(ContractError):
        EventEnvelope.from_wire(wire(event_document))


def test_final_publication_revalidates_size(event_document: dict[str, Any]) -> None:
    event = EventEnvelope.from_wire(wire(event_document))
    broken = event.model_copy(update={"domain": "admin"})
    with pytest.raises(ContractError):
        broken.to_wire()


def test_expiry_at_boundary(event_document: dict[str, Any]) -> None:
    event_document["expires_at"] = "2026-11-12T01:00:00Z"
    event = EventEnvelope.from_wire(wire(event_document))
    event.require_live(datetime(2026, 11, 12, 0, 59, tzinfo=UTC))
    with pytest.raises(ContractError, match="expired"):
        event.require_live(datetime(2026, 11, 12, 1, tzinfo=UTC))
    with pytest.raises(ContractError, match="UTC"):
        event.require_live(datetime(2026, 11, 12))


def test_publication_rejects_multibyte_size_overflow(event_document: dict[str, Any]) -> None:
    from markos_runtime.contracts.events import NoopPayload

    event = EventEnvelope.from_wire(wire(event_document))
    huge_payload = NoopPayload(label="é" * 33000)
    event = event.model_copy(update={"payload": huge_payload})
    with pytest.raises(ContractError, match="byte limit"):
        event.to_wire()
