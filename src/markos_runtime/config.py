"""Shared config field types, bounded file reads and fixed transport limits."""

from dataclasses import dataclass
from pathlib import Path
from typing import Annotated

from pydantic import Field

from markos_runtime.contracts.base import ContractError

SecretRef = Annotated[str, Field(pattern=r"^MARKOS_RUNTIME_[A-Z][A-Z0-9_]{0,63}$")]
FileName = Annotated[str, Field(pattern=r"^[a-z0-9][a-z0-9._-]{0,127}\.json$")]


@dataclass(frozen=True)
class TransportLimits:
    """Broker bounds that the publisher, consumer and session apply."""

    max_message_bytes: int = 65536
    queue_max_messages: int = 10000
    queue_max_bytes: int = 33554432
    prefetch: int = 10
    handoffs_per_process: int = 2
    max_inflight_confirms: int = 100
    confirm_seconds: int = 10
    connect_seconds: int = 10
    heartbeat_seconds: int = 30
    active_handoff_seconds: int = 10
    total_delivery_seconds: int = 30
    close_seconds: int = 2


def read_bounded(path: Path, limit: int) -> bytes:
    try:
        with path.open("rb") as handle:
            body = handle.read(limit + 1)
        if len(body) > limit:
            raise ContractError("document exceeds byte limit")
        return body
    except OSError:
        raise ContractError("cannot read explicit runtime document") from None
