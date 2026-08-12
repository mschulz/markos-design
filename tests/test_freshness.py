from pathlib import Path

from markos.freshness import check_freshness
from markos.index import rebuild_vault_index


def test_freshness_reports_current_index(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    vault.mkdir()
    (vault / "Current.md").write_text("# Current", encoding="utf-8")
    index_path = tmp_path / "index.duckdb"
    rebuild_vault_index(vault, index_path)

    report = check_freshness(vault, index_path)

    assert report.is_current
    assert report.added == ()
    assert report.modified == ()
    assert report.deleted == ()


def test_freshness_reports_added_modified_and_deleted_files(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    vault.mkdir()
    (vault / "Modified.md").write_text("# Before", encoding="utf-8")
    (vault / "Deleted.md").write_text("# Deleted", encoding="utf-8")
    index_path = tmp_path / "index.duckdb"
    rebuild_vault_index(vault, index_path)

    (vault / "Modified.md").write_text("# After", encoding="utf-8")
    (vault / "Deleted.md").unlink()
    (vault / "Added.md").write_text("# Added", encoding="utf-8")

    report = check_freshness(vault, index_path)

    assert not report.is_current
    assert report.added == ("Added.md",)
    assert report.modified == ("Modified.md",)
    assert report.deleted == ("Deleted.md",)


def test_freshness_check_does_not_modify_vault_or_index(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    vault.mkdir()
    note = vault / "Note.md"
    note.write_text("# Note", encoding="utf-8")
    index_path = tmp_path / "index.duckdb"
    rebuild_vault_index(vault, index_path)
    vault_before = note.read_bytes()
    index_before = index_path.read_bytes()

    check_freshness(vault, index_path)

    assert note.read_bytes() == vault_before
    assert index_path.read_bytes() == index_before
