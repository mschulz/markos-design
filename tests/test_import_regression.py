"""M0.0 import safeguards; runtime startup isolation belongs to M0.1."""

import os
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

from markos.settings import Settings, SettingsError

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_project_configuration_selects_no_personal_vault_or_index() -> None:
    config_path = PROJECT_ROOT / "pyproject.toml"
    with config_path.open("rb") as handle:
        config = tomllib.load(handle)

    assert "vault_path" not in config.get("markos", {})
    assert "index_path" not in config.get("markos", {})
    settings = Settings.load(environ={}, config_path=config_path, require_vault=False)
    assert settings.vault_path is None
    assert settings.index_path is None
    with pytest.raises(SettingsError, match="vault path is required"):
        Settings.load(environ={}, config_path=config_path)


def test_importing_application_does_not_open_config_vault_or_network(tmp_path: Path) -> None:
    config = tmp_path / "must-not-read.toml"
    config.write_text("not valid TOML: application import must not load this", encoding="utf-8")
    vault = tmp_path / "synthetic-vault"
    vault.mkdir()
    note = vault / "Note.md"
    note.write_text("# Synthetic note\n", encoding="utf-8")
    before = note.read_bytes()
    env = os.environ.copy()
    env.update(
        MARKOS_CONFIG=str(config),
        MARKOS_VAULT_PATH=str(vault),
        MARKOS_INDEX_PATH=str(tmp_path / "must-not-create.duckdb"),
    )
    script = r'''
import importlib
import os
import sys

config = os.environ["MARKOS_CONFIG"]
vault = os.environ["MARKOS_VAULT_PATH"]
index = os.environ["MARKOS_INDEX_PATH"]

def guard(event, args):
    if event == "socket.connect":
        raise AssertionError("application import attempted a network connection")
    if event in {"open", "os.listdir", "os.scandir"} and args:
        path = args[0]
        if isinstance(path, (str, bytes, os.PathLike)):
            path = os.fsdecode(path)
            if path in {config, vault, index} or path.startswith(vault + os.sep):
                raise AssertionError("application import accessed configured user data")

sys.addaudithook(guard)
settings = importlib.import_module("markos.settings")

def reject_settings(*args, **kwargs):
    raise AssertionError("application import loaded legacy settings")

settings.Settings.load = reject_settings
importlib.import_module("markos.cli")
importlib.import_module("markos.api")
print("import-safe")
'''
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "import-safe"
    assert note.read_bytes() == before
    assert not (tmp_path / "must-not-create.duckdb").exists()
