import json
from pathlib import Path

import duckdb
import pytest
from typer.testing import CliRunner

from markos import __version__
from markos.cli import app
from markos.semantic_extraction import ExtractionSummary
from markos.watch import WatchSummary

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


def test_graph_shows_health_and_shortest_path(tmp_path: Path) -> None:
    index_path = tmp_path / "index.duckdb"
    vault = tmp_path / "vault"
    vault.mkdir()
    (vault / "One.md").write_text("# One\n\n[[Two]]", encoding="utf-8")
    (vault / "Two.md").write_text("# Two", encoding="utf-8")
    runner.invoke(app, ["rebuild", "--vault", str(vault), "--index", str(index_path)])

    health = runner.invoke(app, ["graph", "--index", str(index_path)])
    path = runner.invoke(
        app,
        ["graph", "--index", str(index_path), "--from", "One.md", "--to", "Two.md"],
    )

    assert health.exit_code == 0
    assert "Resolved links" in health.stdout
    assert path.exit_code == 0
    assert "One.md → Two.md" in path.stdout


def test_rebuild_reports_and_lists_ingestion_issues(tmp_path: Path) -> None:
    index_path = tmp_path / "index.duckdb"
    vault = tmp_path / "vault"
    vault.mkdir()
    (vault / "Valid.md").write_text("# Valid", encoding="utf-8")
    (vault / "Broken.md").write_text(
        "---\npublication: Make:\n---\n# Broken",
        encoding="utf-8",
    )

    rebuilt = runner.invoke(
        app,
        ["rebuild", "--vault", str(vault), "--index", str(index_path)],
    )
    listed = runner.invoke(app, ["issues", "--index", str(index_path)])

    assert rebuilt.exit_code == 0
    assert "rejected 1 documents" in rebuilt.stdout
    assert listed.exit_code == 0
    assert "Broken.md" in listed.stdout
    assert "invalid frontmatter" in listed.stdout


def test_graph_caps_isolated_document_preview(tmp_path: Path) -> None:
    index_path = tmp_path / "index.duckdb"
    vault = tmp_path / "vault"
    vault.mkdir()
    for number in range(25):
        (vault / f"Note-{number:02}.md").write_text(f"# Note {number}", encoding="utf-8")
    runner.invoke(app, ["rebuild", "--vault", str(vault), "--index", str(index_path)])

    result = runner.invoke(app, ["graph", "--index", str(index_path)])

    assert result.exit_code == 0
    assert "Note-19.md" in result.stdout
    assert "Note-20.md" not in result.stdout
    assert "and 5 more isolated documents" in result.stdout


def test_validate_graph_reports_success_and_rejection(tmp_path: Path) -> None:
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    (corpus / "Note.md").write_text("# Note\n", encoding="utf-8")
    graph = tmp_path / "graph.json"
    graph.write_text(
        '{"nodes":[{"id":"note","label":"Note","source_file":"Note.md",'
        '"source_location":"L1"}],"edges":[]}',
        encoding="utf-8",
    )

    passed = runner.invoke(app, ["validate-graph", str(graph), "--corpus", str(corpus)])
    graph.write_text(
        '{"nodes":[{"id":"fake","label":"Fake","source_file":"Fake.md",'
        '"source_location":null}],"edges":[]}',
        encoding="utf-8",
    )
    rejected = runner.invoke(app, ["validate-graph", str(graph), "--corpus", str(corpus)])

    assert passed.exit_code == 0
    assert "Graph passed validation" in passed.stdout
    assert rejected.exit_code == 1
    assert "Graph rejected" in rejected.stdout
    assert "unknown_source_file" in rejected.stdout


