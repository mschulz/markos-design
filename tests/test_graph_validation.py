import json
from pathlib import Path
from typing import Any

import pytest

from markos.graph_validation import GraphValidationError, validate_graph_file


def _write_graph(path: Path, payload: dict[str, Any]) -> Path:
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _valid_graph() -> dict[str, Any]:
    return {
        "nodes": [
            {
                "id": "one",
                "label": "One",
                "source_file": "notes/One.md",
                "source_location": "L1",
            },
            {
                "id": "two",
                "label": "Two",
                "source_file": "notes/Two.md",
                "source_location": "line 2",
            },
        ],
        "edges": [
            {
                "source": "one",
                "target": "two",
                "relation": "mentions",
                "confidence": "EXTRACTED",
                "confidence_score": 1.0,
                "source_file": "notes/One.md",
                "source_location": "L1-L2",
            }
        ],
        "hyperedges": [],
    }


def test_accepts_grounded_graph(tmp_path: Path) -> None:
    corpus = tmp_path / "corpus"
    (corpus / "notes").mkdir(parents=True)
    (corpus / "notes" / "One.md").write_text("# One\n\n[[Two]]\n", encoding="utf-8")
    (corpus / "notes" / "Two.md").write_text("# Two\n\nEvidence\n", encoding="utf-8")
    graph = _write_graph(tmp_path / "graph.json", _valid_graph())

    report = validate_graph_file(graph, corpus)

    assert report.is_valid
    assert report.location_coverage == 1.0
    assert report.node_count == 2
    assert report.edge_count == 1


def test_rejects_fabricated_paths_dangling_edges_and_missing_locations(tmp_path: Path) -> None:
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    (corpus / "Active-Badge.md").write_text("# Active Badge\n", encoding="utf-8")
    payload = {
        "nodes": [
            {
                "id": "badge",
                "label": "Active Badge",
                "source_file": "Active-Badge.md",
                "source_location": None,
            },
            {
                "id": "invented",
                "label": "Invented code",
                "source_file": "src/auth/session.py",
                "source_location": "L99",
            },
        ],
        "edges": [
            {
                "source": "missing",
                "target": "invented",
                "relation": "conceptually_related_to",
                "confidence": "EXTRACTED",
                "confidence_score": 1.0,
                "source_file": "Active-Badge.md",
                "source_location": None,
            }
        ],
    }
    graph = _write_graph(tmp_path / "graph.json", payload)

    report = validate_graph_file(graph, corpus)
    codes = {issue.code for issue in report.issues}

    assert not report.is_valid
    assert "unknown_source_file" in codes
    assert "dangling_endpoint" in codes
    assert "insufficient_location_coverage" in codes


def test_rejects_duplicate_ids_bad_confidence_and_out_of_range_lines(tmp_path: Path) -> None:
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    (corpus / "One.md").write_text("one\ntwo\n", encoding="utf-8")
    payload = _valid_graph()
    payload["nodes"] = [
        {"id": "same", "source_file": "One.md", "source_location": "L1"},
        {"id": "same", "source_file": "One.md", "source_location": "L3"},
    ]
    payload["edges"] = [
        {
            "source": "same",
            "target": "same",
            "relation": "duplicates",
            "confidence": "CERTAIN",
            "confidence_score": 2,
            "source_file": "One.md",
            "source_location": "L2-L1",
        }
    ]

    report = validate_graph_file(_write_graph(tmp_path / "graph.json", payload), corpus)
    codes = {issue.code for issue in report.issues}

    assert "duplicate_node_id" in codes
    assert "invalid_confidence" in codes
    assert "invalid_confidence_score" in codes
    assert "invalid_source_location" in codes


@pytest.mark.parametrize(
    ("label", "score"),
    [("EXTRACTED", 0.9), ("INFERRED", 0.5), ("AMBIGUOUS", 0.5)],
)
def test_rejects_confidence_scores_inconsistent_with_labels(
    tmp_path: Path,
    label: str,
    score: float,
) -> None:
    corpus = tmp_path / "corpus"
    (corpus / "notes").mkdir(parents=True)
    (corpus / "notes" / "One.md").write_text("# One\n", encoding="utf-8")
    (corpus / "notes" / "Two.md").write_text("# Two\n", encoding="utf-8")
    payload = _valid_graph()
    payload["edges"][0]["confidence"] = label
    payload["edges"][0]["confidence_score"] = score

    report = validate_graph_file(_write_graph(tmp_path / "graph.json", payload), corpus)

    assert any(issue.code == "inconsistent_confidence_score" for issue in report.issues)


def test_rejects_malformed_graph_json(tmp_path: Path) -> None:
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    graph = tmp_path / "graph.json"
    graph.write_text("not json", encoding="utf-8")

    with pytest.raises(GraphValidationError, match="cannot read graph JSON"):
        validate_graph_file(graph, corpus)


def test_rejects_unsafe_source_path(tmp_path: Path) -> None:
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    payload = {
        "nodes": [
            {
                "id": "escape",
                "source_file": "../outside.md",
                "source_location": "L1",
            }
        ],
        "edges": [],
    }

    report = validate_graph_file(_write_graph(tmp_path / "graph.json", payload), corpus)

    assert any(issue.code == "unknown_source_file" for issue in report.issues)


def test_location_threshold_can_be_lowered_for_diagnostics(tmp_path: Path) -> None:
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    (corpus / "Note.md").write_text("# Note\n", encoding="utf-8")
    payload = {
        "nodes": [
            {
                "id": "note",
                "label": "Note",
                "source_file": "Note.md",
                "source_location": None,
            }
        ],
        "edges": [],
    }

    report = validate_graph_file(
        _write_graph(tmp_path / "graph.json", payload),
        corpus,
        minimum_location_coverage=0.0,
    )

    assert report.is_valid
    assert report.location_coverage == 0.0


def test_rejects_empty_graph_even_with_zero_location_threshold(tmp_path: Path) -> None:
    corpus = tmp_path / "corpus"
    corpus.mkdir()

    report = validate_graph_file(
        _write_graph(tmp_path / "graph.json", {"nodes": [], "edges": []}),
        corpus,
        minimum_location_coverage=0.0,
    )

    assert any(issue.code == "empty_graph" for issue in report.issues)
