"""Closed process configuration for owned synthetic control-plane acceptance only."""

from pathlib import Path
from typing import Annotated, Literal, Self
from urllib.parse import urlsplit
from uuid import UUID

from pydantic import Field, field_validator, model_validator

from markos_runtime.config import read_bounded
from markos_runtime.contracts.base import ContractError, Domain
from markos_runtime.contracts.control import ControlValue
from markos_runtime.contracts.governance import fixture_endpoint


ControlSecretRef = Annotated[str, Field(pattern=r"^MARKOS_CONTROL_[A-Z][A-Z0-9_]{0,63}$")]


class ControlServiceConfig(ControlValue):
    environment: Literal["test"] = "test"
    profile: Literal["control-process-fixture-v1"]
    component: Literal[
        "gateway",
        "issuer",
        "mcp",
        "publisher",
        "completion-publisher",
        "consumer",
        "activity",
        "worker",
    ]
    owner_id: UUID
    domain: Domain
    service_id: Literal["control-gateway", "control-test-agent", "control-activity"]
    api_origin: str = "https://127.0.0.1:18443"
    issuer_endpoint: str = "https://127.0.0.1:19444/fixture"
    tool_endpoint: str = "https://127.0.0.1:19443/mcp"
    listen_host: Literal["127.0.0.1", "0.0.0.0"] = "127.0.0.1"
    health_port: int = Field(ge=1024, le=65535)
    ca_file: str
    certificate_file: str
    private_key_file: str
    fixture_state_file: str
    fixture_seed_file: str
    database_secret_ref: ControlSecretRef = "MARKOS_CONTROL_DATABASE_DSN"
    broker_secret_ref: ControlSecretRef = "MARKOS_CONTROL_BROKER_DSN"
    broker_publisher_user: str = Field(min_length=1, max_length=128)
    audit_url: str = "https://rabbitmq:15671"
    audit_user_secret_ref: ControlSecretRef = "MARKOS_CONTROL_AUDIT_USER"
    audit_password_secret_ref: ControlSecretRef = "MARKOS_CONTROL_AUDIT_PASSWORD"
    issuer_api_secret_ref: ControlSecretRef = "MARKOS_CONTROL_ISSUER_API_TOKEN"
    issuer_worker_secret_ref: ControlSecretRef = "MARKOS_CONTROL_ISSUER_WORKER_TOKEN"
    issuer_admin_secret_ref: ControlSecretRef = "MARKOS_CONTROL_ISSUER_ADMIN_TOKEN"
    tool_secret_ref: ControlSecretRef = "MARKOS_CONTROL_TOOL_TOKEN"
    fixture_admin_secret_ref: ControlSecretRef = "MARKOS_CONTROL_FIXTURE_ADMIN_TOKEN"
    session_key_secret_ref: ControlSecretRef = "MARKOS_CONTROL_SESSION_KEY"
    shutdown_seconds: Literal[15] = 15
    work_seconds: Literal[10] = 10
    real_accounts_enabled: Literal[False] = False
    real_tools_enabled: Literal[False] = False
    migrate_on_start: Literal[False] = False

    @field_validator("health_port", "shutdown_seconds", "work_seconds", mode="before")
    @classmethod
    def exact_number(cls, value: object) -> object:
        if type(value) is not int:
            raise ValueError("exact integer required")
        return value

    @field_validator("audit_url")
    @classmethod
    def local_audit(cls, value: str) -> str:
        if value not in {"https://rabbitmq:15671", "https://127.0.0.1:15671"}:
            raise ValueError("fixed isolated broker management required")
        return value

    @field_validator("api_origin", "issuer_endpoint", "tool_endpoint")
    @classmethod
    def endpoints(cls, value: str) -> str:
        u = urlsplit(value)
        if (
            u.scheme != "https"
            or u.hostname != "127.0.0.1"
            or not u.port
            or not 1024 <= u.port <= 65535
            or u.netloc != f"127.0.0.1:{u.port}"
            or u.username
            or u.password
            or u.query
            or u.fragment
            or u.path not in {"", "/fixture", "/mcp"}
        ):
            raise ValueError("fixed numeric loopback HTTPS fixture required")
        return value

    @field_validator(
        "ca_file", "certificate_file", "private_key_file", "fixture_state_file", "fixture_seed_file"
    )
    @classmethod
    def explicit_file(cls, value: str) -> str:
        if not Path(value).is_absolute() or len(value) > 512 or ".." in Path(value).parts:
            raise ValueError("explicit absolute fixture file required")
        return value

    @field_validator(
        "real_accounts_enabled", "real_tools_enabled", "migrate_on_start", mode="before"
    )
    @classmethod
    def disabled(cls, value: object) -> object:
        if value is not False:
            raise ValueError("disabled fixed switch required")
        return value

    @model_validator(mode="after")
    def role(self) -> Self:
        expected = {
            "gateway": "control-gateway",
            "issuer": "control-gateway",
            "mcp": "control-test-agent",
            "publisher": "control-gateway",
            "completion-publisher": "control-test-agent",
            "consumer": "control-test-agent",
            "activity": "control-activity",
            "worker": "control-test-agent",
        }
        if self.service_id != expected[self.component]:
            raise ValueError("fixed component/service binding required")
        if urlsplit(self.api_origin).path or not self.issuer_endpoint.endswith("/fixture"):
            raise ValueError("fixed service path required")
        fixture_endpoint(self.tool_endpoint)
        if (
            len(
                {
                    self.api_origin,
                    self.issuer_endpoint.removesuffix("/fixture"),
                    self.tool_endpoint.removesuffix("/mcp"),
                }
            )
            != 3
        ):
            raise ValueError("distinct listeners required")
        return self


def load_process(path: Path) -> ControlServiceConfig:
    try:
        return ControlServiceConfig.from_wire(read_bounded(path, 65536))
    except (OSError, ValueError):
        raise ContractError("invalid explicit synthetic process configuration") from None
