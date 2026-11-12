import json
from hashlib import sha256
from pathlib import Path
from typing import Any

import pytest

from markos.graph_validation import validate_graph_file
from markos.semantic_corpus import (
    GRAPHIFY_IGNORE_FILENAME,
    SOURCE_MAP_FILENAME,
    SemanticCorpusError,
    extract_citations,
    load_source_map,
    prepare_semantic_corpus,
    remap_graph_sources,
)


def _write_graph(path: Path, payload: dict[str, Any]) -> Path:
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_preparation_splits_at_headings_and_is_deterministic(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    contents = "Preamble\n\n# One\n" + ("alpha\n" * 40) + "## Two\n" + ("beta\n" * 40)
    (source / "Note.md").write_text(contents, encoding="utf-8")

    first = prepare_semantic_corpus(source, tmp_path / "first", maximum_chunk_characters=256)
    second = prepare_semantic_corpus(source, tmp_path / "second", maximum_chunk_characters=256)
    first_manifest = load_source_map(first.manifest_path)

    assert first.source_count == 1
    assert first.chunk_count == 2
    assert [chunk.chunk_path for chunk in first_manifest.chunks] == [
        "chunks/0001/0001.md",
        "chunks/0001/0002.md",
    ]
    assert first.manifest_path.read_bytes() == second.manifest_path.read_bytes()
    assert (first.path / GRAPHIFY_IGNORE_FILENAME).read_text(encoding="utf-8") == (
        "source-map.json\n"
    )
    assert first_manifest.chunks[0].start_line == 1
    assert first_manifest.chunks[-1].end_line == len(contents.splitlines())
    reconstructed = b"".join(
        (first.path / chunk.chunk_path).read_bytes() for chunk in first_manifest.chunks
    )
    assert reconstructed == contents.encode()


def test_preparation_refuses_source_subdirectory_and_existing_output(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    (source / "Note.md").write_text("# Note\n", encoding="utf-8")

    with pytest.raises(SemanticCorpusError, match="outside the source"):
        prepare_semantic_corpus(source, source / "prepared")

    output = tmp_path / "prepared"
    output.mkdir()
    with pytest.raises(SemanticCorpusError, match="already exists"):
        prepare_semantic_corpus(source, output)


def test_remap_supplies_original_path_and_broad_source_range(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    note = source / "Note.md"
    note.write_text("# Note\n\nAlpha relates to Beta.\n", encoding="utf-8")
    prepared = prepare_semantic_corpus(source, tmp_path / "prepared")
    chunk = load_source_map(prepared.manifest_path).chunks[0]
    graph_payload = {
        "nodes": [
            {
                "id": "alpha",
                "label": "Alpha",
                "source_file": chunk.chunk_path,
            },
            {
                "id": "beta",
                "label": "Beta",
                "source_file": chunk.chunk_path,
                "source_location": "L3",
            },
        ],
        "edges": [
            {
                "source": "alpha",
                "target": "beta",
                "relation": "relates_to",
                "confidence": "EXTRACTED",
                "confidence_score": 1.0,
                "source_file": chunk.chunk_path,
            }
        ],
    }
    graph = _write_graph(tmp_path / "graph.json", graph_payload)
    output = tmp_path / "remapped.json"

    result = remap_graph_sources(graph, prepared.manifest_path, source, prepared.path, output)
    remapped = json.loads(output.read_text(encoding="utf-8"))

    assert result.item_count == 3
    assert result.cited_chunk_count == 1
    assert result.total_chunk_count == 1
    assert result.uncited_chunks == ()
    assert remapped["nodes"][0]["source_file"] == "Note.md"
    assert remapped["nodes"][0]["source_location"] == "L1-L3"
    assert remapped["nodes"][0]["source_location_origin"] == "markos_chunk_range"
    assert remapped["nodes"][1]["source_location"] == "L3"
    assert remapped["nodes"][1]["source_location_origin"] == "provider_chunk_line"
    assert validate_graph_file(output, source).is_valid


def test_remap_rejects_fabricated_chunk_path(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    (source / "Note.md").write_text("# Note\n", encoding="utf-8")
    prepared = prepare_semantic_corpus(source, tmp_path / "prepared")
    graph = _write_graph(
        tmp_path / "graph.json",
        {
            "nodes": [
                {"id": "fake", "label": "Fake", "source_file": "chunks/fabricated.md"}
            ],
            "edges": [],
        },
    )

    with pytest.raises(SemanticCorpusError, match="allowlist"):
        remap_graph_sources(
            graph,
            prepared.manifest_path,
            source,
            prepared.path,
            tmp_path / "remapped.json",
        )


@pytest.mark.parametrize("changed", ["source", "chunk"])
def test_remap_rechecks_source_and_chunk_hashes(tmp_path: Path, changed: str) -> None:
    source = tmp_path / "source"
    source.mkdir()
    note = source / "Note.md"
    note.write_text("# Note\n", encoding="utf-8")
    prepared = prepare_semantic_corpus(source, tmp_path / "prepared")
    chunk = load_source_map(prepared.manifest_path).chunks[0]
    graph = _write_graph(
        tmp_path / "graph.json",
        {"nodes": [{"id": "note", "label": "Note", "source_file": chunk.chunk_path}], "edges": []},
    )
    if changed == "source":
        note.write_text("# Changed\n", encoding="utf-8")
    else:
        (prepared.path / chunk.chunk_path).write_text("# Changed\n", encoding="utf-8")

    with pytest.raises(SemanticCorpusError, match=f"{changed} hash changed"):
        remap_graph_sources(
            graph,
            prepared.manifest_path,
            source,
            prepared.path,
            tmp_path / "remapped.json",
        )


def test_manifest_records_source_and_chunk_hashes(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    note = source / "Note.md"
    note.write_text("# Note\n", encoding="utf-8")

    prepared = prepare_semantic_corpus(source, tmp_path / "prepared")
    chunk = load_source_map(prepared.path / SOURCE_MAP_FILENAME).chunks[0]

    assert chunk.source_hash == sha256(note.read_bytes()).hexdigest()
    assert chunk.chunk_hash == sha256((prepared.path / chunk.chunk_path).read_bytes()).hexdigest()


def test_remap_normalizes_only_missing_extracted_confidence(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    (source / "Note.md").write_text("# Note\n", encoding="utf-8")
    prepared = prepare_semantic_corpus(source, tmp_path / "prepared")
    chunk = load_source_map(prepared.manifest_path).chunks[0]
    graph = _write_graph(
        tmp_path / "graph.json",
        {
            "nodes": [
                {"id": "one", "label": "One", "source_file": chunk.chunk_path},
                {"id": "two", "label": "Two", "source_file": chunk.chunk_path},
            ],
            "edges": [
                {
                    "source": "one",
                    "target": "two",
                    "relation": "mentions",
                    "confidence": "EXTRACTED",
                    "source_file": chunk.chunk_path,
                }
            ],
        },
    )
    output = tmp_path / "remapped.json"

    remap_graph_sources(graph, prepared.manifest_path, source, prepared.path, output)
    edge = json.loads(output.read_text(encoding="utf-8"))["edges"][0]

    assert edge["confidence_score"] == 1.0
    assert edge["confidence_score_origin"] == "markos_graphify_extracted_policy"
    assert validate_graph_file(output, source).is_valid


def test_remap_reports_uncited_prepared_chunks(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    contents = "# One\n" + ("alpha\n" * 50) + "# Two\n" + ("beta\n" * 50)
    (source / "Note.md").write_text(contents, encoding="utf-8")
    prepared = prepare_semantic_corpus(
        source,
        tmp_path / "prepared",
        maximum_chunk_characters=256,
    )
    manifest = load_source_map(prepared.manifest_path)
    graph = _write_graph(
        tmp_path / "graph.json",
        {
            "nodes": [
                {
                    "id": "one",
                    "label": "One",
                    "source_file": manifest.chunks[0].chunk_path,
                }
            ],
            "edges": [],
        },
    )

    result = remap_graph_sources(
        graph,
        prepared.manifest_path,
        source,
        prepared.path,
        tmp_path / "remapped.json",
    )

    assert result.cited_chunk_count == 1
    assert result.total_chunk_count == len(manifest.chunks)
    assert result.uncited_chunks == tuple(chunk.chunk_path for chunk in manifest.chunks[1:])


@pytest.mark.parametrize("confidence", ["INFERRED", "AMBIGUOUS"])
def test_remap_rejects_missing_non_extracted_score(tmp_path: Path, confidence: str) -> None:
    source = tmp_path / "source"
    source.mkdir()
    (source / "Note.md").write_text("# Note\n", encoding="utf-8")
    prepared = prepare_semantic_corpus(source, tmp_path / "prepared")
    chunk = load_source_map(prepared.manifest_path).chunks[0]
    graph = _write_graph(
        tmp_path / "graph.json",
        {
            "nodes": [
                {"id": "one", "label": "One", "source_file": chunk.chunk_path},
                {"id": "two", "label": "Two", "source_file": chunk.chunk_path},
            ],
            "edges": [
                {
                    "source": "one",
                    "target": "two",
                    "relation": "mentions",
                    "confidence": confidence,
                    "source_file": chunk.chunk_path,
                }
            ],
        },
    )

    with pytest.raises(SemanticCorpusError, match=f"required for {confidence}"):
        remap_graph_sources(
            graph,
            prepared.manifest_path,
            source,
            prepared.path,
            tmp_path / "remapped.json",
        )


def test_extract_citations_parses_references_across_source_files(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    (source / "Cited.md").write_text(
        "# Cited\n\n## References\n\n- Doe, J. *A Title.* Some Journal, 2001.\n",
        encoding="utf-8",
    )
    (source / "Uncited.md").write_text("# Uncited\n\nNo bibliography here.\n", encoding="utf-8")

    results = extract_citations(source)

    assert [result.source_path for result in results] == ["Cited.md"]
    citations = results[0].citations
    assert len(citations) == 1
    assert citations[0].title == "A Title"
    assert citations[0].authors == ("Doe, J.",)
    assert citations[0].source_location == "L5"


def test_extract_citations_with_no_references_is_empty(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    (source / "Note.md").write_text("# Note\n\nNo bibliography here.\n", encoding="utf-8")

    assert extract_citations(source) == ()


def test_extract_citations_refuses_unresolvable_source(tmp_path: Path) -> None:
    with pytest.raises(SemanticCorpusError, match="cannot be resolved"):
        extract_citations(tmp_path / "missing")
