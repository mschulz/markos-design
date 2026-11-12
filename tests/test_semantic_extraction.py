import json
import subprocess
from pathlib import Path

import pytest

from markos.graph_validation import validate_graph_file
from markos.semantic_corpus import prepare_semantic_corpus, remap_graph_sources
from markos.semantic_extraction import (
    ExtractionPayload,
    SemanticExtractionError,
    _claude_json_schema,
    canonical_entity_key,
    extract_semantic_graph,
)


def _prepared_corpus(tmp_path: Path) -> tuple[Path, Path, Path]:
    source = tmp_path / "source"
    source.mkdir()
    (source / "Note.md").write_text("# Alpha\n\nAlpha uses Beta.\n", encoding="utf-8")
    prepared = prepare_semantic_corpus(source, tmp_path / "prepared")
    return source, prepared.path, prepared.manifest_path


def _payload(
    *,
    score: float = 1.0,
    evidence: str = "Alpha uses Beta.",
    source_file: str = "chunks/0001/0001.md",
) -> dict[str, object]:
    return {
        "entities": [
            {
                "canonical_label": "Alpha",
                "kind": "concept",
                "source_file": source_file,
                "source_location": "L3",
                "evidence": evidence,
            },
            {
                "canonical_label": "Beta",
                "kind": "concept",
                "source_file": source_file,
                "source_location": "L3",
                "evidence": evidence,
            },
        ],
        "relationships": [
            {
                "source_label": "Alpha",
                "source_kind": "concept",
                "target_label": "Beta",
                "target_kind": "concept",
                "relation": "uses",
                "confidence": "EXTRACTED",
                "confidence_score": score,
                "source_file": source_file,
                "source_location": "L3",
                "evidence": evidence,
            }
        ],
    }


def _mock_claude(
    monkeypatch: pytest.MonkeyPatch,
    payload: dict[str, object] | list[dict[str, object]],
) -> list[list[str]]:
    calls: list[list[str]] = []
    payloads = payload if isinstance(payload, list) else [payload]

    def fake_run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(command)
        assert kwargs["check"] is False
        assert kwargs["input"]
        environment = kwargs["env"]
        assert isinstance(environment, dict)
        assert environment["CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC"] == "1"
        envelope = {
            "structured_output": payloads[len(calls) - 1],
            "usage": {"input_tokens": 123, "output_tokens": 45},
        }
        return subprocess.CompletedProcess(command, 0, json.dumps(envelope), "")

    monkeypatch.setattr("markos.semantic_extraction.shutil.which", lambda _: "/bin/claude")
    monkeypatch.setattr("markos.semantic_extraction.subprocess.run", fake_run)
    return calls


def test_canonical_identity_normalizes_case_and_separators() -> None:
    assert canonical_entity_key("concept", "Context-Aware-Computing") == (
        canonical_entity_key("concept", "context aware computing")
    )


def test_claude_schema_keeps_union_but_removes_unsupported_discriminator() -> None:
    pydantic_schema = json.dumps(ExtractionPayload.model_json_schema())
    claude_schema = json.dumps(_claude_json_schema())

    assert '"discriminator"' in pydantic_schema
    assert '"discriminator"' not in claude_schema
    assert '"oneOf"' in claude_schema


