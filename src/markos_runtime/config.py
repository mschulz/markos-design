"""Explicit, bounded runtime-only configuration; preflight never resolves secrets."""

import json
import os
import tomllib
from collections.abc import Mapping
from pathlib import Path
from typing import Annotated, Literal, Self

from pydantic import Field, model_validator

from markos_runtime.contracts.base import (
    MAX_DOCUMENT_BYTES,
    ContractError,
    Name,
    ValueModel,
    parse_document,
)
from markos_runtime.contracts.policy import Manifest

SecretRef = Annotated[str, Field(pattern=r"^MARKOS_RUNTIME_[A-Z][A-Z0-9_]{0,63}$")]
FileName = Annotated[str, Field(pattern=r"^[a-z0-9][a-z0-9._-]{0,127}\.json$")]


class ProposedLimits(ValueModel):
    """ADR-003 planning bounds; validation does not apply broker/host settings."""

    vm_cpus: int = Field(default=4, ge=1)
    broker_cpus: int = Field(default=4, ge=1, le=4)
    vm_memory_mib: int = Field(default=8192, ge=1024)
    broker_memory_mib: int = Field(default=4096, ge=256, le=4096)
    memory_alarm_mib: int = Field(default=2048, ge=128)
    app_memory_mib: int = Field(default=2048, ge=128)
    os_memory_mib: int = Field(default=2048, ge=256)
    disk_alarm_mib: int = Field(default=10240, ge=10240)
    disk_warning_mib: int = Field(default=20480, ge=10240)
    max_message_bytes: int = Field(default=65536, ge=1, le=65536)
    queue_max_messages: int = Field(default=10000, ge=1, le=10000)
    queue_max_bytes: int = Field(default=33554432, ge=65536, le=33554432)
    max_queues: int = Field(default=64, ge=1, le=64)
    max_connections: int = Field(default=32, ge=1, le=32)
    channels_per_connection: int = Field(default=32, ge=1, le=32)
    prefetch: int = Field(default=10, ge=1, le=10)
    handoffs_per_process: int = Field(default=2, ge=1, le=2)
    worker_slots: int = Field(default=2, ge=1, le=2)
    max_inflight_confirms: int = Field(default=100, ge=1, le=100)
    confirm_seconds: int = Field(default=10, ge=1, le=10)
    connect_seconds: int = Field(default=10, ge=1, le=10)
    heartbeat_seconds: int = Field(default=30, ge=5, le=30)
    active_handoff_seconds: int = Field(default=10, ge=1, le=10)
    total_delivery_seconds: int = Field(default=30, ge=1, le=30)
    statement_seconds: int = Field(default=5, ge=1, le=5)
    lock_seconds: int = Field(default=1, ge=1, le=1)
    close_seconds: int = Field(default=2, ge=1, le=2)
    replay_per_second: int = Field(default=10, ge=1, le=10)
    replay_batch: int = Field(default=100, ge=1, le=100)
    replay_days: int = Field(default=30, ge=30, le=30)
    terminal_retention_days: int = Field(default=30, ge=30)
    dedup_days: int = Field(default=90, ge=90)

    @model_validator(mode="after")
    def coherent(self) -> Self:
        if self.broker_cpus > self.vm_cpus:
            raise ValueError("CPU budget exceeds envelope")
        if self.broker_memory_mib + self.app_memory_mib + self.os_memory_mib > self.vm_memory_mib:
            raise ValueError("memory budgets exceed envelope")
        if self.memory_alarm_mib >= self.broker_memory_mib:
            raise ValueError("memory alarm must precede cap")
        if self.disk_alarm_mib >= self.disk_warning_mib:
            raise ValueError("disk alarm must follow warning")
        if (
            not self.lock_seconds
            <= self.statement_seconds
            <= self.active_handoff_seconds
            < (self.total_delivery_seconds)
        ):
            raise ValueError("handoff deadlines inconsistent")
        if self.handoffs_per_process > self.prefetch:
            raise ValueError("handoff budget exceeds prefetch")
        if self.replay_days > self.dedup_days:
            raise ValueError("replay exceeds dedup floor")
        return self


class RuntimeConfig(ValueModel):
    # Only the synthetic foundation is permitted in M0-A.
    environment: Literal["test", "development"]
    role: Literal["publisher", "consumer"]
    service_id: Name
    manifest_file: FileName
    database_secret_ref: SecretRef = "MARKOS_RUNTIME_DATABASE_DSN"
    broker_secret_ref: SecretRef = "MARKOS_RUNTIME_BROKER_DSN"
    limits: ProposedLimits = Field(default_factory=ProposedLimits)

    @property
    def broker_vhost(self) -> str:
        return f"markos-{self.environment}"

    @model_validator(mode="after")
    def distinct_refs(self) -> Self:
        if self.database_secret_ref == self.broker_secret_ref:
            raise ValueError("secret references must differ")
        return self


def read_bounded(path: Path, limit: int) -> bytes:
    try:
        with path.open("rb") as handle:
            body = handle.read(limit + 1)
        if len(body) > limit:
            raise ContractError("document exceeds byte limit")
        return body
    except OSError:
        raise ContractError("cannot read explicit runtime document") from None


def load_config(
    path: Path | None, environ: Mapping[str, str] | None = None
) -> tuple[RuntimeConfig, Manifest]:
    env = os.environ if environ is None else environ
    if path is None:
        configured = env.get("MARKOS_RUNTIME_CONFIG")
        if not configured:
            raise ContractError("explicit runtime configuration required")
        path = Path(configured)
    try:
        document = tomllib.loads(read_bounded(path, MAX_DOCUMENT_BYTES).decode("utf-8"))
        if set(document) != {"runtime"}:
            raise ContractError("expected runtime table only")
        body = json.dumps(document["runtime"], allow_nan=False).encode("utf-8")
        config = parse_document(RuntimeConfig, body, MAX_DOCUMENT_BYTES)
        manifest_path = path.parent / config.manifest_file
        if manifest_path.is_symlink():
            raise ContractError("manifest must be a regular adjacent file")
        manifest = Manifest.from_wire(read_bounded(manifest_path, MAX_DOCUMENT_BYTES))
        policy = manifest.policy(config.service_id)
        if (config.role == "publisher" and not policy.publishes) or (
            config.role == "consumer" and not policy.consumes
        ):
            raise ContractError("configured role not granted")
        # Prefix deployment environment at the broker vhost boundary, not event identity.
        if len({s.queue for s in manifest.subscriptions}) > config.limits.max_queues:
            raise ContractError("topology exceeds queue budget")
        return config, manifest
    except (ValueError, UnicodeError, RecursionError, TypeError):
        raise ContractError("invalid runtime configuration") from None
