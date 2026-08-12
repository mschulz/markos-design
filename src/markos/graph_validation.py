"""Fail-closed validation for untrusted semantic graph output."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

_LINE_LOCATION = re.compile(r"(?:L|line\s+)(?P<start>\d+)(?:\s*[-–]\s*(?:L)?(?P<end>\d+))?", re.I)
_CONFIDENCE_LABELS = frozenset({"EXTRACTED", "INFERRED"})


class GraphValidationError(RuntimeError):
    """Raised when a graph or corpus cannot be inspected safely."""


@dataclass(frozen=True)
class ValidationIssue:
    """A deterministic problem found in an untrusted graph."""

    code: str
    location: str
    message: str


@dataclass(frozen=True)
class GraphValidationReport:
    """Validation outcome and provenance-coverage measurements."""

    node_count: int
    edge_count: int
    hyperedge_count: int
    located_item_count: int
    provenance_item_count: int
    issues: tuple[ValidationIssue, ...]

    @property
    def location_coverage(self) -> float:
        if self.provenance_item_count == 0:
            return 0.0
        return self.located_item_count / self.provenance_item_count

    @property
    def is_valid(self) -> bool:
        return not self.issues


def validate_graph_file(
    graph_path: Path,
    corpus_path: Path,
    *,
    minimum_location_coverage: float = 1.0,
) -> GraphValidationReport:
    """Validate a Graphify JSON file against an explicit, read-only corpus."""

    if not 0.0 <= minimum_location_coverage <= 1.0:
        raise GraphValidationError("minimum location coverage must be between 0 and 1")
    corpus = corpus_path.resolve()
    if not corpus.is_dir():
        raise GraphValidationError(f"corpus is not a readable directory: {corpus_path}")

    try:
        payload = json.loads(graph_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise GraphValidationError(f"cannot read graph JSON {graph_path}: {error}") from error
    if not isinstance(payload, dict):
        raise GraphValidationError("graph JSON root must be an object")

    source_lines = _corpus_manifest(corpus)
    issues: list[ValidationIssue] = []
    nodes = _object_list(payload, "nodes", issues)
    edges = _object_list(payload, "edges", issues)
    hyperedges = _object_list(payload, "hyperedges", issues, allow_missing=True)
    if not nodes:
        issues.append(ValidationIssue("empty_graph", "nodes", "at least one node is required"))

    node_ids: set[str] = set()
    located = 0
    provenance_items = 0
    for number, node in enumerate(nodes):
        item_location = f"nodes[{number}]"
        node_id = _required_string(node, "id", item_location, issues)
        _required_string(node, "label", item_location, issues)
        if node_id:
            if node_id in node_ids:
                issues.append(
                    ValidationIssue("duplicate_node_id", item_location, f"duplicate id {node_id!r}")
                )
            node_ids.add(node_id)
        provenance_items += 1
        located += _validate_provenance(node, item_location, source_lines, issues)

    for number, edge in enumerate(edges):
        item_location = f"edges[{number}]"
        for field in ("source", "target"):
            endpoint = _required_string(edge, field, item_location, issues)
            if endpoint and endpoint not in node_ids:
                issues.append(
                    ValidationIssue(
                        "dangling_endpoint",
                        f"{item_location}.{field}",
                        f"unknown node id {endpoint!r}",
                    )
                )
        _required_string(edge, "relation", item_location, issues)
        _validate_confidence(edge, item_location, issues)
        provenance_items += 1
        located += _validate_provenance(edge, item_location, source_lines, issues)

    hyperedge_ids: set[str] = set()
    for number, hyperedge in enumerate(hyperedges):
        item_location = f"hyperedges[{number}]"
        hyperedge_id = _required_string(hyperedge, "id", item_location, issues)
        _required_string(hyperedge, "label", item_location, issues)
        if hyperedge_id:
            if hyperedge_id in hyperedge_ids:
                issues.append(
                    ValidationIssue(
                        "duplicate_hyperedge_id", item_location, f"duplicate id {hyperedge_id!r}"
                    )
                )
            hyperedge_ids.add(hyperedge_id)
        members = hyperedge.get("nodes")
        if not isinstance(members, list) or not members:
            issues.append(
                ValidationIssue(
                    "invalid_members",
                    f"{item_location}.nodes",
                    "must be a non-empty list",
                )
            )
        else:
            for member_number, member in enumerate(members):
                if not isinstance(member, str) or member not in node_ids:
                    issues.append(
                        ValidationIssue(
                            "dangling_endpoint",
                            f"{item_location}.nodes[{member_number}]",
                            f"unknown node id {member!r}",
                        )
                    )
        _required_string(hyperedge, "relation", item_location, issues)
        _validate_confidence(hyperedge, item_location, issues)
        provenance_items += 1
        located += _validate_provenance(hyperedge, item_location, source_lines, issues)

    coverage = located / provenance_items if provenance_items else 0.0
    if coverage < minimum_location_coverage:
        issues.append(
            ValidationIssue(
                "insufficient_location_coverage",
                "graph",
                f"location coverage {coverage:.1%} is below required "
                f"{minimum_location_coverage:.1%}",
            )
        )

    return GraphValidationReport(
        node_count=len(nodes),
        edge_count=len(edges),
        hyperedge_count=len(hyperedges),
        located_item_count=located,
        provenance_item_count=provenance_items,
        issues=tuple(issues),
    )


def _corpus_manifest(corpus: Path) -> dict[str, int | None]:
    manifest: dict[str, int | None] = {}
    try:
        paths = sorted(path for path in corpus.rglob("*") if path.is_file())
    except OSError as error:
        raise GraphValidationError(f"cannot inspect corpus {corpus}: {error}") from error
    for path in paths:
        relative = path.relative_to(corpus).as_posix()
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            manifest[relative] = None
        else:
            manifest[relative] = len(text.splitlines()) or 1
    return manifest


def _object_list(
    payload: Mapping[str, Any],
    field: str,
    issues: list[ValidationIssue],
    *,
    allow_missing: bool = False,
) -> list[Mapping[str, Any]]:
    value = payload.get(field, [] if allow_missing else None)
    if not isinstance(value, list):
        issues.append(ValidationIssue("invalid_collection", field, "must be a list"))
        return []
    objects: list[Mapping[str, Any]] = []
    for number, item in enumerate(value):
        if isinstance(item, dict):
            objects.append(item)
        else:
            issues.append(
                ValidationIssue("invalid_item", f"{field}[{number}]", "must be an object")
            )
    return objects


def _required_string(
    item: Mapping[str, Any],
    field: str,
    location: str,
    issues: list[ValidationIssue],
) -> str | None:
    value = item.get(field)
    if not isinstance(value, str) or not value.strip():
        issues.append(
            ValidationIssue("missing_field", f"{location}.{field}", "must be a non-empty string")
        )
        return None
    return value


def _validate_provenance(
    item: Mapping[str, Any],
    location: str,
    source_lines: Mapping[str, int | None],
    issues: list[ValidationIssue],
) -> int:
    source = item.get("source_file")
    if not isinstance(source, str) or not source.strip():
        issues.append(
            ValidationIssue(
                "missing_source_file", f"{location}.source_file", "source provenance is required"
            )
        )
        source_key: str | None = None
    else:
        source_key = _safe_source_path(source)
        if source_key is None or source_key not in source_lines:
            issues.append(
                ValidationIssue(
                    "unknown_source_file",
                    f"{location}.source_file",
                    f"path is not in the corpus allowlist: {source!r}",
                )
            )

    source_location = item.get("source_location")
    if not isinstance(source_location, str) or not source_location.strip():
        return 0

    if source_key is not None and source_key in source_lines:
        _validate_line_location(source_location.strip(), source_lines[source_key], location, issues)
    return 1


def _safe_source_path(value: str) -> str | None:
    if "\\" in value:
        return None
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts or not path.parts:
        return None
    return path.as_posix()


def _validate_line_location(
    location_value: str,
    line_count: int | None,
    item_location: str,
    issues: list[ValidationIssue],
) -> None:
    match = _LINE_LOCATION.fullmatch(location_value)
    if match is None or line_count is None:
        return
    start = int(match.group("start"))
    end = int(match.group("end") or start)
    if start < 1 or end < start or end > line_count:
        issues.append(
            ValidationIssue(
                "invalid_source_location",
                f"{item_location}.source_location",
                f"line range {location_value!r} is outside 1-{line_count}",
            )
        )


def _validate_confidence(
    item: Mapping[str, Any],
    location: str,
    issues: list[ValidationIssue],
) -> None:
    label = item.get("confidence")
    if label not in _CONFIDENCE_LABELS:
        issues.append(
            ValidationIssue(
                "invalid_confidence",
                f"{location}.confidence",
                f"must be one of {sorted(_CONFIDENCE_LABELS)}",
            )
        )
    score = item.get("confidence_score")
    if isinstance(score, bool) or not isinstance(score, int | float) or not 0.0 <= score <= 1.0:
        issues.append(
            ValidationIssue(
                "invalid_confidence_score",
                f"{location}.confidence_score",
                "must be a number between 0 and 1",
            )
        )
