"""Deterministic, heading-aware preparation for grounded semantic extraction."""

from __future__ import annotations

import json
import os
import re
import shutil
import tempfile
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path, PurePosixPath
from typing import Any

from markos.markdown import MarkdownDiscoveryError, discover_markdown

SOURCE_MAP_FORMAT = "markos-source-map-v1"
SOURCE_MAP_FILENAME = "source-map.json"
GRAPHIFY_IGNORE_FILENAME = ".graphifyignore"
_HEADING = re.compile(r"^ {0,3}#{1,6}(?:[ \t]+|$)")
_LINE_LOCATION = re.compile(r"(?:L|line\s+)(?P<start>\d+)(?:\s*[-–]\s*(?:L)?(?P<end>\d+))?", re.I)


class SemanticCorpusError(RuntimeError):
    """Raised when semantic preparation or provenance remapping is unsafe."""


@dataclass(frozen=True)
class SourceChunk:
    """One exact source slice and its deterministic provenance."""

    chunk_path: str
    source_path: str
    source_hash: str
    chunk_hash: str
    start_line: int
    end_line: int

    @property
    def source_location(self) -> str:
        if self.start_line == self.end_line:
            return f"L{self.start_line}"
        return f"L{self.start_line}-L{self.end_line}"


@dataclass(frozen=True)
class SemanticCorpusManifest:
    """Portable mapping from generated chunks back to canonical Markdown."""

    chunks: tuple[SourceChunk, ...]

    @property
    def source_count(self) -> int:
        return len({chunk.source_path for chunk in self.chunks})


@dataclass(frozen=True)
class PreparedCorpus:
    """Summary of a successfully materialized external semantic corpus."""

    path: Path
    manifest_path: Path
    source_count: int
    chunk_count: int


@dataclass(frozen=True)
class RemappedGraph:
    """Summary of a graph copied with canonical source provenance."""

    path: Path
    item_count: int
    cited_chunk_count: int
    total_chunk_count: int
    uncited_chunks: tuple[str, ...]


def prepare_semantic_corpus(
    source_path: Path,
    output_path: Path,
    *,
    maximum_chunk_characters: int = 6000,
) -> PreparedCorpus:
    """Write a new external chunk corpus without changing the source tree."""

    if maximum_chunk_characters < 256:
        raise SemanticCorpusError("maximum chunk characters must be at least 256")
    source = _resolved_directory(source_path, label="source corpus")
    output = output_path.expanduser().resolve(strict=False)
    if _is_within(output, source):
        raise SemanticCorpusError("prepared corpus must be outside the source corpus")
    if output.exists():
        raise SemanticCorpusError(f"prepared corpus already exists: {output}")

    try:
        markdown_paths = discover_markdown(source)
    except MarkdownDiscoveryError as error:
        raise SemanticCorpusError(str(error)) from error
    if not markdown_paths:
        raise SemanticCorpusError("source corpus contains no Markdown files")

    chunks_with_content: list[tuple[SourceChunk, bytes]] = []
    for source_number, relative_path in enumerate(markdown_paths, start=1):
        source_file = source / relative_path
        try:
            source_bytes = source_file.read_bytes()
            source_text = source_bytes.decode("utf-8")
        except (OSError, UnicodeError) as error:
            raise SemanticCorpusError(
                f"could not read Markdown source: {relative_path.as_posix()}"
            ) from error
        source_hash = sha256(source_bytes).hexdigest()
        slices = _heading_aware_slices(source_text, maximum_chunk_characters)
        for number, (start_line, end_line, content) in enumerate(slices, start=1):
            chunk_bytes = content.encode("utf-8")
            chunk_path = f"chunks/{source_number:04}/{number:04}.md"
            chunks_with_content.append(
                (
                    SourceChunk(
                        chunk_path=chunk_path,
                        source_path=relative_path.as_posix(),
                        source_hash=source_hash,
                        chunk_hash=sha256(chunk_bytes).hexdigest(),
                        start_line=start_line,
                        end_line=end_line,
                    ),
                    chunk_bytes,
                )
            )

    output.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".{output.name}-", dir=output.parent))
    try:
        for chunk, chunk_content in chunks_with_content:
            destination = staging / PurePosixPath(chunk.chunk_path)
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(chunk_content)
        manifest = SemanticCorpusManifest(tuple(chunk for chunk, _ in chunks_with_content))
        (staging / SOURCE_MAP_FILENAME).write_text(
            _render_manifest(manifest),
            encoding="utf-8",
        )
        (staging / GRAPHIFY_IGNORE_FILENAME).write_text(
            f"{SOURCE_MAP_FILENAME}\n",
            encoding="utf-8",
        )
        os.replace(staging, output)
    except OSError as error:
        raise SemanticCorpusError(f"could not write prepared corpus: {output}") from error
    finally:
        if staging.exists():
            shutil.rmtree(staging)

    return PreparedCorpus(
        path=output,
        manifest_path=output / SOURCE_MAP_FILENAME,
        source_count=len(markdown_paths),
        chunk_count=len(chunks_with_content),
    )


