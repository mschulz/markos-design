"""Typed seams for later adapters; M0.1 supplies no IO implementation."""

from datetime import datetime
from typing import Literal, Protocol

from markos.runtime.contracts import EventEnvelope, ServiceScope

PublishOutcome = Literal["confirmed", "unroutable", "rejected", "unknown"]
HandoffOutcome = Literal["created", "duplicate", "conflict", "quarantined"]


class Clock(Protocol):
    def now(self) -> datetime:
        """Return timezone-aware UTC; implementations/tests choose the clock."""
        ...


class EventStore(Protocol):
    async def persist_event(self, event: EventEnvelope,
                            consumers: tuple[ServiceScope, ...]) -> None:
        """Atomically persist event, intended consumers and outbox before publish."""
        ...


class InboxStore(Protocol):
    async def handoff(self, event: EventEnvelope, consumer: ServiceScope) -> HandoffOutcome:
        """Atomically record owner-scoped inbox and task before acknowledgement."""
        ...


class EventPublisher(Protocol):
    async def publish(self, body: bytes, *, exchange: str, routing_key: str) -> PublishOutcome:
        """Map actual broker confirms/returns/unknown state, never mere truthiness."""
        ...


class SourceAccess(Protocol):
    async def require_access(self, reference: str, consumer: ServiceScope) -> None:
        """Check source ownership, checksum, access and deletion before resolving."""
        ...
