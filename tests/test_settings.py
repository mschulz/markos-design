from pathlib import Path

import pytest

from markos.settings import Settings, SettingsError


def test_environment_loads_settings(tmp_path: Path) -> None:
    settings = Settings.load(
        environ={
            "MARKOS_MARKDOWN_ROOT": str(tmp_path),
            "MARKOS_INDEX_PATH": str(tmp_path / "index.duckdb"),
            "MARKOS_LOG_LEVEL": "debug",
        }
    )

    assert settings.markdown_root == tmp_path
    assert settings.index_path == tmp_path / "index.duckdb"
    assert settings.log_level == "DEBUG"


def test_environment_overrides_configuration(tmp_path: Path) -> None:
    config_path = tmp_path / "markos.toml"
    config_path.write_text(
        '[markos]\nmarkdown_root = "configured-markdown_root"\nlog_level = "WARNING"\n',
        encoding="utf-8",
    )
    override = tmp_path / "override-markdown_root"

    settings = Settings.load(
        environ={"MARKOS_MARKDOWN_ROOT": str(override)}, config_path=config_path
    )

    assert settings.markdown_root == override
    assert settings.log_level == "WARNING"


def test_relative_paths_are_resolved_from_config_file(tmp_path: Path) -> None:
    config_path = tmp_path / "markos.toml"
    config_path.write_text(
        '[markos]\nmarkdown_root = "markdown_root"\nindex_path = "data/index.duckdb"\n',
        encoding="utf-8",
    )

    settings = Settings.load(environ={}, config_path=config_path)

    assert settings.markdown_root == tmp_path / "markdown_root"
    assert settings.index_path == tmp_path / "data/index.duckdb"


def test_markdown_root_is_required() -> None:
    with pytest.raises(SettingsError, match="markdown_root path is required"):
        Settings.load(environ={})


def test_index_only_settings_do_not_require_markdown_root() -> None:
    settings = Settings.load(
        environ={"MARKOS_INDEX_PATH": "/tmp/index.duckdb"},
        require_markdown_root=False,
    )

    assert settings.markdown_root is None
    assert settings.index_path == Path("/tmp/index.duckdb")
