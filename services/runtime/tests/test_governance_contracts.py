"""Closed fixture contract boundaries remain independent of optional MCP libraries."""

import json
from pathlib import Path

import pytest

from markos_runtime.contracts.base import ContractError
from markos_runtime.contracts.governance import GovernedChatRequest, ReviewedFixtureGrant
from markos_runtime.control.api_config import SyntheticGovernanceApiConfig, SyntheticWebApiConfig

ROOT = Path(__file__).resolve().parents[3] / "config/runtime/control"


@pytest.mark.parametrize(
    "endpoint",
    [
        "http://127.0.0.1:19443/mcp",
        "https://localhost:19443/mcp",
        "https://169.254.169.254:19443/mcp",
        "https://127.0.0.1:443/mcp",
        "https://127.0.0.1:65536/mcp",
        "https://127.0.0.1:19443/other",
        "https://user:secret@127.0.0.1:19443/mcp",
        "https://127.0.0.1:19443/mcp?token=private",
        "https://127.0.0.1:19443/mcp#fragment",
        "https://127.0.0.2:19443/mcp",
    ],
)
def test_no_remote_dns_redirect_metadata_or_ambient_endpoint(endpoint: str) -> None:
    value = json.loads((ROOT / "reviewed-fixture-grant.example.json").read_text())
    with pytest.raises(ContractError):
        ReviewedFixtureGrant.from_wire(json.dumps({**value, "endpoint": endpoint}).encode())


@pytest.mark.parametrize(
    "change",
    [
        {"tool": "fixture.mutate"},
        {"server_id": "arbitrary-server"},
        {"tool_schema_sha256": "0" * 64},
        {"effect": "unknown"},
        {"data_class": "private"},
        {"recipient": "third-party"},
        {"protocol": "2025-11-25"},
        {"enabled": 1},
        {"require_approval": "false"},
        {"revision": 2},
        {"wildcard": "*"},
        {"credential": "secret"},
    ],
)
def test_grant_cannot_expand_closed_synthetic_profile(change: dict[str, object]) -> None:
    value = json.loads((ROOT / "reviewed-fixture-grant.example.json").read_text())
    with pytest.raises(ContractError):
        ReviewedFixtureGrant.from_wire(json.dumps({**value, **change}).encode())


def test_separate_governance_profile_and_safe_examples() -> None:
    value = (ROOT / "governance-api.example.json").read_bytes()
    profile = SyntheticGovernanceApiConfig.from_wire(value)
    assert profile.governance_profile == "synthetic-governance-v1"
    assert not profile.real_accounts_enabled and not profile.tool_execution_enabled
    with pytest.raises(ContractError):
        SyntheticWebApiConfig.from_wire(value)
    assert not ReviewedFixtureGrant.from_wire(
        (ROOT / "reviewed-fixture-grant.example.json").read_bytes()
    ).enabled


@pytest.mark.parametrize("label,allowed", [("界" * 256, True), ("界" * 257, False)])
def test_fixture_argument_exact_character_bound(label: str, allowed: bool) -> None:
    value = json.loads((ROOT / "governed-chat-request.example.json").read_text())
    value["fixture_arguments"]["label"] = label
    if allowed:
        assert (
            GovernedChatRequest.from_wire(json.dumps(value).encode()).fixture_arguments.label
            == label
        )
    else:
        with pytest.raises(ContractError):
            GovernedChatRequest.from_wire(json.dumps(value).encode())
