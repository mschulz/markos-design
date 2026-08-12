from pathlib import Path

import pytest

from markos.settings import Settings, SettingsError


def test_environment_loads_settings(tmp_path: Path) -> None:
    settings = Settings.load(
        environ={
            "MARKOS_VAULT_PATH": str(tmp_path),
            "MARKOS_INDEX_PATH": str(tmp_path / "index.duckdb"),
            "MARKOS_LOG_LEVEL": "debug",
        }
    )

    assert settings.vault_path == tmp_path
    assert settings.index_path == tmp_path / "index.duckdb"
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


def test_relative_paths_are_resolved_from_config_file(tmp_path: Path) -> None:
    config_path = tmp_path / "markos.toml"
    config_path.write_text(
        '[markos]\nvault_path = "vault"\nindex_path = "data/index.duckdb"\n',
        encoding="utf-8",
    )

    settings = Settings.load(environ={}, config_path=config_path)

    assert settings.vault_path == tmp_path / "vault"
    assert settings.index_path == tmp_path / "data/index.duckdb"


def test_vault_path_is_required() -> None:
    with pytest.raises(SettingsError, match="vault path is required"):
        Settings.load(environ={})


def test_index_only_settings_do_not_require_vault() -> None:
    settings = Settings.load(
        environ={"MARKOS_INDEX_PATH": "/tmp/index.duckdb"},
        require_vault=False,
    )

    assert settings.vault_path is None
    assert settings.index_path == Path("/tmp/index.duckdb")
