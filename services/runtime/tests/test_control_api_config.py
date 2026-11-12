"""API fixture configuration remains independent of optional server libraries."""

import json
from pathlib import Path

import pytest

from markos_runtime.contracts.base import ContractError
from markos_runtime.control.api_config import SyntheticApiConfig


@pytest.mark.parametrize(
    "change",
    [
        {"api_origin": "http://127.0.0.1:18443"},
        {"api_origin": "https://public.example:18443"},
        {"api_origin": "https://127.0.0.1:443"},
        {"api_origin": "https://127.0.0.1:18443/callback"},
        {"api_origin": "https://user:secret@127.0.0.1:18443"},
        {"api_origin": "https://127.0.0.1:18443?token=secret"},
        {"api_origin": "https://127.0.0.1:18443#fragment"},
        {"identity_profile": "real-provider"},
        {"environment": "production"},
        {"real_accounts_enabled": True},
        {"chat_execution_enabled": True},
        {"tool_execution_enabled": True},
        {"real_accounts_enabled": 0},
        {"gateway_enabled": True},
    ],
)
def test_fixture_profile_rejects_enablement_and_unapproved_origin(
    change: dict[str, object],
) -> None:
    value = {"schema_version": 1, "api_origin": "https://127.0.0.1:18443", **change}
    with pytest.raises(ContractError):
        SyntheticApiConfig.from_wire(json.dumps(value).encode())


def test_fixture_profile_example() -> None:
    example = Path(__file__).resolve().parents[3] / "config/runtime/control/api.example.json"
    profile = SyntheticApiConfig.from_wire(example.read_bytes())
    assert profile.api_origin == "https://127.0.0.1:18443"
    assert not profile.real_accounts_enabled and not profile.chat_execution_enabled


def test_packaged_api_schema_fingerprints_match_reviewed_migrations() -> None:
    from hashlib import sha256

    from markos_runtime.api.schema import EXPECTED_MIGRATIONS

    directory = Path(__file__).resolve().parents[3] / "infrastructure/migrations"
    actual = {
        str(int(p.name[:4])): sha256(p.read_bytes()).hexdigest() for p in directory.glob("*.sql")
    }
    assert actual == EXPECTED_MIGRATIONS