def test_extraction_verifies_and_writes_deterministic_graph(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source, prepared, manifest = _prepared_corpus(tmp_path)
    calls = _mock_claude(monkeypatch, _payload())
    graph_path = tmp_path / "graph.json"

    summary = extract_semantic_graph(
        prepared,
        manifest,
        source,
        graph_path,
        allow_external_llm=True,
        model="sonnet",
    )
    graph = json.loads(graph_path.read_text(encoding="utf-8"))
    remapped = tmp_path / "remapped.json"
    remap_graph_sources(graph_path, manifest, source, prepared, remapped)

    assert summary.node_count == 2
    assert summary.edge_count == 1
    assert summary.rejected_relationship_count == 0
    assert summary.cited_chunk_count == 1
    assert summary.total_chunk_count == 1
    assert summary.input_tokens == 123
    assert summary.output_tokens == 45
    assert graph["contract_version"] == "markos-semantic-extraction-v1"
    assert [node["canonical_key"] for node in graph["nodes"]] == [
        "concept:alpha",
        "concept:beta",
    ]
    assert graph["edges"][0]["confidence_score"] == 1.0
    assert validate_graph_file(remapped, source).is_valid
    assert len(calls) == 1
    command = calls[0]
    assert "--no-session-persistence" in command
    assert command[command.index("--tools") + 1] == ""
    assert command[command.index("--model") + 1] == "sonnet"
    assert command[command.index("--effort") + 1] == "low"
    assert "--json-schema" in command


def test_extraction_drops_extracted_relationship_without_both_endpoint_names(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source, prepared, manifest = _prepared_corpus(tmp_path)
    payload = _payload()
    relationship = payload["relationships"]
    assert isinstance(relationship, list)
    assert isinstance(relationship[0], dict)
    relationship[0]["evidence"] = "Alpha"
    relationship[0]["source_location"] = "L3"
    (source / "Note.md").write_text("# Alpha\n\nAlpha uses Beta. Alpha\n", encoding="utf-8")
    new_prepared = prepare_semantic_corpus(source, tmp_path / "prepared-again")
    payload["entities"] = [
        {
            "canonical_label": "Alpha",
            "kind": "concept",
            "source_file": "chunks/0001/0001.md",
            "source_location": "L3",
            "evidence": "Alpha uses Beta. Alpha",
        },
        {
            "canonical_label": "Beta",
            "kind": "concept",
            "source_file": "chunks/0001/0001.md",
            "source_location": "L3",
            "evidence": "Alpha uses Beta. Alpha",
        },
    ]
    _mock_claude(monkeypatch, payload)

    summary = extract_semantic_graph(
        new_prepared.path,
        new_prepared.manifest_path,
        source,
        tmp_path / "rejected-edge-graph.json",
        allow_external_llm=True,
    )
    graph = json.loads((tmp_path / "rejected-edge-graph.json").read_text(encoding="utf-8"))

    assert summary.node_count == 2
    assert summary.edge_count == 0
    assert summary.rejected_relationship_count == 1
    assert graph["rejections"] == [
        {
            "code": "extracted_evidence_missing_endpoint",
            "location": "relationships[0]",
        }
    ]


def test_extraction_drops_inferred_relationship_from_evidence_graph(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source, prepared, manifest = _prepared_corpus(tmp_path)
    payload = _payload()
    relationship = payload["relationships"]
    assert isinstance(relationship, list)
    assert isinstance(relationship[0], dict)
    relationship[0]["confidence"] = "INFERRED"
    relationship[0]["confidence_score"] = 0.75
    _mock_claude(monkeypatch, payload)

    summary = extract_semantic_graph(
        prepared,
        manifest,
        source,
        tmp_path / "inferred-graph.json",
        allow_external_llm=True,
    )
    graph = json.loads((tmp_path / "inferred-graph.json").read_text(encoding="utf-8"))

    assert summary.edge_count == 0
    assert summary.rejected_relationship_count == 1
    assert graph["rejections"][0]["code"] == "non_extracted_relationship"


def test_extraction_calls_claude_once_per_prepared_chunk(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "source"
    source.mkdir()
    (source / "One.md").write_text("# Alpha\n\nAlpha uses Beta.\n", encoding="utf-8")
    (source / "Two.md").write_text("# Alpha\n\nAlpha uses Beta.\n", encoding="utf-8")
    prepared = prepare_semantic_corpus(source, tmp_path / "prepared")
    calls = _mock_claude(
        monkeypatch,
        [
            _payload(source_file="chunks/0001/0001.md"),
            _payload(source_file="chunks/0002/0001.md"),
        ],
    )

    summary = extract_semantic_graph(
        prepared.path,
        prepared.manifest_path,
        source,
        tmp_path / "graph.json",
        allow_external_llm=True,
    )

    assert len(calls) == 2
    assert summary.total_chunk_count == 2


def test_extraction_requires_separate_external_llm_consent(tmp_path: Path) -> None:
    source, prepared, manifest = _prepared_corpus(tmp_path)

    with pytest.raises(SemanticExtractionError, match="explicit consent"):
        extract_semantic_graph(
            prepared,
            manifest,
            source,
            tmp_path / "graph.json",
            allow_external_llm=False,
        )


def test_extraction_rejects_provider_confidence_outside_contract(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source, prepared, manifest = _prepared_corpus(tmp_path)
    _mock_claude(monkeypatch, _payload(score=0.9))

    with pytest.raises(SemanticExtractionError, match="violates extraction schema"):
        extract_semantic_graph(
            prepared,
            manifest,
            source,
            tmp_path / "graph.json",
            allow_external_llm=True,
        )


def test_extraction_rejects_fabricated_evidence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source, prepared, manifest = _prepared_corpus(tmp_path)
    _mock_claude(monkeypatch, _payload(evidence="Alpha secretly controls Beta."))

    with pytest.raises(SemanticExtractionError, match="not exact cited source text"):
        extract_semantic_graph(
            prepared,
            manifest,
            source,
            tmp_path / "graph.json",
            allow_external_llm=True,
        )


def test_extraction_rechecks_source_hash_before_transmission(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source, prepared, manifest = _prepared_corpus(tmp_path)
    calls = _mock_claude(monkeypatch, _payload())
    (source / "Note.md").write_text("# Changed\n", encoding="utf-8")

    with pytest.raises(SemanticExtractionError, match="source hash changed"):
        extract_semantic_graph(
            prepared,
            manifest,
            source,
            tmp_path / "graph.json",
            allow_external_llm=True,
        )
    assert calls == []


def test_extraction_refuses_output_inside_source_corpus(tmp_path: Path) -> None:
    source, prepared, manifest = _prepared_corpus(tmp_path)

    with pytest.raises(SemanticExtractionError, match="outside the source corpus"):
        extract_semantic_graph(
            prepared,
            manifest,
            source,
            source / "graph.json",
            allow_external_llm=True,
        )


def test_extraction_refuses_output_inside_prepared_corpus(tmp_path: Path) -> None:
    source, prepared, manifest = _prepared_corpus(tmp_path)

    with pytest.raises(SemanticExtractionError, match="outside the prepared corpus"):
        extract_semantic_graph(
            prepared,
            manifest,
            source,
            prepared / "graph.json",
            allow_external_llm=True,
        )


def test_extraction_skips_chunks_fully_covered_by_citations(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "source"
    source.mkdir()
    (source / "Cited.md").write_text(
        "# Cited\n\n## References\n\n- Doe, J. *A Title.* Some Journal, 2001.\n",
        encoding="utf-8",
    )
    (source / "Prose.md").write_text("# Alpha\n\nAlpha uses Beta.\n", encoding="utf-8")
    prepared = prepare_semantic_corpus(source, tmp_path / "prepared")
    calls = _mock_claude(monkeypatch, _payload(source_file="chunks/0002/0001.md"))

    summary = extract_semantic_graph(
        prepared.path,
        prepared.manifest_path,
        source,
        tmp_path / "graph.json",
        allow_external_llm=True,
    )

    assert len(calls) == 1
    assert summary.total_chunk_count == 2
    assert summary.citation_skipped_chunk_count == 1
    assert summary.node_count == 4
    graph = json.loads((tmp_path / "graph.json").read_text(encoding="utf-8"))
    keys = {node["canonical_key"] for node in graph["nodes"]}
    assert "publication:a title" in keys
    assert "person:doe j" in keys
    citation_node = next(
        node for node in graph["nodes"] if node["canonical_key"] == "publication:a title"
    )
    assert citation_node["source_file"] == "chunks/0001/0001.md"
    assert citation_node["evidence"] == "Doe, J. *A Title.* Some Journal, 2001."


def test_extraction_succeeds_from_citations_alone_with_no_llm_calls(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "source"
    source.mkdir()
    (source / "Cited.md").write_text(
        "# Cited\n\n## References\n\n- Doe, J. *A Title.* Some Journal, 2001.\n",
        encoding="utf-8",
    )
    prepared = prepare_semantic_corpus(source, tmp_path / "prepared")
    calls = _mock_claude(monkeypatch, _payload())

    summary = extract_semantic_graph(
        prepared.path,
        prepared.manifest_path,
        source,
        tmp_path / "graph.json",
        allow_external_llm=True,
    )

    assert calls == []
    assert summary.node_count == 2
    assert summary.edge_count == 0
    assert summary.citation_skipped_chunk_count == 1
    graph = json.loads((tmp_path / "graph.json").read_text(encoding="utf-8"))
    assert {node["canonical_key"] for node in graph["nodes"]} == {
        "publication:a title",
        "person:doe j",
    }
