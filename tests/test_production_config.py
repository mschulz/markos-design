"""The production profile refuses every fixture setting, and its secrets and DSN fail closed."""

import json
import os
from pathlib import Path
from typing import Any

import pytest

from markos_runtime.contracts.base import ContractError
from markos_runtime.control_lifecycle.config import ControlServiceConfig
from markos_runtime.control_lifecycle.production import (
    FileSecrets,
    ProductionServiceConfig,
    production_dsn,
)
from markos_runtime.lifecycle.service import FatalServiceError, secret

KEY = "A" * 43 + "="
CLIENT = "123456-abc.apps.googleusercontent.com"
SPACES = {"endpoint": "https://syd1.digitaloceanspaces.com", "region": "syd1", "bucket": "markos"}


def config(**changes: Any) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "profile": "control-process-production-v1",
        "component": "gateway",
        "service_id": "control-gateway",
        "owner_id": "11111111-1111-4111-8111-111111111111",
        "domain": "personal",
        "api_origin": "https://markos.example.com",
        "issuer_endpoint": "https://issuer:19444/issuer",
        "health_port": 8780,
        "ca_file": "/etc/markos/ca.pem",
        "certificate_file": "/etc/markos/server.pem",
        "private_key_file": "/etc/markos/key.pem",
        "database_host": "db.abcdef.supabase.co",
        "database_ca_file": "/etc/markos/supabase-ca.pem",
        "broker_publisher_user": "markos-publisher",
        "spaces": SPACES,
        **changes,
    }


def load(document: dict[str, Any]) -> ProductionServiceConfig:
    return ProductionServiceConfig.from_wire(json.dumps(document).encode())


def test_a_gateway_loads_with_every_switch_off() -> None:
    profile = load(config())
    assert profile.environment == "production" and profile.listen_host == "0.0.0.0"
    assert profile.secrets_dir == "/run/secrets"
    assert profile.mail_enabled is False and profile.gateway_port == 18443
    assert profile.real_tools_enabled is False and profile.migrate_on_start is False


@pytest.mark.parametrize(
    "key,value",
    [
        # Fixture settings do not exist in production.
        ("fixture_state_file", "/fixture/state.json"),
        ("fixture_seed_file", "/fixture/seed.json"),
        ("tool_endpoint", "https://tools:19443/mcp"),
        ("google_endpoint", "https://127.0.0.1:19445"),
        ("google_signin", True),
        ("bill_objects_path", "/var/lib/markos/bills"),
        ("real_accounts_enabled", False),
        ("profile", "control-process-fixture-v1"),
        ("environment", "test"),
        ("component", "google"),
        ("component", "mcp"),
        # Fixed switches stay off.
        ("real_tools_enabled", True),
        ("migrate_on_start", True),
        ("migrate_on_start", 0),
        ("secrets_dir", "/tmp/secrets"),
        ("listen_host", "127.0.0.1"),
        ("audit_url", "https://example.com:15671"),
        ("health_port", True),
        ("work_seconds", 10.0),
        ("gateway_port", 80),
        ("spaces", None),
        # The public origin is a DNS name on the default port.
        ("api_origin", "http://markos.example.com"),
        ("api_origin", "https://127.0.0.1"),
        ("api_origin", "https://localhost"),
        ("api_origin", "https://markos.example.com:8443"),
        ("api_origin", "https://markos.example.com/app"),
        ("api_origin", "https://user@markos.example.com"),
        # The issuer is private on the Compose network.
        ("issuer_endpoint", "https://issuer.example.com/issuer"),
        ("issuer_endpoint", "https://issuer/issuer"),
        ("issuer_endpoint", "http://issuer:19444/issuer"),
        ("issuer_endpoint", "https://issuer:80/issuer"),
        # The database is a named host with an explicit CA.
        ("database_host", "127.0.0.1"),
        ("database_host", "postgres"),
        ("database_host", "DB.Example.com"),
        ("database_ca_file", "certs/ca.pem"),
        ("ca_file", "/etc/../ca.pem"),
        ("unknown", True),
    ],
)
def test_production_profile_fails_closed(key: str, value: Any) -> None:
    with pytest.raises(ContractError):
        load(config(**{key: value}))


def test_the_fixture_model_refuses_the_production_profile() -> None:
    with pytest.raises(ContractError):
        ControlServiceConfig.from_wire(json.dumps(config()).encode())


ISSUER = {
    "component": "issuer",
    "spaces": None,
    "google_client_id": CLIENT,
    "google_signin_subjects": {"1234567890": "owner"},
}
MAIL_GATEWAY = {
    "mail_enabled": True,
    "mail_worker_public_key": KEY,
    "push_account": "gmail-push@markos.iam.gserviceaccount.com",
    "google_client_id": CLIENT,
}
MAIL = {
    "component": "mail",
    "service_id": "control-mail",
    "mail_enabled": True,
    "pubsub_topic": "projects/markos/topics/gmail",
    "google_client_id": CLIENT,
}


