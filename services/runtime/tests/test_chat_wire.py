"""Closed typed chat union, strict old-wire compatibility and separately inert profiles."""

import json
from pathlib import Path
from typing import Any

import pytest

from markos_runtime.contracts.base import ContractError
from markos_runtime.contracts.control import ChatEvent
from markos_runtime.contracts.events import EventEnvelope
from markos_runtime.contracts.wire import parse_event, routing_key
from markos_runtime.control.api_config import SyntheticApiConfig, SyntheticConversationApiConfig

EXAMPLES = Path(__file__).resolve().parents[3] / "config/runtime/control"


def chat() -> dict[str, Any]:
    return json.loads((EXAMPLES / "chat-event.example.json").read_bytes())  # type: ignore[no-any-return]


def test_closed_union_preserves_distinct_noop_and_message_shapes() -> None:
    original = (EXAMPLES.parent / "noop.example.json").read_bytes()
    assert isinstance(parse_event(original), EventEnvelope)
    event = parse_event(json.dumps(chat()).encode())
    assert isinstance(event, ChatEvent) and event.consumers == ("control-test-agent",)
    assert event.routing_key == routing_key(event.event_type) == "v1.control.chat-requested"
    with pytest.raises(ContractError):
        EventEnvelope.from_wire(event.to_wire())
    completed = chat()
    completed.update(
        event_type="control.chat.completed",
        source="control-test-agent",
        causation_id=completed["event_id"],
    )
    completed["message"]["kind"] = "assistant-message"
    result = parse_event(json.dumps(completed).encode())
    assert isinstance(result, ChatEvent) and result.consumers == ("control-activity",)
    assert result.routing_key == "v1.control.chat-completed"


@pytest.mark.parametrize(
    "field,value",
    [
        ("event_type", "control.tool.execute"),
        ("source", "foundation-generator"),
        ("schema_version", True),
        ("schema_version", 1.0),
        ("schema_version", 2),
        ("payload", {"label": "not-chat"}),
        ("payload_ref", {"source_id": "66666666-6666-4666-8666-666666666666"}),
        ("actor_id", "forged"),
        ("token", "secret"),
        ("occurred_at", "2026-11-13T01:00:00+01:00"),
    ],
)
def test_chat_does_not_expand_arbitrary_types_or_payloads(field: str, value: object) -> None:
    event = chat()
    event[field] = value
    with pytest.raises(ContractError):
        parse_event(json.dumps(event).encode())


@pytest.mark.parametrize(
    "field,value",
    [
        ("kind", "assistant-message"),
        ("owner_id", "99999999-9999-4999-8999-999999999999"),
        ("domain", "work"),
        ("sha256", "invalid"),
        ("message_id", "00000000-0000-0000-0000-000000000000"),
        ("body", "never in event"),
    ],
)
def test_chat_reference_is_typed_and_scope_bound(field: str, value: object) -> None:
    event = chat()
    event["message"][field] = value
    with pytest.raises(ContractError):
        parse_event(json.dumps(event).encode())


@pytest.mark.parametrize("value", [True, False, 1, 0, "true", None])
def test_original_profile_cannot_turn_on_conversation(value: object) -> None:
    original = {
        "schema_version": 1,
        "api_origin": "https://127.0.0.1:18443",
        "chat_execution_enabled": value,
        "conversation_profile": "deterministic-conversation-v1",
    }
    with pytest.raises(ContractError):
        SyntheticApiConfig.from_wire(json.dumps(original).encode())


@pytest.mark.parametrize(
    "field,value",
    [
        ("chat_execution_enabled", False),
        ("chat_execution_enabled", 1),
        ("real_accounts_enabled", True),
        ("tool_execution_enabled", True),
        ("conversation_profile", "arbitrary-agent"),
        ("api_origin", "https://remote.example:18443"),
    ],
)
def test_conversation_profile_cannot_enable_real_or_arbitrary_execution(
    field: str, value: object
) -> None:
    profile = json.loads((EXAMPLES / "conversation-api.example.json").read_bytes())
    profile[field] = value
    with pytest.raises(ContractError):
        SyntheticConversationApiConfig.from_wire(json.dumps(profile).encode())