def remap_graph_sources(
    graph_path: Path,
    manifest_path: Path,
    source_corpus_path: Path,
    prepared_corpus_path: Path,
    output_path: Path,
) -> RemappedGraph:
    """Copy a graph while replacing verified chunk provenance with source ranges."""

    source_corpus = _resolved_directory(source_corpus_path, label="source corpus")
    prepared_corpus = _resolved_directory(prepared_corpus_path, label="prepared corpus")
    output = output_path.expanduser().resolve(strict=False)
    if output.exists():
        raise SemanticCorpusError(f"remapped graph already exists: {output}")
    if _is_within(output, source_corpus):
        raise SemanticCorpusError("remapped graph must be outside the source corpus")

    manifest = load_source_map(manifest_path)
    chunks = {chunk.chunk_path: chunk for chunk in manifest.chunks}
    _verify_manifest_files(chunks, source_corpus, prepared_corpus)
    payload = _read_json_object(graph_path, label="graph")

    item_count = 0
    cited_chunks: set[str] = set()
    for collection_name in ("nodes", "edges", "hyperedges"):
        collection = payload.get(collection_name, [] if collection_name == "hyperedges" else None)
        if not isinstance(collection, list):
            raise SemanticCorpusError(f"graph field {collection_name!r} must be a list")
        for number, item in enumerate(collection):
            if not isinstance(item, dict):
                raise SemanticCorpusError(f"{collection_name}[{number}] must be an object")
            cited_chunks.add(_remap_item(item, chunks, f"{collection_name}[{number}]"))
            if collection_name != "nodes":
                _normalize_graphify_confidence(item, f"{collection_name}[{number}]")
            item_count += 1

    try:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    except OSError as error:
        raise SemanticCorpusError(f"could not write remapped graph: {output}") from error
    uncited_chunks = tuple(sorted(chunks.keys() - cited_chunks))
    return RemappedGraph(
        path=output,
        item_count=item_count,
        cited_chunk_count=len(cited_chunks),
        total_chunk_count=len(chunks),
        uncited_chunks=uncited_chunks,
    )


def load_source_map(path: Path) -> SemanticCorpusManifest:
    """Read and validate a portable source-map manifest."""

    payload = _read_json_object(path, label="source map")
    if payload.get("format") != SOURCE_MAP_FORMAT:
        raise SemanticCorpusError(f"unsupported source-map format: {payload.get('format')!r}")
    raw_chunks = payload.get("chunks")
    if not isinstance(raw_chunks, list) or not raw_chunks:
        raise SemanticCorpusError("source map must contain at least one chunk")
    chunks: list[SourceChunk] = []
    seen_paths: set[str] = set()
    for number, raw_chunk in enumerate(raw_chunks):
        if not isinstance(raw_chunk, dict):
            raise SemanticCorpusError(f"source-map chunk {number} must be an object")
        chunk = _parse_chunk(raw_chunk, number)
        if chunk.chunk_path in seen_paths:
            raise SemanticCorpusError(f"duplicate chunk path: {chunk.chunk_path}")
        seen_paths.add(chunk.chunk_path)
        chunks.append(chunk)
    return SemanticCorpusManifest(tuple(chunks))


