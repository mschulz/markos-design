from pathlib import Path

import pytest

from markos.index import rebuild_vault_index
from markos.para_audit import (
    ParaAuditError,
    audit_para_migration,
    render_para_audit,
    write_para_audit,
)


def test_audit_recommends_small_raw_collection_as_resource_pilot(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    source = vault / "Remote Laboratories"
    source.mkdir(parents=True)
    (source / "paper.pdf").write_bytes(b"%PDF source")
    index_path = tmp_path / "index.duckdb"
    rebuild_vault_index(vault, index_path)

    report = audit_para_migration(vault, index_path, ("Remote Laboratories",))
    folder = report.folders[0]

    assert folder.suggested_destination == "Resources/Remote Laboratories"
    assert folder.action == "pilot resource migration"
    assert folder.risk == "low"
    assert folder.raw_file_count == 1
    assert "No vault files or folders were changed" in render_para_audit(report)
    assert (source / "paper.pdf").read_bytes() == b"%PDF source"


def test_audit_reports_link_risk_and_existing_structure(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    source = vault / "Research"
    source.mkdir(parents=True)
    (source / "raw").mkdir()
    (source / "knowledge").mkdir()
    (source / "Topic.md").write_text("# Topic", encoding="utf-8")
    (vault / "Home.md").write_text("# Home\n\n[[Research/Topic]]", encoding="utf-8")
    index_path = tmp_path / "index.duckdb"
    rebuild_vault_index(vault, index_path)

    report = audit_para_migration(vault, index_path, ("Research",))
    folder = report.folders[0]

    assert folder.action == "adopt existing concept folder"
    assert folder.risk == "medium"
    assert folder.external_inbound_count == 1
    assert folder.external_inbound_sources == ("Home.md",)
    assert folder.existing_managed_folders == ("knowledge", "raw")


def test_large_linked_collection_is_not_sent_through_inbox(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    source = vault / "Legacy"
    source.mkdir(parents=True)
    for number in range(100):
        (source / f"Note-{number}.md").write_text(f"# Note {number}", encoding="utf-8")
        (vault / f"Outside-{number}.md").write_text(
            f"# Outside {number}\n\n[[Legacy/Note-{number}]]",
            encoding="utf-8",
        )
    index_path = tmp_path / "index.duckdb"
    rebuild_vault_index(vault, index_path)

    report = audit_para_migration(vault, index_path, ("Legacy",))
    folder = report.folders[0]

    assert folder.action == "batch migration in place"
    assert folder.suggested_destination == "Multiple PARA destinations"
    assert folder.risk == "high"
    assert folder.external_inbound_count == 100
    assert any("inbox" in reason for reason in folder.reasons)


def test_report_can_only_be_written_outside_vault(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    source = vault / "Topic"
    source.mkdir(parents=True)
    index_path = tmp_path / "index.duckdb"
    rebuild_vault_index(vault, index_path)
    report = audit_para_migration(vault, index_path, ("Topic",))

    output = write_para_audit(report, tmp_path / "reports" / "audit.md")

    assert output.is_file()
    with pytest.raises(ParaAuditError, match="outside"):
        write_para_audit(report, vault / "audit.md")
    assert not (vault / "audit.md").exists()


@pytest.mark.parametrize("source", ["../outside", "/absolute"])
def test_audit_rejects_unsafe_source_paths(tmp_path: Path, source: str) -> None:
    vault = tmp_path / "vault"
    vault.mkdir()
    index_path = tmp_path / "index.duckdb"
    rebuild_vault_index(vault, index_path)

    with pytest.raises(ParaAuditError, match="vault-relative"):
        audit_para_migration(vault, index_path, (source,))
