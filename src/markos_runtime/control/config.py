"""Explicit test-only control configuration, with all runtime paths disabled."""

from pathlib import Path
from typing import Literal, Self
from urllib.parse import urlsplit

from pydantic import Field, field_validator, model_validator

from markos_runtime.config import FileName, SecretRef, read_bounded
from markos_runtime.contracts.base import MAX_DOCUMENT_BYTES, ContractError, parse_document
from markos_runtime.contracts.control import ControlValue


class ControlLimits(ControlValue):
    http_bytes: Literal[65536] = 65536
    chat_bytes: Literal[16384] = 16384
    response_bytes: Literal[262144] = 262144
    list_rows: Literal[100] = 100
    sse_frame_bytes: Literal[8192] = 8192
    streams_per_session: Literal[1] = 1
    streams_per_principal: Literal[4] = 4
    streams_global: Literal[20] = 20
    stream_seconds: Literal[600] = 600
    reauthorize_seconds: Literal[15] = 15
    writes_per_minute: Literal[30] = 30
    write_burst: Literal[5] = 5
    chat_pending_global: Literal[100] = 100
    chat_pending_scope: Literal[10] = 10
    sessions_principal: Literal[10] = 10
    sessions_global: Literal[100] = 100
    approval_pending_global: Literal[100] = 100
    approval_pending_scope: Literal[10] = 10
    unresolved_reserved_global: Literal[100] = 100
    unresolved_reserved_scope: Literal[20] = 20
    session_idle_seconds: Literal[1800] = 1800
    session_absolute_seconds: Literal[28800] = 28800
    cli_access_seconds: Literal[300] = 300
    approval_seconds: Literal[900] = 900
    statement_seconds: Literal[5] = 5
    lock_seconds: Literal[1] = 1
    request_seconds: Literal[10] = 10
    pool_connections: Literal[4] = 4
    memory_mib: Literal[256] = 256
    cpu_millicores: Literal[500] = 500
    audit_days: Literal[365] = 365
    sensitive_copy_days: Literal[7] = 7
    log_days: Literal[14] = 14
    unresolved_alert_seconds: Literal[86400] = 86400
    cleanup_seconds: Literal[86400] = 86400
    chat_terminal_days: Literal[30] = 30
    session_secret_purge_seconds: Literal[86400] = 86400

    @field_validator("*", mode="before")
    @classmethod
    def exact_numbers(cls, value: object) -> object:
        if type(value) is not int:
            raise ValueError("integer limit required")
        return value


class ControlConfig(ControlValue):
    environment: Literal["test"] = "test"
    issuer_id: Literal["synthetic-issuer"] = "synthetic-issuer"
    issuer_url: str = "https://auth.markos.test"
    audience: Literal["markos-control-test"] = "markos-control-test"
    algorithm: Literal["ES256"] = "ES256"
    jwks_file: FileName = "issuer-public.example.json"
    token_type: Literal["human-access"] = "human-access"
    database_secret_ref: SecretRef = "MARKOS_RUNTIME_CONTROL_DATABASE_DSN"
    session_secret_ref: SecretRef = "MARKOS_RUNTIME_CONTROL_SESSION_KEY"
    jwks_cache_seconds: Literal[60] = 60
    jwks_fetch_seconds: Literal[5] = 5
    jwks_refresh_min_seconds: Literal[60] = 60
    provider_session_cache_seconds: Literal[15] = 15
    jwks_max_bytes: Literal[65536] = 65536
    browser_cookie: Literal["__Host-markos-session"] = "__Host-markos-session"
    cookie_secure: Literal[True] = True
    cookie_http_only: Literal[True] = True
    cookie_same_site: Literal["lax"] = "lax"
    gateway_enabled: Literal[False] = False
    chat_execution_enabled: Literal[False] = False
    tool_execution_enabled: Literal[False] = False
    real_accounts_enabled: Literal[False] = False
    limits: ControlLimits = Field(default_factory=lambda: ControlLimits(schema_version=1))

    @field_validator(
        "cookie_secure",
        "cookie_http_only",
        "gateway_enabled",
        "chat_execution_enabled",
        "tool_execution_enabled",
        "real_accounts_enabled",
        mode="before",
    )
    @classmethod
    def exact_switches(cls, value: object) -> object:
        if type(value) is not bool:
            raise ValueError("boolean required")
        return value

    @field_validator(
        "jwks_cache_seconds",
        "jwks_max_bytes",
        "jwks_fetch_seconds",
        "jwks_refresh_min_seconds",
        "provider_session_cache_seconds",
        mode="before",
    )
    @classmethod
    def exact_cache_bounds(cls, value: object) -> object:
        if type(value) is not int:
            raise ValueError("integer required")
        return value

    @field_validator("issuer_url")
    @classmethod
    def fixture_origin(cls, value: str) -> str:
        url = urlsplit(value)
        if value != "https://auth.markos.test" or url.username or url.password:
            raise ValueError("explicit synthetic issuer origin required")
        return value

    @model_validator(mode="after")
    def distinct_refs(self) -> Self:
        if self.database_secret_ref == self.session_secret_ref:
            raise ValueError("distinct secret references required")
        return self


def load_control(path: Path) -> ControlConfig:
    try:
        return parse_document(
            ControlConfig, read_bounded(path, MAX_DOCUMENT_BYTES), MAX_DOCUMENT_BYTES
        )
    except (OSError, ValueError):
        raise ContractError("invalid explicit control configuration") from None
