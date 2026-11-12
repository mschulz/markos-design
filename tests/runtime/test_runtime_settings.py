import json
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from markos.runtime.bootstrap import validate_startup
from markos.runtime.settings import ResourceLimits, RuntimeConfigurationError, RuntimeSettings


def test_explicit_config_and_runtime_env_selection(runtime_config: Path) -> None:
    settings, manifest = RuntimeSettings.load(runtime_config, environ={})
    assert manifest == runtime_config.parent / "runtime.subscriptions.json"
    assert settings.scope.service_id == "foundation.publisher"
    assert settings.limits.memory_alarm_mib == 2048
    assert settings.replay_window_days == 30 and settings.dedup_retention_days == 90
    loaded, _ = RuntimeSettings.load(environ={"MARKOS_RUNTIME_CONFIG": str(runtime_config)})
    assert loaded == settings
    explicit, _ = RuntimeSettings.load(runtime_config,
                                      environ={"MARKOS_RUNTIME_CONFIG": "/must-not-open"})
    assert explicit == settings


def test_legacy_config_does_not_select_runtime_file(tmp_path: Path) -> None:
    with pytest.raises(RuntimeConfigurationError, match="explicit runtime"):
        RuntimeSettings.load(environ={"MARKOS_CONFIG": str(tmp_path / "not-runtime.toml")})


@pytest.mark.parametrize("content", [
    "[markos]\nvault_path='must-not-open'", "broken [ secret-password",
    "[runtime]\ndatabase_dsn='secret-password'", "x" * 65537,
])
def test_bad_config_is_bounded_and_redacted(content: str, tmp_path: Path) -> None:
    path = tmp_path / "runtime.toml"
    path.write_text(content)
    with pytest.raises(RuntimeConfigurationError) as raised:
        RuntimeSettings.load(path, environ={})
    assert "secret-password" not in str(raised.value)


def test_unknown_setting_and_path_traversal_fail(runtime_config: Path) -> None:
    settings, _ = RuntimeSettings.load(runtime_config, environ={})
    values = settings.model_dump(mode="json")
    for update in ({"topology_file": "../private.json"}, {"vault_path": "private"},
                   {"broker_dsn_env": "MARKOS_CONFIG"}, {"replay_window_days": 31},
                   {"replay_window_days": 30.0}):
        with pytest.raises(ValidationError):
            RuntimeSettings.model_validate_json(json.dumps(values | update))


@pytest.mark.parametrize("mutation", ["role", "environment", "budget", "manifest"])
def test_preflight_rejects_role_environment_budget_and_manifest_mismatch(
    mutation: str, runtime_config: Path, runtime_env: dict[str, str]
) -> None:
    if mutation == "role":
        runtime_config.write_text(runtime_config.read_text().replace('role = "publisher"',
                                                                   'role = "consumer"'))
    elif mutation == "budget":
        with runtime_config.open("a") as handle:
            handle.write("\n[runtime.limits]\nmax_queues = 1\n")
    else:
        manifest = runtime_config.parent / "runtime.subscriptions.json"
        body = manifest.read_text()
        manifest.write_text(body.replace('"test"', '"production"')
                            if mutation == "environment" else "{broken secret-password")
    with pytest.raises(RuntimeConfigurationError, match="preflight failed") as raised:
        validate_startup(runtime_config, environ=runtime_env)
    assert "secret-password" not in str(raised.value)


@pytest.mark.parametrize("changes", [
    {"vm_cpus": 2}, {"vm_memory_mib": 4096}, {"memory_alarm_mib": 4096},
    {"disk_free_warning_mib": 10240}, {"prefetch": 1}, {"handoff_timeout_seconds": 2},
    {"delivery_timeout_seconds": 10}, {"max_inflight_confirms": 101},
    {"max_connections": 33}, {"queue_max_bytes": 1}, {"prefetch": True},
])
def test_inconsistent_resource_budgets_fail(changes: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        ResourceLimits.model_validate(changes)


def test_credentials_are_required_and_never_echoed(
    runtime_config: Path, runtime_env: dict[str, str]
) -> None:
    prepared = validate_startup(runtime_config, environ=runtime_env)
    assert "synthetic:synthetic" not in repr(prepared)
    assert "synthetic:synthetic" not in prepared.model_dump_json()
    for env in ({}, runtime_env | {"MARKOS_RUNTIME_DATABASE_DSN": "secret-password"}):
        with pytest.raises(RuntimeConfigurationError) as raised:
            validate_startup(runtime_config, environ=env)
        assert "secret-password" not in str(raised.value)


def test_registered_consumer_can_validate_its_own_role(
    runtime_config: Path, runtime_env: dict[str, str]
) -> None:
    body = runtime_config.read_text().replace('role = "publisher"', 'role = "consumer"')
    runtime_config.write_text(body.replace('service_id = "foundation.publisher"',
                                         'service_id = "foundation.worker"'))
    result = validate_startup(runtime_config, environ=runtime_env)
    assert result.settings.role == "consumer"
    assert result.settings.scope.service_id == "foundation.worker"


def test_production_requires_encrypted_verified_endpoints(
    runtime_config: Path, runtime_env: dict[str, str]
) -> None:
    runtime_config.write_text(runtime_config.read_text().replace('"test"', '"production"'))
    manifest = runtime_config.parent / "runtime.subscriptions.json"
    manifest.write_text(manifest.read_text().replace('"test"', '"production"'))
    with pytest.raises(RuntimeConfigurationError):
        validate_startup(runtime_config, environ=runtime_env)
    secure = {
        "MARKOS_RUNTIME_BROKER_DSN": "amqps://synthetic:synthetic@broker.invalid/test",
        "MARKOS_RUNTIME_DATABASE_DSN":
            "postgresql://synthetic:synthetic@database.invalid/test?sslmode=verify-full",
    }
    assert validate_startup(runtime_config, environ=secure).settings.environment == "production"
    secure["MARKOS_RUNTIME_DATABASE_DSN"] += "&sslmode=disable"
    with pytest.raises(RuntimeConfigurationError):
        validate_startup(runtime_config, environ=secure)


@pytest.mark.parametrize("dsn", [
    "amqp://u:p@broker.invalid/test", "amqp://localhost/test",
    "amqps://u:p@host.invalid:99999/test", "amqps://u:p@host.invalid/test\n",
    "amqps://u:p@host.invalid/test?no_verify_ssl=1",
])
def test_bad_or_remote_plaintext_broker_credentials_fail(
    dsn: str, runtime_config: Path, runtime_env: dict[str, str]
) -> None:
    with pytest.raises(RuntimeConfigurationError):
        validate_startup(runtime_config, environ=runtime_env | {"MARKOS_RUNTIME_BROKER_DSN": dsn})


@pytest.mark.parametrize("query", ["options=unsafe", "sslmode=disable&sslmode=verify-full"])
def test_database_endpoint_cannot_override_unreviewed_options(
    query: str, runtime_config: Path, runtime_env: dict[str, str]
) -> None:
    env = runtime_env | {"MARKOS_RUNTIME_DATABASE_DSN":
                         "postgresql://u:p@localhost/test?" + query}
    with pytest.raises(RuntimeConfigurationError):
        validate_startup(runtime_config, environ=env)
