from pathlib import Path

import pytest

from markos.index import rebuild_markdown_index
from markos.para_audit import (
    ParaAuditError,
    audit_para_migration,
    render_para_audit,
    write_para_audit,
)


def test_audit_recommends_small_raw_collection_as_resource_pilot(tmp_path: Path) -> None:
    markdown_root = tmp_path / "markdown_root"
    source = markdown_root / "Remote Laboratories"
    source.mkdir(parents=True)
    (source / "paper.pdf").write_bytes(b"%PDF source")
    index_path = tmp_path / "index.duckdb"
    rebuild_markdown_index(markdown_root, index_path)

    report = audit_para_migration(markdown_root, index_path, ("Remote Laboratories",))
    folder = report.folders[0]

    assert folder.suggested_destination == "Resources/Remote Laboratories"
    assert folder.action == "pilot resource migration"
    assert folder.risk == "low"
    assert folder.raw_file_count == 1
    assert "No Markdown files or folders were changed" in render_para_audit(report)
    assert (source / "paper.pdf").read_bytes() == b"%PDF source"


def test_audit_reports_link_risk_and_existing_structure(tmp_path: Path) -> None:
    markdown_root = tmp_path / "markdown_root"
    source = markdown_root / "Research"
    source.mkdir(parents=True)
    (source / "raw").mkdir()
    (source / "knowledge").mkdir()
    (source / "Topic.md").write_text("# Topic", encoding="utf-8")
    (markdown_root / "Home.md").write_text("# Home\n\n[[Research/Topic]]", encoding="utf-8")
    index_path = tmp_path / "index.duckdb"
    rebuild_markdown_index(markdown_root, index_path)

    report = audit_para_migration(markdown_root, index_path, ("Research",))
    folder = report.folders[0]

    assert folder.action == "adopt existing concept folder"
    assert folder.risk == "medium"
    assert folder.external_inbound_count == 1
    assert folder.external_inbound_sources == ("Home.md",)
    assert folder.existing_managed_folders == ("knowledge", "raw")


def test_large_linked_collection_is_not_sent_through_inbox(tmp_path: Path) -> None:
    markdown_root = tmp_path / "markdown_root"
    source = markdown_root / "Legacy"
    source.mkdir(parents=True)
    for number in range(100):
        (source / f"Note-{number}.md").write_text(f"# Note {number}", encoding="utf-8")
        (markdown_root / f"Outside-{number}.md").write_text(
            f"# Outside {number}\n\n[[Legacy/Note-{number}]]",
            encoding="utf-8",
        )
    index_path = tmp_path / "index.duckdb"
    rebuild_markdown_index(markdown_root, index_path)

    report = audit_para_migration(markdown_root, index_path, ("Legacy",))
    folder = report.folders[0]

    assert folder.action == "batch migration in place"
    assert folder.suggested_destination == "Multiple PARA destinations"
    assert folder.risk == "high"
    assert folder.external_inbound_count == 100
    assert any("inbox" in reason for reason in folder.reasons)


def test_report_can_only_be_written_outside_markdown_root(tmp_path: Path) -> None:
    markdown_root = tmp_path / "markdown_root"
    source = markdown_root / "Topic"
    source.mkdir(parents=True)
    index_path = tmp_path / "index.duckdb"
    rebuild_markdown_index(markdown_root, index_path)
    report = audit_para_migration(markdown_root, index_path, ("Topic",))

    output = write_para_audit(report, tmp_path / "reports" / "audit.md")

    assert output.is_file()
    with pytest.raises(ParaAuditError, match="outside"):
        write_para_audit(report, markdown_root / "audit.md")
    assert not (markdown_root / "audit.md").exists()


@pytest.mark.parametrize("source", ["../outside", "/absolute"])
def test_audit_rejects_unsafe_source_paths(tmp_path: Path, source: str) -> None:
    markdown_root = tmp_path / "markdown_root"
    markdown_root.mkdir()
    index_path = tmp_path / "index.duckdb"
    rebuild_markdown_index(markdown_root, index_path)

    with pytest.raises(ParaAuditError, match="root-relative"):
        audit_para_migration(markdown_root, index_path, (source,))
