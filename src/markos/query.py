"""Read-only queries over a rebuilt MarkOS DuckDB index."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path, PurePath
from typing import Any, cast

import duckdb

from markos.domain import KnowledgeDocument
from markos.index import SCHEMA_VERSION


class KnowledgeQueryError(ValueError):
    """Raised when an index cannot be safely queried."""


@dataclass(frozen=True)
class SearchResult:
    """A deterministic search result with a short body excerpt."""

    path: str
    title: str
    snippet: str


def search_index(index_path: Path, query: str, *, limit: int = 20) -> tuple[SearchResult, ...]:
    """Search paths, titles, and bodies in a compatible index."""

    term = query.strip()
    if not term:
        raise KnowledgeQueryError("search query must not be blank")
    if not 1 <= limit <= 100:
        raise KnowledgeQueryError("search limit must be between 1 and 100")

    connection = _open_index(index_path)
    try:
        rows = connection.execute(
            """
            SELECT path, title, body
            FROM documents
            WHERE contains(lower(path), lower(?))
               OR contains(lower(title), lower(?))
               OR contains(lower(body), lower(?))
            ORDER BY
                CASE
                    WHEN lower(title) = lower(?) THEN 0
                    WHEN starts_with(lower(title), lower(?)) THEN 1
                    WHEN contains(lower(title), lower(?)) THEN 2
                    WHEN contains(lower(path), lower(?)) THEN 3
                    ELSE 4
                END,
                lower(title),
                path
            LIMIT ?
            """,
            [term, term, term, term, term, term, term, limit],
        ).fetchall()
    except duckdb.Error as error:
        raise KnowledgeQueryError(f"could not search index: {index_path}") from error
    finally:
        connection.close()

    return tuple(
        SearchResult(path=str(path), title=str(title), snippet=_snippet(str(body), term))
        for path, title, body in rows
    )


def read_document(index_path: Path, identifier: str) -> KnowledgeDocument | None:
    """Read one indexed document by its vault-relative identifier."""

    connection = _open_index(index_path)
    try:
        row = connection.execute(
            "SELECT path, title, body, metadata FROM documents WHERE path = ?",
            [identifier],
        ).fetchone()
        if row is None:
            return None
        links = connection.execute(
            "SELECT target FROM document_links WHERE source_path = ? ORDER BY ordinal",
            [identifier],
        ).fetchall()
    except duckdb.Error as error:
        raise KnowledgeQueryError(f"could not read index: {index_path}") from error
    finally:
        connection.close()

    path, title, body, encoded_metadata = row
    try:
        metadata = json.loads(str(encoded_metadata))
    except (json.JSONDecodeError, TypeError) as error:
        raise KnowledgeQueryError(f"invalid document metadata in index: {identifier}") from error
    if not isinstance(metadata, dict):
        raise KnowledgeQueryError(f"invalid document metadata in index: {identifier}")

    return KnowledgeDocument(
        path=PurePath(str(path)),
        title=str(title),
        body=str(body),
        metadata=cast(dict[str, Any], metadata),
        links=tuple(str(target) for (target,) in links),
    )


def _open_index(index_path: Path) -> duckdb.DuckDBPyConnection:
    path = index_path.expanduser().resolve(strict=False)
    if not path.is_file():
        raise KnowledgeQueryError(f"index does not exist: {path}")

    try:
        connection = duckdb.connect(str(path), read_only=True)
    except duckdb.Error as error:
        raise KnowledgeQueryError(f"could not open index read-only: {path}") from error

    try:
        state = connection.execute("SELECT schema_version FROM index_state").fetchone()
    except duckdb.Error as error:
        connection.close()
        raise KnowledgeQueryError(f"not a MarkOS index: {path}") from error

    if state is None or state[0] != SCHEMA_VERSION:
        connection.close()
        found = "missing" if state is None else str(state[0])
        raise KnowledgeQueryError(
            f"incompatible index schema: expected {SCHEMA_VERSION}, found {found}"
        )
    return connection


def _snippet(body: str, query: str, *, width: int = 160) -> str:
    compact = " ".join(body.split())
    if len(compact) <= width:
        return compact

    match_at = compact.lower().find(query.lower())
    if match_at < 0:
        return f"{compact[: width - 1].rstrip()}…"

    start = max(0, match_at - width // 3)
    end = min(len(compact), start + width)
    excerpt = compact[start:end].strip()
    if start > 0:
        excerpt = f"…{excerpt}"
    if end < len(compact):
        excerpt = f"{excerpt}…"
    return excerpt
