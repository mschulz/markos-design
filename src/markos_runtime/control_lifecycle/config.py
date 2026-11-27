"""Closed process configuration for owned synthetic control-plane acceptance only."""

from pathlib import Path
from typing import Annotated, Literal, Self
from uuid import UUID

from pydantic import Field, ValidationInfo, field_validator, model_validator

from markos_runtime.config import read_bounded
from markos_runtime.contracts.base import ContractError, Domain, exact_int, loopback_https
from markos_runtime.contracts.control import ControlValue

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
        "google",
        "mail",
    ]
    owner_id: UUID
    domain: Domain
    service_id: Literal["control-gateway", "control-test-agent", "control-activity", "control-mail"]
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
    issuer_state_key_secret_ref: ControlSecretRef = "MARKOS_CONTROL_ISSUER_STATE_KEY"
    tool_secret_ref: ControlSecretRef = "MARKOS_CONTROL_TOOL_TOKEN"
    fixture_admin_secret_ref: ControlSecretRef = "MARKOS_CONTROL_FIXTURE_ADMIN_TOKEN"
    session_key_secret_ref: ControlSecretRef = "MARKOS_CONTROL_SESSION_KEY"
    bill_objects_path: str | None = None
    # Mail is off unless set on the gateway and the mail process. Google is the fixture Google.
    mail_enabled: bool = False
    google_endpoint: str = "https://127.0.0.1:19445"
    google_client_id: str = Field(
        default="markos-fixture.apps.googleusercontent.test",
        pattern=r"^[a-z0-9.-]{1,128}\.apps\.googleusercontent\.test$",
    )
    google_client_secret_ref: ControlSecretRef = "MARKOS_CONTROL_GOOGLE_CLIENT_SECRET"
    google_admin_secret_ref: ControlSecretRef = "MARKOS_CONTROL_GOOGLE_ADMIN_TOKEN"
    mail_database_secret_ref: ControlSecretRef = "MARKOS_CONTROL_MAIL_DATABASE_DSN"
    mail_token_key_secret_ref: ControlSecretRef = "MARKOS_CONTROL_MAIL_TOKEN_KEY"
    mail_worker_public_key: str | None = Field(default=None, pattern=r"^[A-Za-z0-9_-]{43}=$")
    push_account: str = Field(
        default="gmail-push@markos-fixture.iam.gserviceaccount.test",
        pattern=r"^[a-z0-9-]{1,64}@[a-z0-9-]{1,64}\.iam\.gserviceaccount\.test$",
    )
    pubsub_topic: str = Field(
        default="projects/markos-fixture/topics/gmail",
        pattern=r"^projects/[a-z][a-z0-9-]{1,62}/topics/[A-Za-z][A-Za-z0-9._-]{1,254}$",
    )
    shutdown_seconds: Literal[15] = 15
    work_seconds: Literal[10] = 10
    real_accounts_enabled: Literal[False] = False
    real_tools_enabled: Literal[False] = False
    migrate_on_start: Literal[False] = False

    _exact_number = field_validator(
        "health_port", "shutdown_seconds", "work_seconds", mode="before"
    )(exact_int)

    @field_validator("audit_url")
    @classmethod
    def local_audit(cls, value: str) -> str:
        if value not in {"https://rabbitmq:15671", "https://127.0.0.1:15671"}:
            raise ValueError("fixed isolated broker management required")
        return value

    @field_validator("api_origin", "issuer_endpoint", "tool_endpoint", "google_endpoint")
    @classmethod
    def endpoints(cls, value: str, info: ValidationInfo) -> str:
        path = {
            "api_origin": "",
            "issuer_endpoint": "/fixture",
            "tool_endpoint": "/mcp",
            "google_endpoint": "",
        }
        loopback_https(value, paths=frozenset({path[str(info.field_name)]}))
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
        if self.bill_objects_path is not None and (
            self.component not in {"gateway", "mail"}
            or not Path(self.bill_objects_path).is_absolute()
            or ".." in Path(self.bill_objects_path).parts
        ):
            raise ValueError("bill originals require an explicit gateway or mail directory")
        if self.mail_enabled != (self.component == "mail") and self.component != "gateway":
            raise ValueError("mail settings are for the gateway and the mail process only")
        if (self.mail_worker_public_key is not None) != (
            self.component == "gateway" and self.mail_enabled
        ):
            raise ValueError("a mail gateway requires the mail worker public key")
        expected = {
            "gateway": "control-gateway",
            "issuer": "control-gateway",
            "mcp": "control-test-agent",
            "publisher": "control-gateway",
            "completion-publisher": "control-test-agent",
            "consumer": "control-test-agent",
            "activity": "control-activity",
            "worker": "control-test-agent",
            "google": "control-gateway",
            "mail": "control-mail",
        }
        if self.service_id != expected[self.component]:
            raise ValueError("fixed component/service binding required")
        if (
            len(
                {
                    self.api_origin,
                    self.issuer_endpoint.removesuffix("/fixture"),
                    self.tool_endpoint.removesuffix("/mcp"),
                    self.google_endpoint,
                }
            )
            != 4
        ):
            raise ValueError("distinct listeners required")
        return self


def load_process(path: Path) -> ControlServiceConfig:
    try:
        return ControlServiceConfig.from_wire(read_bounded(path, 65536))
    except (OSError, ValueError):
        raise ContractError("invalid explicit synthetic process configuration") from None
