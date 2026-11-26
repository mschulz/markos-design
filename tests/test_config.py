import json
from collections.abc import Iterator, Mapping
from pathlib import Path

import pytest
from pydantic import ValidationError

from markos_runtime.cli import main
from markos_runtime.config import ProposedLimits, load_config
from markos_runtime.contracts import ContractError


def test_explicit_config_and_relative_manifest(
    config_file: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    config, manifest = load_config(config_file, {})
    assert config.service_id == "foundation-generator"
    assert len(manifest.subscriptions) == 1
    assert load_config(None, {"MARKOS_RUNTIME_CONFIG": str(config_file)}) == (config, manifest)
    assert not hasattr(config, "markdown_root")


def test_explicit_cli_path_overrides_env(config_file: Path) -> None:
    assert load_config(config_file, {"MARKOS_RUNTIME_CONFIG": "/does/not/exist"})[0].role == (
        "publisher"
    )


def test_no_legacy_discovery(tmp_path: Path) -> None:
    legacy = tmp_path / "forbidden.toml"
    legacy.write_text("THIS MUST NOT BE PARSED")
    for name in ["MARKOS_CONFIG", "MARKOS_VAULT_PATH", "MARKOS_MARKDOWN_ROOT", "MARKOS_INDEX_PATH"]:
        with pytest.raises(ContractError, match="explicit runtime"):
            load_config(None, {name: str(legacy)})


@pytest.mark.parametrize(
    "edit",
    [
        "environment-production",
        "unknown-field",
        "inline-secret",
        "duplicate-secret",
        "parent-manifest",
        "absolute-manifest",
        "wrong-role",
        "wrong-service",
        "extra-table",
        "malformed",
        "oversize",
        "nonfinite",
        "nonstring-manifest",
    ],
)
def test_invalid_config_is_redacted(config_file: Path, edit: str) -> None:
    text = config_file.read_text()
    if edit == "environment-production":
        text = text.replace('environment="test"', 'environment="production"')
    elif edit == "unknown-field":
        text += 'markdown_root="DO_NOT_ECHO_PRIVATE"\n'
    elif edit == "inline-secret":
        text += 'database_secret_ref="postgresql://DO_NOT_ECHO_PRIVATE"\n'
    elif edit == "duplicate-secret":
        text += 'database_secret_ref="MARKOS_RUNTIME_BROKER_DSN"\n'
    elif edit == "parent-manifest":
        text = text.replace("subscriptions.json", "../subscriptions.json")
    elif edit == "absolute-manifest":
        text = text.replace("subscriptions.json", "/tmp/subscriptions.json")
    elif edit == "wrong-role":
        text = text.replace('role="publisher"', 'role="consumer"')
    elif edit == "wrong-service":
        text = text.replace("foundation-generator", "unknown")
    elif edit == "extra-table":
        text += '[legacy]\nvault_path="DO_NOT_ECHO_PRIVATE"\n'
    elif edit == "malformed":
        text = "[runtime\nDO_NOT_ECHO_PRIVATE"
    elif edit == "oversize":
        text = "#" * 262145
    elif edit == "nonfinite":
        text += "[runtime.limits]\nvm_cpus=nan\n"
    else:
        text = text.replace('"subscriptions.json"', "123")
    config_file.write_text(text)
    with pytest.raises(ContractError) as error:
        load_config(config_file, {})
    assert "DO_NOT_ECHO" not in str(error.value)
    assert error.value.__suppress_context__


def test_manifest_cannot_be_symlink(config_file: Path) -> None:
    manifest = config_file.parent / "subscriptions.json"
    original = config_file.parent / "original.json"
    manifest.rename(original)
    manifest.symlink_to(original)
    with pytest.raises(ContractError):
        load_config(config_file, {})


@pytest.mark.parametrize(
    "values",
    [
        {"vm_cpus": 2},
        {"vm_memory_mib": 4000},
        {"memory_alarm_mib": 4096},
        {"disk_alarm_mib": 20480},
        {"active_handoff_seconds": 5, "total_delivery_seconds": 5},
        {"prefetch": 1},
        {"max_message_bytes": 65537},
        {"queue_max_messages": 10001},
        {"max_queues": 65},
        {"max_connections": 33},
        {"channels_per_connection": 33},
        {"replay_days": 29},
        {"replay_days": 31},
        {"dedup_days": 89},
        {"replay_days": 91},
        {"terminal_retention_days": 29},
        {"prefetch": True},
        {"prefetch": "10"},
        {"prefetch": 10.0},
    ],
)
def test_incoherent_profile_rejected(values: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        ProposedLimits.model_validate(values)


def test_valid_profile_is_planning_only() -> None:
    limits = ProposedLimits()
    assert (limits.replay_days, limits.terminal_retention_days, limits.dedup_days) == (30, 30, 90)
    assert (limits.prefetch, limits.handoffs_per_process) == (10, 2)


def test_cli_success_and_redacted_failures(
    config_file: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["preflight", "--config", str(config_file)]) == 0
    assert "validation only" in capsys.readouterr().out
    config_file.write_text("DO_NOT_ECHO_PRIVATE")
    assert main(["preflight", "--config", str(config_file)]) == 2
    result = capsys.readouterr()
    assert "DO_NOT_ECHO" not in result.err
    assert str(tmp_path) not in result.err


def test_cli_event_scope(
    config_file: Path,
    event_document: dict[str, object],
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    event = tmp_path / "event.json"
    event.write_text(json.dumps(event_document))
    args = ["preflight", "--config", str(config_file), "--event", str(event)]
    assert main(args) == 0
    event_document["source"] = "forged-publisher"
    event.write_text(json.dumps(event_document))
    assert main(args) == 2
    assert "forged-publisher" not in capsys.readouterr().err


def test_environment_namespaces_broker_vhost(config_file: Path) -> None:
    config, _ = load_config(config_file, {})
    assert config.broker_vhost == "markos-test"
    development = config.model_copy(update={"environment": "development"})
    assert development.broker_vhost == "markos-development"


def test_preflight_never_resolves_secret_values(config_file: Path) -> None:
    class ReferenceOnlyEnvironment(Mapping[str, str]):
        def __getitem__(self, key: str) -> str:
            assert key == "MARKOS_RUNTIME_CONFIG", "unexpected environment lookup"
            return str(config_file)

        def __iter__(self) -> Iterator[str]:
            return iter(("MARKOS_RUNTIME_CONFIG",))

        def __len__(self) -> int:
            return 1

    config, _ = load_config(None, ReferenceOnlyEnvironment())
    assert config.broker_secret_ref == "MARKOS_RUNTIME_BROKER_DSN"
