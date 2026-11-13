"""Closed fixture launcher config must not widen auth, destinations or implicit startup."""

import json
from typing import Any
from uuid import UUID

import pytest

from markos_runtime.contracts.base import ContractError
from markos_runtime.control_lifecycle.config import ControlServiceConfig


def config() -> dict[str, Any]:
    return {
        "schema_version": 1,
        "profile": "control-process-fixture-v1",
        "component": "gateway",
        "service_id": "control-gateway",
        "owner_id": "11111111-1111-4111-8111-111111111111",
        "domain": "personal",
        "health_port": 8780,
        "ca_file": "/fixture/ca.pem",
        "certificate_file": "/fixture/server.pem",
        "private_key_file": "/fixture/key.pem",
        "fixture_state_file": "/fixture/state.json",
        "fixture_seed_file": "/fixture/seed.json",
        "broker_publisher_user": "synthetic-publisher",
    }


@pytest.mark.parametrize(
    "key,value",
    [
        ("profile", "production"),
        ("environment", "production"),
        ("component", "shell"),
        ("service_id", "control-test-agent"),
        ("issuer_endpoint", "https://example.com/fixture"),
        ("tool_endpoint", "http://127.0.0.1:19443/mcp"),
        ("migrate_on_start", True),
        ("real_accounts_enabled", 0),
        ("real_tools_enabled", True),
        ("health_port", True),
        ("work_seconds", 10.0),
        ("fixture_state_file", "../secrets.json"),
        ("audit_url", "https://example.com"),
        ("unknown", True),
    ],
)
def test_process_profile_fails_closed(key: str, value: Any) -> None:
    document = {**config(), key: value}
    with pytest.raises(ContractError):
        ControlServiceConfig.from_wire(json.dumps(document).encode())


def test_process_profile_keeps_preflight_inert_and_secrets_explicit() -> None:
    profile = ControlServiceConfig.from_wire(json.dumps(config()).encode())
    assert profile.owner_id == UUID(config()["owner_id"])
    assert profile.listen_host == "127.0.0.1"
    assert profile.migrate_on_start is False and profile.real_accounts_enabled is False
    assert profile.real_tools_enabled is False
    assert profile.database_secret_ref == "MARKOS_CONTROL_DATABASE_DSN"
