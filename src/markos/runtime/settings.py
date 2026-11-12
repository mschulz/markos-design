"""Explicit, bounded runtime TOML loading, independent of legacy Markdown root settings."""

from __future__ import annotations

import json
import os
import tomllib
from collections.abc import Mapping
from pathlib import Path
from typing import Annotated, Literal, Self

from pydantic import Field, ValidationError, field_validator, model_validator

from markos.runtime.contracts import ContractModel, Environment, ServiceScope

MAX_CONFIG_BYTES = 65_536
SecretName = Annotated[str, Field(pattern=r"^MARKOS_RUNTIME_[A-Z][A-Z0-9_]{0,63}$")]


class RuntimeConfigurationError(ValueError):
    """Configuration error whose text never includes values or parser input."""


class ResourceLimits(ContractModel):
    vm_cpus: int = Field(default=4, ge=1, le=64)
    broker_cpus: int = Field(default=4, ge=1, le=64)
    vm_memory_mib: int = Field(default=8192, ge=1024)
    broker_memory_mib: int = Field(default=4096, ge=256)
    memory_alarm_mib: int = Field(default=2048, ge=128)
    app_memory_mib: int = Field(default=2048, ge=128)
    os_memory_mib: int = Field(default=2048, ge=256)
    disk_free_alarm_mib: int = Field(default=10240, ge=1024)
    disk_free_warning_mib: int = Field(default=20480, ge=1024)
    max_message_bytes: int = Field(default=65536, ge=1, le=65536)
    queue_max_messages: int = Field(default=10000, ge=1, le=10000)
    queue_max_bytes: int = Field(default=33554432, ge=1, le=33554432)
    max_queues: int = Field(default=64, ge=1, le=64)
    max_connections: int = Field(default=32, ge=1, le=32)
    max_channels_per_connection: int = Field(default=32, ge=1, le=32)
    prefetch: int = Field(default=10, ge=1, le=10)
    handoffs_per_process: int = Field(default=2, ge=1, le=2)
    agent_slots: int = Field(default=2, ge=1, le=2)
    max_inflight_confirms: int = Field(default=100, ge=1, le=100)
    confirm_timeout_seconds: int = Field(default=10, ge=1, le=10)
    connection_timeout_seconds: int = Field(default=10, ge=1, le=10)
    heartbeat_seconds: int = Field(default=30, ge=5)
    handoff_timeout_seconds: int = Field(default=10, ge=1, le=10)
    delivery_timeout_seconds: int = Field(default=30, ge=1, le=30)
    statement_timeout_seconds: int = Field(default=5, ge=1, le=5)
    lock_timeout_seconds: int = Field(default=1, ge=1, le=1)
    close_timeout_seconds: int = Field(default=2, ge=1, le=2)
    replay_events_per_second: int = Field(default=10, ge=1, le=10)
    replay_batch_size: int = Field(default=100, ge=1, le=100)

    @model_validator(mode="after")
    def budgets_fit(self) -> Self:
        if self.broker_cpus > self.vm_cpus:
            raise ValueError("CPU budget exceeds VM")
        if self.broker_memory_mib + self.app_memory_mib + self.os_memory_mib > self.vm_memory_mib:
            raise ValueError("memory budgets exceed VM")
        if self.memory_alarm_mib >= self.broker_memory_mib:
            raise ValueError("memory alarm must precede cap")
        if self.disk_free_alarm_mib >= self.disk_free_warning_mib:
            raise ValueError("disk warning must precede alarm")
        if not (self.lock_timeout_seconds <= self.statement_timeout_seconds
                <= self.handoff_timeout_seconds < self.delivery_timeout_seconds):
            raise ValueError("handoff deadlines are inconsistent")
        if self.handoffs_per_process > self.prefetch:
            raise ValueError("handoff concurrency exceeds prefetch")
        if self.queue_max_bytes < self.max_message_bytes:
            raise ValueError("queue cannot hold one maximum-size message")
        return self


class RuntimeSettings(ContractModel):
    environment: Environment
    role: Literal["publisher", "consumer"]
    scope: ServiceScope
    topology_file: Annotated[str, Field(pattern=r"^[a-z0-9][a-z0-9._-]{0,127}$")]
    broker_dsn_env: SecretName = "MARKOS_RUNTIME_BROKER_DSN"
    database_dsn_env: SecretName = "MARKOS_RUNTIME_DATABASE_DSN"
    limits: ResourceLimits = Field(default_factory=ResourceLimits)
    replay_window_days: Literal[30] = 30
    terminal_retention_days: Literal[30] = 30
    dedup_retention_days: Literal[90] = 90

    @field_validator("replay_window_days", "terminal_retention_days", "dedup_retention_days",
                     mode="before")
    @classmethod
    def integer_retention(cls, value: object) -> object:
        if type(value) is not int:
            raise ValueError("retention must be an integer")
        return value

    @model_validator(mode="after")
    def distinct_secret_names(self) -> Self:
        if self.broker_dsn_env == self.database_dsn_env:
            raise ValueError("credential references must differ")
        return self

    @classmethod
    def load(cls, config_path: Path | None = None, *,
             environ: Mapping[str, str] | None = None) -> tuple[Self, Path]:
        env = os.environ if environ is None else environ
        if config_path is None:
            configured = env.get("MARKOS_RUNTIME_CONFIG")
            if not configured:
                raise RuntimeConfigurationError("explicit runtime configuration is required")
            config_path = Path(configured)
        try:
            with config_path.open("rb") as handle:
                body = handle.read(MAX_CONFIG_BYTES + 1)
            if len(body) > MAX_CONFIG_BYTES:
                raise ValueError("configuration too large")
            document = tomllib.loads(body.decode("utf-8"))
            if set(document) != {"runtime"}:
                raise ValueError("expected runtime table only")
            model = cls.model_validate_json(json.dumps(document["runtime"], allow_nan=False))
            return model, config_path.parent / model.topology_file
        except (OSError, ValueError, TypeError, ValidationError, RecursionError):
            raise RuntimeConfigurationError("invalid runtime configuration") from None