def test_prepare_remap_and_validate_graph_workflow(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    (source / "Note.md").write_text("# Note\n\nGrounded text.\n", encoding="utf-8")
    prepared = tmp_path / "prepared"

    preparation = runner.invoke(
        app,
        ["prepare-graph-corpus", str(source), "--output", str(prepared)],
    )
    manifest = json.loads((prepared / "source-map.json").read_text(encoding="utf-8"))
    chunk_path = manifest["chunks"][0]["chunk_path"]
    graph = tmp_path / "graph.json"
    graph.write_text(
        json.dumps(
            {
                "nodes": [
                    {"id": "note", "label": "Note", "source_file": chunk_path}
                ],
                "edges": [],
            }
        ),
        encoding="utf-8",
    )
    remapped = tmp_path / "remapped.json"
    remapping = runner.invoke(
        app,
        [
            "remap-graph-sources",
            str(graph),
            "--manifest",
            str(prepared / "source-map.json"),
            "--source-corpus",
            str(source),
            "--prepared-corpus",
            str(prepared),
            "--output",
            str(remapped),
        ],
    )
    validation = runner.invoke(
        app,
        ["validate-graph", str(remapped), "--corpus", str(source)],
    )

    assert preparation.exit_code == 0
    assert "1 Markdown source as 1 chunk" in preparation.stdout
    assert remapping.exit_code == 0
    assert "Remapped provenance for 1 graph items" in remapping.stdout
    assert "cited 1/1 prepared" in remapping.stdout
    assert "chunks." in remapping.stdout
    assert validation.exit_code == 0
    assert "source-location coverage 100.0%" in validation.stdout


def test_semantic_extraction_cli_requires_consent_and_reports_summary(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, object]] = []

    def fake_extract(*args: object, **kwargs: object) -> ExtractionSummary:
        calls.append({"args": args, "kwargs": kwargs})
        return ExtractionSummary(
            path=tmp_path / "graph.json",
            node_count=2,
            edge_count=1,
            rejected_relationship_count=0,
            cited_chunk_count=1,
            total_chunk_count=1,
            citation_skipped_chunk_count=0,
            input_tokens=100,
            output_tokens=20,
        )

    common = [
        "extract-semantic-graph",
        str(tmp_path / "prepared"),
        "--manifest",
        str(tmp_path / "source-map.json"),
        "--source-corpus",
        str(tmp_path / "source"),
        "--output",
        str(tmp_path / "graph.json"),
    ]

    without_consent = runner.invoke(app, common)
    monkeypatch.setattr("markos.cli.extract_semantic_graph", fake_extract)
    with_consent = runner.invoke(app, [*common, "--allow-external-llm"])

    assert without_consent.exit_code == 1
    assert "explicit" in without_consent.stderr
    assert "consent" in without_consent.stderr
    assert with_consent.exit_code == 0
    assert len(calls) == 1
    assert calls[0]["kwargs"] == {
        "allow_external_llm": True,
        "model": "sonnet",
        "effort": "low",
        "timeout_seconds": 600,
    }
    stdout = " ".join(with_consent.stdout.split())
    assert "Extracted 2 nodes and 1 edges" in stdout
    assert "skipped 0 chunk(s) fully covered by deterministic citations" in stdout
    assert "rejected 0 relationships" in stdout


def test_freshness_exits_zero_when_current_and_one_when_stale(tmp_path: Path) -> None:
    index_path = tmp_path / "index.duckdb"
    vault = tmp_path / "vault"
    vault.mkdir()
    note = vault / "Note.md"
    note.write_text("# Before", encoding="utf-8")
    runner.invoke(app, ["rebuild", "--vault", str(vault), "--index", str(index_path)])

    current = runner.invoke(
        app,
        ["freshness", "--vault", str(vault), "--index", str(index_path)],
    )
    note.write_text("# After", encoding="utf-8")
    stale = runner.invoke(
        app,
        ["freshness", "--vault", str(vault), "--index", str(index_path)],
    )

    assert current.exit_code == 0
    assert "Index is current" in current.stdout
    assert stale.exit_code == 1
    assert "Modified" in stale.stdout
    assert "Note.md" in stale.stdout


def test_sync_skips_current_index_and_rebuilds_stale_index(tmp_path: Path) -> None:
    index_path = tmp_path / "index.duckdb"
    vault = tmp_path / "vault"
    vault.mkdir()
    note = vault / "Note.md"
    note.write_text("# Before", encoding="utf-8")
    runner.invoke(app, ["rebuild", "--vault", str(vault), "--index", str(index_path)])

    current = runner.invoke(app, ["sync", "--vault", str(vault), "--index", str(index_path)])
    note.write_text("# After", encoding="utf-8")
    stale = runner.invoke(app, ["sync", "--vault", str(vault), "--index", str(index_path)])

    assert current.exit_code == 0
    assert "already current" in current.stdout
    assert stale.exit_code == 0
    assert "Synchronized 1 documents" in stale.stdout
    assert "stale" in stale.stdout


def test_watch_passes_configuration_and_timing_options(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    vault = tmp_path / "vault"
    vault.mkdir()
    index_path = tmp_path / "index.duckdb"
    calls: list[tuple[Path, Path, float, float]] = []

    def fake_watch(
        source: Path,
        destination: Path,
        *,
        poll_interval: float,
        debounce: float,
    ) -> WatchSummary:
        calls.append((source, destination, poll_interval, debounce))
        return WatchSummary(sync_attempts=2, rebuilds=1, errors=0)

    monkeypatch.setattr("markos.cli.watch_vault_index", fake_watch)
    result = runner.invoke(
        app,
        [
            "watch",
            "--vault",
            str(vault),
            "--index",
            str(index_path),
            "--poll-interval",
            "2",
            "--debounce",
            "0.75",
        ],
    )

    assert result.exit_code == 0
    assert calls == [(vault, index_path, 2.0, 0.75)]
    assert "2 synchronization attempts" in result.stdout
    assert "1 rebuild" in result.stdout


def test_para_audit_prints_preview_without_modifying_vault(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    source = vault / "Synthetic Biology"
    source.mkdir(parents=True)
    paper = source / "study.pdf"
    paper.write_bytes(b"%PDF study")
    index_path = tmp_path / "index.duckdb"
    runner.invoke(app, ["rebuild", "--vault", str(vault), "--index", str(index_path)])

    result = runner.invoke(
        app,
        [
            "para-audit",
            "Synthetic Biology",
            "--vault",
            str(vault),
            "--index",
            str(index_path),
        ],
    )

    assert result.exit_code == 0
    assert "Resources/Synthetic Biology" in result.stdout
    assert "Read-only assessment" in result.stdout
    assert paper.read_bytes() == b"%PDF study"
