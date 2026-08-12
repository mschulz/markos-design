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


def test_search_reads_index_without_vault_configuration(tmp_path: Path) -> None:
    index_path = tmp_path / "index.duckdb"
    vault = tmp_path / "vault"
    vault.mkdir()
    (vault / "Vision.md").write_text("# Vision\n\nKnowledge outlives tools.\n", encoding="utf-8")
    rebuild_result = runner.invoke(
        app,
        ["rebuild", "--vault", str(vault), "--index", str(index_path)],
    )
    assert rebuild_result.exit_code == 0

    result = runner.invoke(
        app,
        ["search", "knowledge", "--index", str(index_path)],
        env={"MARKOS_VAULT_PATH": ""},
    )

    assert result.exit_code == 0
    assert "Vision.md" in result.stdout
    assert "Knowledge outlives tools" in result.stdout


def test_search_reports_no_matches(tmp_path: Path) -> None:
    index_path = tmp_path / "index.duckdb"
    vault = tmp_path / "vault"
    vault.mkdir()
    (vault / "Note.md").write_text("# Note", encoding="utf-8")
    runner.invoke(app, ["rebuild", "--vault", str(vault), "--index", str(index_path)])

    result = runner.invoke(app, ["search", "absent", "--index", str(index_path)])

    assert result.exit_code == 0
    assert "No matching documents" in result.stdout


def test_links_shows_outgoing_and_incoming_links(tmp_path: Path) -> None:
    index_path = tmp_path / "index.duckdb"
    vault = tmp_path / "vault"
    vault.mkdir()
    (vault / "One.md").write_text("# One\n\nSee [[Two]] and [[Missing]].", encoding="utf-8")
    (vault / "Two.md").write_text("# Two", encoding="utf-8")
    runner.invoke(app, ["rebuild", "--vault", str(vault), "--index", str(index_path)])

    outgoing = runner.invoke(app, ["links", "One.md", "--index", str(index_path)])
    incoming = runner.invoke(
        app,
        ["links", "Two.md", "--incoming", "--index", str(index_path)],
    )

    assert outgoing.exit_code == 0
    assert "Two.md" in outgoing.stdout
    assert "Unresolved" in outgoing.stdout
    assert incoming.exit_code == 0
    assert "One.md" in incoming.stdout


def test_serve_starts_read_only_api_without_vault(
    tmp_path: Path,
    monkeypatch: object,
) -> None:
    calls: list[tuple[object, str, int]] = []

    def fake_run(api: object, *, host: str, port: int) -> None:
        calls.append((api, host, port))

    monkeypatch.setattr("markos.cli.uvicorn.run", fake_run)  # type: ignore[attr-defined]

    result = runner.invoke(
        app,
        ["serve", "--index", str(tmp_path / "index.duckdb"), "--port", "9000"],
        env={"MARKOS_VAULT_PATH": ""},
    )

    assert result.exit_code == 0
    assert len(calls) == 1
    assert calls[0][1:] == ("127.0.0.1", 9000)
