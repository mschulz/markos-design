import json
from pathlib import Path, PurePath

import duckdb
import pytest

from markos.domain import KnowledgeDocument
from markos.index import KnowledgeIndexError, rebuild_index, rebuild_vault_index
from markos.markdown import MarkdownParseError


def test_rebuild_stores_documents_links_metadata_and_state(tmp_path: Path) -> None:
    index_path = tmp_path / "index.duckdb"
    documents = (
        KnowledgeDocument(
            path=PurePath("One.md"),
            title="One",
            body="# One",
            metadata={"status": "active"},
            links=("Two", "https://example.com"),
        ),
        KnowledgeDocument(path=PurePath("Two.md"), title="Two"),
    )

    summary = rebuild_index(index_path, documents)

    with duckdb.connect(str(index_path), read_only=True) as connection:
        rows = connection.execute(
            "SELECT path, title, body, metadata FROM documents ORDER BY path"
        ).fetchall()
        links = connection.execute(
            "SELECT source_path, ordinal, target FROM document_links ORDER BY source_path, ordinal"
        ).fetchall()
        state = connection.execute(
            "SELECT schema_version, document_count, link_count FROM index_state"
        ).fetchone()

    assert rows == [
        ("One.md", "One", "# One", json.dumps({"status": "active"})),
        ("Two.md", "Two", "", "{}"),
    ]
    assert links == [
        ("One.md", 0, "Two"),
        ("One.md", 1, "https://example.com"),
    ]
    assert state == (1, 2, 2)
    assert summary.document_count == 2
    assert summary.link_count == 2


def test_rebuild_replaces_stale_rows(tmp_path: Path) -> None:
    index_path = tmp_path / "index.duckdb"
    rebuild_index(
        index_path,
        [KnowledgeDocument(path=PurePath("Old.md"), title="Old", links=("Gone",))],
    )

    rebuild_index(
        index_path,
        [KnowledgeDocument(path=PurePath("New.md"), title="New")],
    )

    with duckdb.connect(str(index_path), read_only=True) as connection:
        paths = connection.execute("SELECT path FROM documents").fetchall()
        links = connection.execute("SELECT target FROM document_links").fetchall()
    assert paths == [("New.md",)]
    assert links == []


def test_rebuild_rejects_duplicate_document_paths_before_writing(tmp_path: Path) -> None:
    index_path = tmp_path / "index.duckdb"
    document = KnowledgeDocument(path=PurePath("Same.md"), title="Same")

    with pytest.raises(KnowledgeIndexError, match="duplicate document path"):
        rebuild_index(index_path, [document, document])

    assert not index_path.exists()


def test_vault_rebuild_keeps_index_outside_vault_and_vault_unchanged(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    vault.mkdir()
    note = vault / "Note.md"
    note.write_text("# Note\n\nSee [[Other]].\n", encoding="utf-8")
    before = note.read_bytes()
    index_path = tmp_path / "data" / "index.duckdb"

    summary = rebuild_vault_index(vault, index_path)

    assert summary.document_count == 1
    assert summary.link_count == 1
    assert note.read_bytes() == before
    assert list(vault.iterdir()) == [note]


def test_vault_rebuild_rejects_index_inside_vault(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    vault.mkdir()

    with pytest.raises(KnowledgeIndexError, match="outside"):
        rebuild_vault_index(vault, vault / "index.duckdb")

    assert list(vault.iterdir()) == []


def test_parse_failure_preserves_existing_index(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    vault.mkdir()
    index_path = tmp_path / "index.duckdb"
    rebuild_index(
        index_path,
        [KnowledgeDocument(path=PurePath("Existing.md"), title="Existing")],
    )
    (vault / "broken.md").write_bytes(b"\xff")

    with pytest.raises(MarkdownParseError):
        rebuild_vault_index(vault, index_path)

    with duckdb.connect(str(index_path), read_only=True) as connection:
        paths = connection.execute("SELECT path FROM documents").fetchall()
    assert paths == [("Existing.md",)]
