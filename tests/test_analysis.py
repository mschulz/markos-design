from pathlib import Path, PurePath

import pytest

from markos.analysis import GraphAnalysisError, analyze_graph, shortest_path
from markos.domain import KnowledgeDocument
from markos.index import rebuild_index


def _build_graph_index(path: Path) -> None:
    rebuild_index(
        path,
        [
            KnowledgeDocument(path=PurePath("A.md"), title="A", links=("B", "Missing")),
            KnowledgeDocument(path=PurePath("B.md"), title="B", links=("C",)),
            KnowledgeDocument(path=PurePath("C.md"), title="C"),
            KnowledgeDocument(path=PurePath("D.md"), title="D"),
        ],
    )


def test_graph_report_counts_health_and_isolated_documents(tmp_path: Path) -> None:
    index_path = tmp_path / "index.duckdb"
    _build_graph_index(index_path)

    report = analyze_graph(index_path)

    assert report.document_count == 4
    assert report.resolved_link_count == 2
    assert report.unresolved_link_count == 1
    assert report.isolated_document_count == 1
    assert report.weak_component_count == 2
    assert report.isolated_documents == ("D.md",)


def test_shortest_path_follows_resolved_directed_links(tmp_path: Path) -> None:
    index_path = tmp_path / "index.duckdb"
    _build_graph_index(index_path)

    assert shortest_path(index_path, "A.md", "C.md") == ("A.md", "B.md", "C.md")
    assert shortest_path(index_path, "A.md", "A.md") == ("A.md",)


def test_shortest_path_reports_missing_nodes_and_disconnected_direction(tmp_path: Path) -> None:
    index_path = tmp_path / "index.duckdb"
    _build_graph_index(index_path)

    with pytest.raises(GraphAnalysisError, match="not indexed"):
        shortest_path(index_path, "Missing.md", "A.md")
    with pytest.raises(GraphAnalysisError, match="no directed path"):
        shortest_path(index_path, "C.md", "A.md")


def test_graph_analysis_does_not_modify_index(tmp_path: Path) -> None:
    index_path = tmp_path / "index.duckdb"
    _build_graph_index(index_path)
    before = index_path.read_bytes()

    analyze_graph(index_path)
    shortest_path(index_path, "A.md", "C.md")

    assert index_path.read_bytes() == before
