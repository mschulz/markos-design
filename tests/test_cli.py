from pathlib import Path

import duckdb
from typer.testing import CliRunner

from markos import __version__
from markos.cli import app

runner = CliRunner()


def test_version() -> None:
    result = runner.invoke(app, ["version"])

    assert result.exit_code == 0
    assert f"MarkOS {__version__}" in result.stdout


def test_doctor_accepts_readable_vault(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    vault.mkdir()

    result = runner.invoke(app, ["doctor", "--vault", str(vault)])

    assert result.exit_code == 0
    assert "Readable" in result.stdout
    assert list(vault.iterdir()) == []


def test_doctor_reads_environment_configuration(tmp_path: Path) -> None:
    result = runner.invoke(app, ["doctor"], env={"MARKOS_VAULT_PATH": str(tmp_path)})

    assert result.exit_code == 0
    assert "Readable" in result.stdout


def test_doctor_rejects_missing_vault(tmp_path: Path) -> None:
    result = runner.invoke(app, ["doctor", "--vault", str(tmp_path / "missing")])

    assert result.exit_code == 1
    assert "Not found" in result.stdout


def test_doctor_reports_missing_configuration() -> None:
    result = runner.invoke(app, ["doctor"])

    assert result.exit_code == 2
    assert "Configuration error" in result.stderr


def test_rebuild_creates_external_index_without_modifying_vault(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    vault.mkdir()
    note = vault / "Note.md"
    note.write_text("# Note\n\nSee [[Other]].\n", encoding="utf-8")
    before = note.read_bytes()
    index_path = tmp_path / "data" / "index.duckdb"

    result = runner.invoke(
        app,
        ["rebuild", "--vault", str(vault), "--index", str(index_path)],
    )

    assert result.exit_code == 0
    assert "1 documents" in result.stdout
    assert "1 links" in result.stdout
    assert note.read_bytes() == before
    with duckdb.connect(str(index_path), read_only=True) as connection:
        assert connection.execute("SELECT path FROM documents").fetchall() == [("Note.md",)]


def test_rebuild_requires_index_configuration(tmp_path: Path) -> None:
    result = runner.invoke(app, ["rebuild", "--vault", str(tmp_path)])

    assert result.exit_code == 2
    assert "index path is required" in result.stderr