@pytest.mark.parametrize(
    "changes",
    [
        ISSUER,
        MAIL_GATEWAY,
        MAIL,
        {"component": "worker", "service_id": "control-test-agent", "spaces": None},
        {"component": "activity", "service_id": "control-activity", "spaces": None},
    ],
)
def test_each_component_loads_with_its_own_settings(changes: dict[str, Any]) -> None:
    assert load(config(**changes)).component == changes.get("component", "gateway")


@pytest.mark.parametrize(
    "changes",
    [
        {"component": "issuer", "spaces": None},
        {**ISSUER, "spaces": SPACES},
        {**MAIL, "spaces": None},
        {**ISSUER, "google_client_id": None},
        {**ISSUER, "google_client_id": "markos-fixture.apps.googleusercontent.test"},
        {"google_signin_subjects": {"1": "owner"}},
        {"google_client_id": CLIENT},
        {"mail_enabled": True},
        {**MAIL_GATEWAY, "push_account": "a@b.iam.gserviceaccount.test"},
        {**MAIL_GATEWAY, "mail_worker_public_key": None},
        {**MAIL_GATEWAY, "pubsub_topic": "projects/markos/topics/gmail"},
        {**MAIL, "mail_enabled": False},
        {**MAIL, "pubsub_topic": None},
        {
            "component": "worker",
            "service_id": "control-test-agent",
            "spaces": None,
            "mail_enabled": True,
        },
        {"component": "worker", "service_id": "control-test-agent"},
        {"component": "worker", "service_id": "control-gateway", "spaces": None},
        {"spaces": {**SPACES, "endpoint": "http://syd1.digitaloceanspaces.com"}},
        {"spaces": {**SPACES, "endpoint": "https://127.0.0.1:9000"}},
        {"spaces": {**SPACES, "bucket": "Bad_Bucket"}},
        {"spaces": {**SPACES, "access_key": "AKIA"}},
    ],
)
def test_role_settings_fail_closed(changes: dict[str, Any]) -> None:
    with pytest.raises(ContractError):
        load(config(**changes))


def write(directory: Path, name: str, body: bytes) -> None:
    (directory / name).write_bytes(body)
    (directory / name).chmod(0o600)


def test_file_secrets_read_like_the_environment(tmp_path: Path) -> None:
    write(tmp_path, "MARKOS_CONTROL_SESSION_KEY", b"value\n")
    write(tmp_path, "unrelated", b"x")
    secrets = FileSecrets(str(tmp_path))
    assert secret(secrets, "MARKOS_CONTROL_SESSION_KEY") == "value"
    assert list(secrets) == ["MARKOS_CONTROL_SESSION_KEY"] and len(secrets) == 1


@pytest.mark.parametrize(
    "body",
    [b"", b"\n", b"x" * 8193, b"two\nlines", b"nul\x00", b"\xff\xfe"],
)
def test_bad_secret_files_read_as_missing(tmp_path: Path, body: bytes) -> None:
    write(tmp_path, "MARKOS_CONTROL_SESSION_KEY", body)
    with pytest.raises(FatalServiceError):
        secret(FileSecrets(str(tmp_path)), "MARKOS_CONTROL_SESSION_KEY")


def test_links_directories_and_bad_names_read_as_missing(tmp_path: Path) -> None:
    write(tmp_path, "target", b"value")
    os.symlink(tmp_path / "target", tmp_path / "MARKOS_CONTROL_LINKED")
    (tmp_path / "MARKOS_CONTROL_DIRECTORY").mkdir()
    secrets = FileSecrets(str(tmp_path))
    for name in ["MARKOS_CONTROL_LINKED", "MARKOS_CONTROL_DIRECTORY", "MARKOS_CONTROL_ABSENT"]:
        with pytest.raises(FatalServiceError):
            secret(secrets, name)
    with pytest.raises(FatalServiceError):
        secret(secrets, "../target")
    assert FileSecrets(str(tmp_path / "absent")).get("MARKOS_CONTROL_SESSION_KEY") is None


HOST = "db.abcdef.supabase.co"
CA = "/etc/markos/supabase-ca.pem"
GOOD = f"host={HOST} dbname=postgres user=markos_gateway sslmode=verify-full sslrootcert={CA}"


def test_a_verified_supabase_dsn_passes() -> None:
    production_dsn(GOOD, HOST, CA)
    production_dsn(
        f"postgresql://markos_gateway:pw@{HOST}:5432/postgres?sslmode=verify-full&sslrootcert={CA}",
        HOST,
        CA,
    )


@pytest.mark.parametrize(
    "dsn",
    [
        GOOD.replace("verify-full", "require"),
        GOOD.replace("verify-full", "verify-ca"),
        GOOD.replace(f" sslrootcert={CA}", ""),
        GOOD.replace(CA, "/tmp/other-ca.pem"),
        GOOD.replace(HOST, "127.0.0.1"),
        GOOD.replace(HOST, f"{HOST},127.0.0.1"),
        GOOD + " hostaddr=127.0.0.1",
        GOOD.replace(" dbname=postgres", ""),
        GOOD.replace(" user=markos_gateway", ""),
        "not a dsn ===",
    ],
)
def test_unverified_or_other_databases_are_refused(dsn: str) -> None:
    with pytest.raises(FatalServiceError):
        production_dsn(dsn, HOST, CA)
