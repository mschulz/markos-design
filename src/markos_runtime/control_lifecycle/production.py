"""Closed production process configuration, file secrets and the Supabase DSN check.

No service accepts this profile yet: M7-D2 wires it into the services and Compose.
"""

import os
import re
import stat
from collections.abc import Iterator, Mapping
from pathlib import Path
from typing import Literal, Self
from urllib.parse import urlsplit
from uuid import UUID

from pydantic import Field, field_validator, model_validator

from markos_runtime.contracts.base import Domain, ValueModel, exact_int
from markos_runtime.contracts.control import ControlValue
from markos_runtime.control_lifecycle.config import (
    SERVICE_IDS,
    ControlSecretRef,
    GoogleSubject,
    MarkosSubject,
)

SECRETS_DIR = "/run/secrets"
SECRET_NAME = re.compile(r"MARKOS_CONTROL_[A-Z][A-Z0-9_]{0,63}")
MAX_SECRET_BYTES = 8192
# A public DNS name: lowercase labels, at least one dot, never an IP address or a bare label.
DNS_NAME = re.compile(r"(?=.{4,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}")


def _dns_name(value: str) -> str:
    if not DNS_NAME.fullmatch(value):
        raise ValueError("public DNS name required")
    return value


def _https(value: str, *, host: str | None = None, path: str = "") -> str:
    """Check an HTTPS URL with no credentials, query or fragment.

    With `host`, the URL is a private Compose address that names a port. Without it,
    the URL is public: a DNS name on the default port.
    """
    u = urlsplit(value)
    if (
        u.scheme != "https"
        or u.username
        or u.password
        or u.path != path
        or u.query
        or u.fragment
        or not u.hostname
    ):
        raise ValueError("HTTPS URL required")
    if host is None:
        _dns_name(u.hostname)
        if u.netloc != u.hostname:
            raise ValueError("public HTTPS URL on the default port required")
    elif u.netloc != f"{host}:{u.port}" or not u.port or not 1024 <= u.port <= 65535:
        raise ValueError("private Compose HTTPS URL required")
    return value


def _absolute_file(value: str) -> str:
    if not Path(value).is_absolute() or len(value) > 512 or ".." in Path(value).parts:
        raise ValueError("explicit absolute file required")
    return value


class SpacesSettings(ValueModel):
    """The DigitalOcean Spaces bucket for bill originals. Keys come from secret files."""

    endpoint: str
    region: str = Field(pattern=r"^[a-z0-9-]{1,32}$")
    bucket: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{1,61}[a-z0-9]$")
    access_key_ref: ControlSecretRef = "MARKOS_CONTROL_SPACES_ACCESS_KEY"
    secret_key_ref: ControlSecretRef = "MARKOS_CONTROL_SPACES_SECRET_KEY"

    @field_validator("endpoint")
    @classmethod
    def public_endpoint(cls, value: str) -> str:
        return _https(value)


