"""Closed wire union. No-op and chat variants keep distinct payload shapes."""

from markos_runtime.contracts.base import ContractError, EventType
from markos_runtime.contracts.control import ChatEvent
from markos_runtime.contracts.events import EventEnvelope

RuntimeEvent = EventEnvelope | ChatEvent


def parse_event(body: bytes) -> RuntimeEvent:
    try:
        return EventEnvelope.from_wire(body)
    except ContractError:
        return ChatEvent.from_wire(body)


def routing_key(event_type: EventType) -> str:
    return {
        "foundation.noop.requested": "v1.foundation.noop-requested",
        "control.chat.requested": "v1.control.chat-requested",
        "control.chat.completed": "v1.control.chat-completed",
    }[event_type]
