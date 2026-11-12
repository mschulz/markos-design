from pathlib import Path

from markos.freshness import check_freshness
from markos.index import rebuild_markdown_index
from markos.query import read_document
from markos.sync import sync_markdown_index


def test_sync_creates_missing_index(tmp_path: Path) -> None:
    markdown_root = tmp_path / "markdown_root"
    markdown_root.mkdir()
    (markdown_root / "Note.md").write_text("# Note", encoding="utf-8")
    index_path = tmp_path / "index.duckdb"

    result = sync_markdown_index(markdown_root, index_path)

    assert result.rebuilt
    assert result.reason == "missing"
    assert result.summary is not None
    assert result.summary.document_count == 1
    assert check_freshness(markdown_root, index_path).is_current


def test_sync_is_byte_for_byte_noop_when_current(tmp_path: Path) -> None:
    markdown_root = tmp_path / "markdown_root"
    markdown_root.mkdir()
    (markdown_root / "Note.md").write_text("# Note", encoding="utf-8")
    index_path = tmp_path / "index.duckdb"
    rebuild_markdown_index(markdown_root, index_path)
    before = index_path.read_bytes()

    result = sync_markdown_index(markdown_root, index_path)

    assert not result.rebuilt
    assert result.reason == "current"
    assert index_path.read_bytes() == before


def test_sync_rebuilds_stale_index_and_records_changes(tmp_path: Path) -> None:
    markdown_root = tmp_path / "markdown_root"
    markdown_root.mkdir()
    note = markdown_root / "Note.md"
    note.write_text("# Before", encoding="utf-8")
    index_path = tmp_path / "index.duckdb"
    rebuild_markdown_index(markdown_root, index_path)
    note.write_text("# After", encoding="utf-8")

    result = sync_markdown_index(markdown_root, index_path)

    assert result.rebuilt
    assert result.reason == "stale"
    assert result.changes is not None
    assert result.changes.modified == ("Note.md",)
    document = read_document(index_path, "Note.md")
    assert document is not None
    assert document.title == "After"
    assert check_freshness(markdown_root, index_path).is_current


def test_force_sync_rebuilds_current_index(tmp_path: Path) -> None:
    markdown_root = tmp_path / "markdown_root"
    markdown_root.mkdir()
    (markdown_root / "Note.md").write_text("# Note", encoding="utf-8")
    index_path = tmp_path / "index.duckdb"
    rebuild_markdown_index(markdown_root, index_path)

    result = sync_markdown_index(markdown_root, index_path, force=True)

    assert result.rebuilt
    assert result.reason == "forced"