def _heading_aware_slices(text: str, maximum_characters: int) -> tuple[tuple[int, int, str], ...]:
    lines = text.splitlines(keepends=True)
    if not lines:
        return ((1, 1, ""),)

    boundaries = [0]
    boundaries.extend(index for index, line in enumerate(lines) if index and _HEADING.match(line))
    boundaries.append(len(lines))
    sections = [
        (boundaries[index], boundaries[index + 1])
        for index in range(len(boundaries) - 1)
        if boundaries[index] < boundaries[index + 1]
    ]

    slices: list[tuple[int, int]] = []
    pending_start: int | None = None
    pending_end = 0
    pending_size = 0
    for section_start, section_end in sections:
        section_size = sum(len(line) for line in lines[section_start:section_end])
        if section_size > maximum_characters:
            if pending_start is not None:
                slices.append((pending_start, pending_end))
                pending_start = None
                pending_size = 0
            slices.extend(
                _bounded_line_slices(lines, section_start, section_end, maximum_characters)
            )
        elif pending_start is None:
            pending_start = section_start
            pending_end = section_end
            pending_size = section_size
        elif pending_size + section_size <= maximum_characters:
            pending_end = section_end
            pending_size += section_size
        else:
            slices.append((pending_start, pending_end))
            pending_start = section_start
            pending_end = section_end
            pending_size = section_size
    if pending_start is not None:
        slices.append((pending_start, pending_end))

    return tuple(
        (start + 1, end, "".join(lines[start:end]))
        for start, end in slices
    )


def _bounded_line_slices(
    lines: list[str],
    start: int,
    end: int,
    maximum_characters: int,
) -> list[tuple[int, int]]:
    slices: list[tuple[int, int]] = []
    chunk_start = start
    chunk_size = 0
    for index in range(start, end):
        line_size = len(lines[index])
        if chunk_size and chunk_size + line_size > maximum_characters:
            slices.append((chunk_start, index))
            chunk_start = index
            chunk_size = 0
        chunk_size += line_size
    slices.append((chunk_start, end))
    return slices


def _render_manifest(manifest: SemanticCorpusManifest) -> str:
    payload = {
        "format": SOURCE_MAP_FORMAT,
        "chunks": [
            {
                "chunk_path": chunk.chunk_path,
                "source_path": chunk.source_path,
                "source_hash": chunk.source_hash,
                "chunk_hash": chunk.chunk_hash,
                "start_line": chunk.start_line,
                "end_line": chunk.end_line,
            }
            for chunk in manifest.chunks
        ],
    }
    return json.dumps(payload, indent=2, sort_keys=True) + "\n"


def _parse_chunk(payload: dict[str, Any], number: int) -> SourceChunk:
    def required_string(field: str) -> str:
        value = payload.get(field)
        if not isinstance(value, str) or not value:
            raise SemanticCorpusError(f"source-map chunk {number} has invalid {field}")
        return value

    chunk_path = required_string("chunk_path")
    source_path = required_string("source_path")
    if not _safe_relative_path(chunk_path) or not _safe_relative_path(source_path):
        raise SemanticCorpusError(f"source-map chunk {number} has an unsafe path")
    source_hash = required_string("source_hash")
    chunk_hash = required_string("chunk_hash")
    if not _valid_hash(source_hash) or not _valid_hash(chunk_hash):
        raise SemanticCorpusError(f"source-map chunk {number} has an invalid SHA-256")
    start_line = payload.get("start_line")
    end_line = payload.get("end_line")
    if (
        isinstance(start_line, bool)
        or not isinstance(start_line, int)
        or isinstance(end_line, bool)
        or not isinstance(end_line, int)
        or start_line < 1
        or end_line < start_line
    ):
        raise SemanticCorpusError(f"source-map chunk {number} has an invalid line range")
    return SourceChunk(
        chunk_path=chunk_path,
        source_path=source_path,
        source_hash=source_hash,
        chunk_hash=chunk_hash,
        start_line=start_line,
        end_line=end_line,
    )