class ProductionServiceConfig(ControlValue):
    environment: Literal["production"] = "production"
    profile: Literal["control-process-production-v1"]
    # No fixture Google and no fixture tool server.
    component: Literal[
        "gateway",
        "issuer",
        "publisher",
        "completion-publisher",
        "consumer",
        "activity",
        "worker",
        "mail",
    ]
    owner_id: UUID
    domain: Domain
    service_id: Literal["control-gateway", "control-test-agent", "control-activity", "control-mail"]
    # The public origin that Caddy serves. The issuer is private on the Compose network.
    api_origin: str
    issuer_endpoint: str
    listen_host: Literal["0.0.0.0"] = "0.0.0.0"
    health_port: int = Field(ge=1024, le=65535)
    # Private TLS between services on the Compose network.
    ca_file: str
    certificate_file: str
    private_key_file: str
    # Supabase PostgreSQL. Every DSN must name this host and verify it against this CA.
    database_host: str
    database_ca_file: str
    secrets_dir: Literal["/run/secrets"] = "/run/secrets"
    database_secret_ref: ControlSecretRef = "MARKOS_CONTROL_DATABASE_DSN"
    broker_secret_ref: ControlSecretRef = "MARKOS_CONTROL_BROKER_DSN"
    broker_publisher_user: str = Field(min_length=1, max_length=128)
    audit_url: Literal["https://rabbitmq:15671"] = "https://rabbitmq:15671"
    audit_user_secret_ref: ControlSecretRef = "MARKOS_CONTROL_AUDIT_USER"
    audit_password_secret_ref: ControlSecretRef = "MARKOS_CONTROL_AUDIT_PASSWORD"
    issuer_api_secret_ref: ControlSecretRef = "MARKOS_CONTROL_ISSUER_API_TOKEN"
    issuer_worker_secret_ref: ControlSecretRef = "MARKOS_CONTROL_ISSUER_WORKER_TOKEN"
    issuer_admin_secret_ref: ControlSecretRef = "MARKOS_CONTROL_ISSUER_ADMIN_TOKEN"
    issuer_state_key_secret_ref: ControlSecretRef = "MARKOS_CONTROL_ISSUER_STATE_KEY"
    session_key_secret_ref: ControlSecretRef = "MARKOS_CONTROL_SESSION_KEY"
    spaces: SpacesSettings | None = None
    # Sign-in is always Google, in the issuer. Mail is off unless the gateway and the
    # mail process both set it.
    google_client_id: str | None = Field(
        default=None, pattern=r"^[0-9]{1,32}-[a-z0-9]{1,64}\.apps\.googleusercontent\.com$"
    )
    google_client_secret_ref: ControlSecretRef = "MARKOS_CONTROL_GOOGLE_CLIENT_SECRET"
    google_signin_subjects: dict[GoogleSubject, MarkosSubject] | None = Field(
        default=None, min_length=1, max_length=100
    )
    mail_enabled: bool = False
    mail_database_secret_ref: ControlSecretRef = "MARKOS_CONTROL_MAIL_DATABASE_DSN"
    mail_token_key_secret_ref: ControlSecretRef = "MARKOS_CONTROL_MAIL_TOKEN_KEY"
    mail_worker_public_key: str | None = Field(default=None, pattern=r"^[A-Za-z0-9_-]{43}=$")
    push_account: str | None = Field(
        default=None,
        pattern=r"^[a-z0-9-]{1,64}@[a-z0-9-]{1,64}\.iam\.gserviceaccount\.com$",
    )
    pubsub_topic: str | None = Field(
        default=None,
        pattern=r"^projects/[a-z][a-z0-9-]{1,62}/topics/[A-Za-z][A-Za-z0-9._-]{1,254}$",
    )
    shutdown_seconds: Literal[15] = 15
    work_seconds: Literal[10] = 10
    real_tools_enabled: Literal[False] = False
    migrate_on_start: Literal[False] = False

    _exact_number = field_validator(
        "health_port", "shutdown_seconds", "work_seconds", mode="before"
    )(exact_int)

    @field_validator("api_origin")
    @classmethod
    def public_origin(cls, value: str) -> str:
        return _https(value)

    @field_validator("issuer_endpoint")
    @classmethod
    def private_issuer(cls, value: str) -> str:
        return _https(value, host="issuer", path="/fixture")

    @field_validator("database_host")
    @classmethod
    def database(cls, value: str) -> str:
        return _dns_name(value)

    @field_validator("ca_file", "certificate_file", "private_key_file", "database_ca_file")
    @classmethod
    def explicit_file(cls, value: str) -> str:
        return _absolute_file(value)

    @field_validator("real_tools_enabled", "migrate_on_start", mode="before")
    @classmethod
    def disabled(cls, value: object) -> object:
        if value is not False:
            raise ValueError("disabled fixed switch required")
        return value

    @model_validator(mode="after")
    def role(self) -> Self:
        if self.service_id != SERVICE_IDS[self.component]:
            raise ValueError("fixed component/service binding required")
        if self.spaces is not None and self.component not in {"gateway", "mail"}:
            raise ValueError("bill originals are for the gateway and the mail process only")
        if self.mail_enabled != (self.component == "mail") and self.component != "gateway":
            raise ValueError("mail settings are for the gateway and the mail process only")
        if (self.mail_worker_public_key is not None) != (
            self.component == "gateway" and self.mail_enabled
        ):
            raise ValueError("a mail gateway requires the mail worker public key")
        if (self.push_account is not None) != (self.component == "gateway" and self.mail_enabled):
            raise ValueError("a mail gateway requires the push account")
        if (self.pubsub_topic is not None) != (self.component == "mail"):
            raise ValueError("the mail process requires the Pub/Sub topic")
        if (self.google_signin_subjects is not None) != (self.component == "issuer"):
            raise ValueError("the issuer requires the Google subject map")
        if (self.google_client_id is not None) != (self.component == "issuer" or self.mail_enabled):
            raise ValueError("the Google client ID is for the issuer and mail only")
        return self


class FileSecrets(Mapping[str, str]):
    """Secrets read from root-only files that Compose mounts in the secrets directory.

    `secret()` reads this like `os.environ`. A missing, linked, oversized or
    non-regular file reads as absent, so the service refuses to start.
    """

    def __init__(self, directory: str = SECRETS_DIR) -> None:
        self.directory = directory

    def __getitem__(self, name: str) -> str:
        if not SECRET_NAME.fullmatch(name):
            raise KeyError(name)
        try:
            fd = os.open(os.path.join(self.directory, name), os.O_RDONLY | os.O_NOFOLLOW)
        except OSError:
            raise KeyError(name) from None
        try:
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_SECRET_BYTES:
                raise KeyError(name)
            raw = os.read(fd, MAX_SECRET_BYTES + 1)
        finally:
            os.close(fd)
        try:
            value = raw.decode("utf-8").removesuffix("\n")
        except UnicodeDecodeError:
            raise KeyError(name) from None
        if len(raw) > MAX_SECRET_BYTES or not value or "\n" in value or "\x00" in value:
            raise KeyError(name)
        return value

    def __iter__(self) -> Iterator[str]:
        try:
            names = sorted(os.listdir(self.directory))
        except OSError:
            return iter(())
        return iter([n for n in names if SECRET_NAME.fullmatch(n)])

    def __len__(self) -> int:
        return sum(1 for _ in self)


def production_dsn(dsn: str, host: str, ca_file: str) -> None:
    """Refuse a DSN unless it names the configured host and verifies it against the CA."""
    from psycopg.conninfo import conninfo_to_dict

    from markos_runtime.lifecycle.service import FatalServiceError

    try:
        info = conninfo_to_dict(dsn)
    except Exception:
        raise FatalServiceError("verified production database required") from None
    if (
        info.get("host") != host
        or "hostaddr" in info
        or info.get("sslmode") != "verify-full"
        or info.get("sslrootcert") != ca_file
        or not info.get("dbname")
        or not info.get("user")
    ):
        raise FatalServiceError("verified production database required")
