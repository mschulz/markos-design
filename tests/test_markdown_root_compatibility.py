"""Migration checks for canonical root naming and deprecated public interfaces."""

from collections.abc import Callable
from pathlib import Path
from typing import cast

import pytest
from typer.testing import CliRunner

from markos import index, markdown, sync, watch
from markos.cli import app
from markos.index import IndexSummary
from markos.para_audit import ParaAuditReport
from markos.settings import Settings
from markos.sync import SyncResult


def test_settings_canonical_serialization_and_legacy_constructor(tmp_path: Path) -> None:
    legacy_constructor = cast(Callable[..., Settings], Settings)
    settings = legacy_constructor(vault_path=tmp_path)
    assert settings.markdown_root == tmp_path
    assert "vault_path" not in settings.model_dump()
    assert settings.model_dump()["markdown_root"] == tmp_path
    with pytest.warns(DeprecationWarning, match="markdown_root"):
        assert settings.vault_path == tmp_path
    with pytest.warns(DeprecationWarning, match="require_markdown_root"):
        assert Settings.load(environ={}, require_vault=False).markdown_root is None


@pytest.mark.parametrize("old_toml", [False, True])
@pytest.mark.parametrize("old_env", [False, True])
def test_environment_overrides_toml_for_both_spellings(
    tmp_path: Path, old_toml: bool, old_env: bool
) -> None:
    config = tmp_path / "markos.toml"
    key = "vault_path" if old_toml else "markdown_root"
    config.write_text(f'[markos]\n{key}="configured-notes"\n')
    env_key = "MARKOS_VAULT_PATH" if old_env else "MARKOS_MARKDOWN_ROOT"
    environment = {env_key: str(tmp_path / "environment-notes")}
    if old_toml or old_env:
        with pytest.warns(DeprecationWarning):
            settings = Settings.load(config_path=config, environ=environment)
    else:
        settings = Settings.load(config_path=config, environ=environment)
    assert settings.markdown_root == tmp_path / "environment-notes"


def test_new_spelling_wins_within_each_layer(tmp_path: Path) -> None:
    config = tmp_path / "markos.toml"
    config.write_text('[markos]\nvault_path="old-notes"\nmarkdown_root="new-notes"\n')
    assert Settings.load(config_path=config, environ={}).markdown_root == tmp_path / "new-notes"
    both = {
        "MARKOS_VAULT_PATH": str(tmp_path / "old-env"),
        "MARKOS_MARKDOWN_ROOT": str(tmp_path / "new-env"),
    }
    assert Settings.load(config_path=config, environ=both).markdown_root == tmp_path / "new-env"


def test_old_relative_config_still_resolves_against_its_file(tmp_path: Path) -> None:
    config = tmp_path / "markos.toml"
    config.write_text('[markos]\nvault_path="notes"\nindex_path="data/index.duckdb"\n')
    with pytest.warns(DeprecationWarning):
        settings = Settings.load(config_path=config, environ={})
    assert settings.markdown_root == tmp_path / "notes"
    assert settings.index_path == tmp_path / "data/index.duckdb"


@pytest.mark.parametrize("flag", ["--markdown-root", "--vault"])
def test_cli_flag_overrides_legacy_environment(flag: str, tmp_path: Path) -> None:
    notes = tmp_path / "notes"
    notes.mkdir()
    result = CliRunner().invoke(
        app,
        ["doctor", flag, str(notes)],
        env={
            "MARKOS_VAULT_PATH": str(tmp_path / "missing"),
            "MARKOS_MARKDOWN_ROOT": "",
            "MARKOS_CONFIG": "",
        },
    )
    assert result.exit_code == 0, result.output
    assert "Markdown root" in result.output
    assert not list(notes.iterdir())


def test_cli_help_uses_canonical_folder_language() -> None:
    result = CliRunner().invoke(app, ["doctor", "--help"])
    assert result.exit_code == 0
    assert "--markdown-root" in result.output
    assert "Obsidian" not in result.output


def test_deprecated_python_functions_keep_keyword_and_read_only_behavior(tmp_path: Path) -> None:
    notes = tmp_path / "notes"
    notes.mkdir()
    note = notes / "Note.md"
    note.write_text("# A normal Markdown folder\n\n[[Other]]\n")
    original = note.read_bytes()
    destination = tmp_path / "index.duckdb"
    rebuild = cast(Callable[..., IndexSummary], index.rebuild_vault_index)
    with pytest.warns(DeprecationWarning):
        result = rebuild(vault_path=notes, index_path=destination)
    assert result.document_count == 1
    before = destination.read_bytes()
    synchronize = cast(Callable[..., SyncResult], sync.sync_vault_index)
    with pytest.warns(DeprecationWarning):
        assert not synchronize(vault_path=notes, index_path=destination).rebuilt
    assert destination.read_bytes() == before
    assert note.read_bytes() == original
    assert watch.snapshot_markdown_root(notes)
    with pytest.warns(DeprecationWarning):
        assert watch.snapshot_vault(notes) == watch.snapshot_markdown_root(notes)


def test_unchanged_public_function_accepts_old_keyword(tmp_path: Path) -> None:
    (tmp_path / "Note.md").write_text("# Note\n")
    discover = cast(Callable[..., tuple[Path, ...]], markdown.discover_markdown)
    with pytest.warns(DeprecationWarning):
        assert discover(vault_path=tmp_path) == (Path("Note.md"),)
    with pytest.raises(TypeError, match="not both"):
        discover(vault_path=tmp_path, markdown_root=tmp_path)


def test_audit_report_keeps_deprecated_constructor_and_property(tmp_path: Path) -> None:
    with pytest.warns(DeprecationWarning):
        report = ParaAuditReport(vault_path=tmp_path, folders=())
    assert report.markdown_root == tmp_path
    with pytest.warns(DeprecationWarning):
        assert report.vault_path == tmp_path