def _verify_manifest_files(
    chunks: dict[str, SourceChunk],
    source_corpus: Path,
    prepared_corpus: Path,
) -> None:
    verified_sources: set[str] = set()
    for chunk in chunks.values():
        if chunk.source_path not in verified_sources:
            if (
                _relative_file_hash(source_corpus, chunk.source_path, "source")
                != chunk.source_hash
            ):
                raise SemanticCorpusError(f"source hash changed: {chunk.source_path}")
            verified_sources.add(chunk.source_path)
        if (
            _relative_file_hash(prepared_corpus, chunk.chunk_path, "prepared chunk")
            != chunk.chunk_hash
        ):
            raise SemanticCorpusError(f"prepared chunk hash changed: {chunk.chunk_path}")


def _remap_item(item: dict[str, Any], chunks: dict[str, SourceChunk], location: str) -> str:
    chunk_path = item.get("source_file")
    if not isinstance(chunk_path, str) or chunk_path not in chunks:
        raise SemanticCorpusError(
            f"{location}.source_file is not in the prepared-corpus allowlist: {chunk_path!r}"
        )
    chunk = chunks[chunk_path]
    source_location = item.get("source_location")
    item["source_file"] = chunk.source_path
    remapped_location, origin = _remapped_location(source_location, chunk)
    item["source_location"] = remapped_location
    item["source_location_origin"] = origin
    return chunk_path


def _normalize_graphify_confidence(item: dict[str, Any], location: str) -> None:
    if item.get("confidence_score") is not None:
        return
    confidence = item.get("confidence")
    if confidence == "EXTRACTED":
        item["confidence_score"] = 1.0
        item["confidence_score_origin"] = "markos_graphify_extracted_policy"
        return
    if confidence in {"INFERRED", "AMBIGUOUS"}:
        raise SemanticCorpusError(
            f"{location}.confidence_score is required for {confidence} relationships"
        )


def _remapped_location(value: object, chunk: SourceChunk) -> tuple[str, str]:
    if not isinstance(value, str):
        return chunk.source_location, "markos_chunk_range"
    match = _LINE_LOCATION.fullmatch(value.strip())
    if match is None:
        return chunk.source_location, "markos_chunk_range"
    relative_start = int(match.group("start"))
    relative_end = int(match.group("end") or relative_start)
    chunk_line_count = chunk.end_line - chunk.start_line + 1
    if relative_start < 1 or relative_end < relative_start or relative_end > chunk_line_count:
        raise SemanticCorpusError(
            f"chunk-relative location {value!r} is outside 1-{chunk_line_count}"
        )
    source_start = chunk.start_line + relative_start - 1
    source_end = chunk.start_line + relative_end - 1
    if source_start == source_end:
        return f"L{source_start}", "provider_chunk_line"
    return f"L{source_start}-L{source_end}", "provider_chunk_line"


def _read_json_object(path: Path, *, label: str) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise SemanticCorpusError(f"could not read {label} JSON {path}: {error}") from error
    if not isinstance(payload, dict):
        raise SemanticCorpusError(f"{label} JSON root must be an object")
    return payload


def _relative_file_hash(root: Path, relative: str, label: str) -> str:
    path = root / PurePosixPath(relative)
    try:
        resolved = path.resolve(strict=True)
        resolved.relative_to(root)
        if path.absolute() != resolved or not resolved.is_file():
            raise OSError("not a regular file")
        return sha256(resolved.read_bytes()).hexdigest()
    except (OSError, ValueError) as error:
        raise SemanticCorpusError(f"could not read {label}: {path}") from error


def _valid_hash(value: str) -> bool:
    return len(value) == 64 and all(character in "0123456789abcdef" for character in value)


def _safe_relative_path(value: str) -> bool:
    if "\\" in value:
        return False
    path = PurePosixPath(value)
    return bool(path.parts) and not path.is_absolute() and ".." not in path.parts


def _resolved_directory(path: Path, *, label: str) -> Path:
    try:
        resolved = path.expanduser().resolve(strict=True)
    except OSError as error:
        raise SemanticCorpusError(f"{label} cannot be resolved: {path}") from error
    if not resolved.is_dir():
        raise SemanticCorpusError(f"{label} is not a directory: {path}")
    return resolved


def _is_within(path: Path, directory: Path) -> bool:
    try:
        path.relative_to(directory)
    except ValueError:
        return False
    return True
