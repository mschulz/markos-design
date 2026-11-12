"""Test-only service configuration; no live mode or implicit credential discovery."""

from pathlib import Path
from typing import Literal
from uuid import UUID

from pydantic import Field, field_validator, model_validator

from markos_runtime.config import FileName, SecretRef, read_bounded
from markos_runtime.contracts.base import (
    MAX_DOCUMENT_BYTES,
    ContractError,
    Domain,
    Name,
    ValueModel,
    parse_document,
)
from markos_runtime.contracts.policy import Manifest


class ServiceConfig(ValueModel):
    schema_version: Literal[1]
    environment: Literal["test"]
    component: Literal["publisher", "consumer", "worker", "scheduler", "recovery", "review"]
    service_id: Name
    owner_id: UUID
    domain: Domain
    manifest_file: FileName
    health_port: int = Field(default=8787, ge=1024, le=65535)
    tick_seconds: float = Field(default=1.0, ge=0.1, le=30)
    shutdown_seconds: int = Field(default=10, ge=5, le=15)
    synthetic_work_seconds: float = Field(default=0.0, ge=0, le=5)
    cleanup_enabled: bool = False
    database_secret_ref: SecretRef = "MARKOS_RUNTIME_DATABASE_DSN"
    broker_secret_ref: SecretRef = "MARKOS_RUNTIME_BROKER_DSN"
    audit_user_secret_ref: SecretRef = "MARKOS_RUNTIME_AUDIT_USER"
    audit_password_secret_ref: SecretRef = "MARKOS_RUNTIME_AUDIT_PASSWORD"
    audit_url: str = Field(default="https://rabbitmq:15671", max_length=256)
    publisher_users: dict[Name, Name] = Field(default_factory=dict, max_length=64)

    @field_validator("schema_version", mode="before")
    @classmethod
    def version_integer(cls, value: object) -> object:
        if type(value) is not int:
            raise ValueError("integer schema version required")
        return value

    @field_validator("audit_url")
    @classmethod
    def private_test_audit(cls, value: str) -> str:
        from urllib.parse import urlsplit

        url = urlsplit(value)
        if (
            url.hostname not in {"127.0.0.1", "localhost", "::1", "rabbitmq"}
            or url.username
            or url.password
            or url.query
            or url.fragment
            or url.scheme not in {"http", "https"}
            or (url.hostname == "rabbitmq" and url.scheme != "https")
        ):
            raise ValueError("secure synthetic audit endpoint required")
        return value

    @model_validator(mode="after")
    def unique_refs(self) -> "ServiceConfig":
        refs = (
            self.database_secret_ref,
            self.broker_secret_ref,
            self.audit_user_secret_ref,
            self.audit_password_secret_ref,
        )
        if len(set(refs)) != len(refs):
            raise ValueError("distinct credential references required")
        if self.component != "worker" and self.synthetic_work_seconds:
            raise ValueError("delay only for synthetic worker")
        if self.component != "recovery" and self.cleanup_enabled:
            raise ValueError("cleanup only for recovery")
        return self


def load_service(path: Path) -> tuple[ServiceConfig, Manifest]:
    try:
        config = parse_document(
            ServiceConfig, read_bounded(path, MAX_DOCUMENT_BYTES), MAX_DOCUMENT_BYTES
        )
        manifest_path = path.parent / config.manifest_file
        if manifest_path.is_symlink():
            raise ContractError("adjacent manifest required")
        manifest = Manifest.from_wire(read_bounded(manifest_path, MAX_DOCUMENT_BYTES))
        policy = manifest.policy(config.service_id)
        if (policy.owner_id, policy.domain) != (config.owner_id, config.domain):
            raise ContractError("service scope mismatch")
        publish = config.component in {"publisher", "scheduler", "recovery"}
        if not (policy.publishes if publish else policy.consumes):
            raise ContractError("service role not granted")
        return config, manifest
    except (ValueError, TypeError, RecursionError):
        raise ContractError("invalid synthetic service configuration") from None
