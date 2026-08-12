"""Read-only graph analysis over a rebuilt MarkOS index."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import networkx as nx

from markos.query import GraphSnapshot, graph_snapshot


class GraphAnalysisError(ValueError):
    """Raised when a requested graph analysis cannot be completed."""


@dataclass(frozen=True)
class GraphReport:
    """Summary health metrics for the indexed knowledge graph."""

    document_count: int
    resolved_link_count: int
    unresolved_link_count: int
    isolated_document_count: int
    weak_component_count: int
    isolated_documents: tuple[str, ...]


def analyze_graph(index_path: Path) -> GraphReport:
    """Calculate deterministic graph health metrics from the read-only index."""

    snapshot = graph_snapshot(index_path)
    graph = _resolved_graph(snapshot)
    isolated_documents = tuple(sorted(nx.isolates(graph), key=lambda path: (path.casefold(), path)))
    weak_component_count = nx.number_weakly_connected_components(graph) if graph else 0

    return GraphReport(
        document_count=len(snapshot.document_paths),
        resolved_link_count=sum(link.resolved_path is not None for link in snapshot.links),
        unresolved_link_count=sum(link.resolved_path is None for link in snapshot.links),
        isolated_document_count=len(isolated_documents),
        weak_component_count=weak_component_count,
        isolated_documents=isolated_documents,
    )


def shortest_path(index_path: Path, source: str, target: str) -> tuple[str, ...]:
    """Return the shortest directed path across resolved internal links."""

    snapshot = graph_snapshot(index_path)
    known = set(snapshot.document_paths)
    if source not in known:
        raise GraphAnalysisError(f"document is not indexed: {source}")
    if target not in known:
        raise GraphAnalysisError(f"document is not indexed: {target}")

    graph = _resolved_graph(snapshot)
    try:
        path = nx.shortest_path(graph, source=source, target=target)
    except nx.NetworkXNoPath as error:
        raise GraphAnalysisError(f"no directed path from {source} to {target}") from error
    return tuple(path)


def _resolved_graph(snapshot: GraphSnapshot) -> nx.DiGraph[str]:
    graph: nx.DiGraph[str] = nx.DiGraph()
    graph.add_nodes_from(snapshot.document_paths)
    graph.add_edges_from(
        (link.source_path, link.resolved_path)
        for link in snapshot.links
        if link.resolved_path is not None
    )
    return graph
