from pathlib import Path, PurePath

import duckdb
import pytest

from markos.domain import KnowledgeDocument
from markos.index import SCHEMA_VERSION, rebuild_index
from markos.markdown import IngestionIssue
from markos.query import (
    KnowledgeQueryError,
    backlinks,
    ingestion_issues,
    outgoing_links,
    read_document,
    search_index,
)


def _build_search_index(path: Path) -> None:
    rebuild_index(
        path,
        [
            KnowledgeDocument(
                path=PurePath("Projects/MarkOS.md"),
                title="MarkOS Architecture",
                body="# Architecture\n\nA local-first knowledge engine with Markdown.",
                metadata={"status": "active"},
                links=("Vision", "Roadmap"),
            ),
            KnowledgeDocument(
                path=PurePath("Vision.md"),
                title="Vision",
                body="# Vision\n\nKnowledge should outlive its tools.",
            ),
            KnowledgeDocument(
                path=PurePath("Archive/Other.md"),
                title="Other",
                body="An archived local note.",
            ),
        ],
    )


def test_search_matches_title_path_and_body_with_deterministic_ranking(tmp_path: Path) -> None:
    index_path = tmp_path / "index.duckdb"
    _build_search_index(index_path)

    title_results = search_index(index_path, "vision")
    body_results = search_index(index_path, "local")
    path_results = search_index(index_path, "projects")

    assert [result.path for result in title_results] == ["Vision.md"]
    assert [result.path for result in body_results] == [
        "Projects/MarkOS.md",
        "Archive/Other.md",
    ]
    assert [result.path for result in path_results] == ["Projects/MarkOS.md"]


def test_search_is_case_insensitive_and_limited(tmp_path: Path) -> None:
    index_path = tmp_path / "index.duckdb"
    _build_search_index(index_path)

    results = search_index(index_path, "LOCAL", limit=1)

    assert len(results) == 1
    assert results[0].path == "Projects/MarkOS.md"


def test_search_rejects_blank_query_and_invalid_limit(tmp_path: Path) -> None:
    with pytest.raises(KnowledgeQueryError, match="blank"):
        search_index(tmp_path / "missing.duckdb", "  ")
    with pytest.raises(KnowledgeQueryError, match="between"):
        search_index(tmp_path / "missing.duckdb", "term", limit=0)


def test_read_document_restores_metadata_and_ordered_links(tmp_path: Path) -> None:
    index_path = tmp_path / "index.duckdb"
    _build_search_index(index_path)

    document = read_document(index_path, "Projects/MarkOS.md")

    assert document is not None
    assert document.title == "MarkOS Architecture"
    assert document.metadata == {"status": "active"}
    assert document.links == ("Vision", "Roadmap")
    assert read_document(index_path, "Missing.md") is None


def test_queries_reject_missing_and_incompatible_indexes(tmp_path: Path) -> None:
    with pytest.raises(KnowledgeQueryError, match="does not exist"):
        search_index(tmp_path / "missing.duckdb", "term")

    incompatible = tmp_path / "incompatible.duckdb"
    with duckdb.connect(str(incompatible)) as connection:
        connection.execute("CREATE TABLE index_state (schema_version INTEGER)")
        connection.execute("INSERT INTO index_state VALUES (999)")

    with pytest.raises(KnowledgeQueryError, match=f"expected {SCHEMA_VERSION}, found 999"):
        search_index(incompatible, "term")


def test_search_does_not_modify_index(tmp_path: Path) -> None:
    index_path = tmp_path / "index.duckdb"
    _build_search_index(index_path)
    before = index_path.read_bytes()

    search_index(index_path, "knowledge")

    assert index_path.read_bytes() == before


def test_outgoing_links_and_backlinks_use_resolved_paths(tmp_path: Path) -> None:
    index_path = tmp_path / "index.duckdb"
    _build_search_index(index_path)

    outgoing = outgoing_links(index_path, "Projects/MarkOS.md")
    incoming = backlinks(index_path, "Vision.md")

    assert [(link.target, link.resolved_path) for link in outgoing] == [
        ("Vision", "Vision.md"),
        ("Roadmap", None),
    ]
    assert [(link.source_path, link.target) for link in incoming] == [
        ("Projects/MarkOS.md", "Vision")
    ]


def test_ingestion_issues_are_returned_in_deterministic_order(tmp_path: Path) -> None:
    index_path = tmp_path / "index.duckdb"
    rebuild_index(
        index_path,
        [],
        issues=[
            IngestionIssue(path="z.md", error="z error"),
            IngestionIssue(path="A.md", error="a error"),
        ],
    )

    assert [(issue.path, issue.error) for issue in ingestion_issues(index_path)] == [
        ("A.md", "a error"),
        ("z.md", "z error"),
    ]
