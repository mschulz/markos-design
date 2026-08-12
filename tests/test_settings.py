from pathlib import Path

import pytest

from markos.settings import Settings, SettingsError


def test_environment_loads_settings(tmp_path: Path) -> None:
    settings = Settings.load(
        environ={"MARKOS_VAULT_PATH": str(tmp_path), "MARKOS_LOG_LEVEL": "debug"}
    )

    assert settings.vault_path == tmp_path
    assert settings.log_level == "DEBUG"


def test_environment_overrides_configuration(tmp_path: Path) -> None:
    config_path = tmp_path / "markos.toml"
    config_path.write_text(
        '[markos]\nvault_path = "configured-vault"\nlog_level = "WARNING"\n',
        encoding="utf-8",
    )
    override = tmp_path / "override-vault"

    settings = Settings.load(environ={"MARKOS_VAULT_PATH": str(override)}, config_path=config_path)

    assert settings.vault_path == override
    assert settings.log_level == "WARNING"


def test_relative_vault_path_is_resolved_from_config_file(tmp_path: Path) -> None:
    config_path = tmp_path / "markos.toml"
    config_path.write_text('[markos]\nvault_path = "vault"\n', encoding="utf-8")

    settings = Settings.load(environ={}, config_path=config_path)

    assert settings.vault_path == tmp_path / "vault"


def test_vault_path_is_required() -> None:
    with pytest.raises(SettingsError, match="vault path is required"):
        Settings.load(environ={})
