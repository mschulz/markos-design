from pathlib import Path

from markos.freshness import check_freshness
from markos.index import rebuild_markdown_index


def test_freshness_reports_current_index(tmp_path: Path) -> None:
    markdown_root = tmp_path / "markdown_root"
    markdown_root.mkdir()
    (markdown_root / "Current.md").write_text("# Current", encoding="utf-8")
    index_path = tmp_path / "index.duckdb"
    rebuild_markdown_index(markdown_root, index_path)

    report = check_freshness(markdown_root, index_path)

    assert report.is_current
    assert report.added == ()
    assert report.modified == ()
    assert report.deleted == ()


def test_freshness_reports_added_modified_and_deleted_files(tmp_path: Path) -> None:
    markdown_root = tmp_path / "markdown_root"
    markdown_root.mkdir()
    (markdown_root / "Modified.md").write_text("# Before", encoding="utf-8")
    (markdown_root / "Deleted.md").write_text("# Deleted", encoding="utf-8")
    index_path = tmp_path / "index.duckdb"
    rebuild_markdown_index(markdown_root, index_path)

    (markdown_root / "Modified.md").write_text("# After", encoding="utf-8")
    (markdown_root / "Deleted.md").unlink()
    (markdown_root / "Added.md").write_text("# Added", encoding="utf-8")

    report = check_freshness(markdown_root, index_path)

    assert not report.is_current
    assert report.added == ("Added.md",)
    assert report.modified == ("Modified.md",)
    assert report.deleted == ("Deleted.md",)


def test_freshness_check_does_not_modify_markdown_root_or_index(tmp_path: Path) -> None:
    markdown_root = tmp_path / "markdown_root"
    markdown_root.mkdir()
    note = markdown_root / "Note.md"
    note.write_text("# Note", encoding="utf-8")
    index_path = tmp_path / "index.duckdb"
    rebuild_markdown_index(markdown_root, index_path)
    markdown_before = note.read_bytes()
    index_before = index_path.read_bytes()

    check_freshness(markdown_root, index_path)

    assert note.read_bytes() == markdown_before
    assert index_path.read_bytes() == index_before
