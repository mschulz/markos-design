"""Rebuildable DuckDB index for parsed knowledge documents."""

from __future__ import annotations

import json
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import duckdb

from markos.domain import KnowledgeDocument
from markos.markdown import load_markdown

SCHEMA_VERSION = 1


class KnowledgeIndexError(ValueError):
    """Raised when a knowledge index cannot be safely rebuilt."""


@dataclass(frozen=True)
class IndexSummary:
    """Counts and location produced by a successful index rebuild."""

    path: Path
    document_count: int
    link_count: int
    rebuilt_at: datetime


def rebuild_vault_index(vault_path: Path, index_path: Path) -> IndexSummary:
    """Parse a vault read-only, then replace its external rebuildable index."""

    root = _resolved_directory(vault_path, label="vault")
    destination = index_path.expanduser().resolve(strict=False)
    if _is_within(destination, root):
        raise KnowledgeIndexError("index path must be outside the Markdown vault")

    documents = load_markdown(root)
    return rebuild_index(destination, documents)


def rebuild_index(index_path: Path, documents: Iterable[KnowledgeDocument]) -> IndexSummary:
    """Atomically replace index contents with ``documents``."""

    destination = index_path.expanduser().resolve(strict=False)
    materialized = tuple(documents)
    _validate_unique_paths(materialized)

    try:
        destination.parent.mkdir(parents=True, exist_ok=True)
    except OSError as error:
        message = f"could not create index directory: {destination.parent}"
        raise KnowledgeIndexError(message) from error

    rebuilt_at = datetime.now(UTC)
    document_rows = [
        (
            document.identifier,
            document.title,
            document.body,
            json.dumps(document.metadata, default=_json_default, sort_keys=True),
        )
        for document in materialized
    ]
    link_rows = [
        (document.identifier, ordinal, target)
        for document in materialized
        for ordinal, target in enumerate(document.links)
    ]

    try:
        connection = duckdb.connect(str(destination))
    except duckdb.Error as error:
        raise KnowledgeIndexError(f"could not open index: {destination}") from error

    try:
        connection.begin()
        _create_schema(connection)
        connection.execute("DELETE FROM document_links")
        connection.execute("DELETE FROM documents")
        connection.execute("DELETE FROM index_state")
        if document_rows:
            connection.executemany(
                "INSERT INTO documents (path, title, body, metadata) VALUES (?, ?, ?, ?)",
                document_rows,
            )
        if link_rows:
            connection.executemany(
                "INSERT INTO document_links (source_path, ordinal, target) VALUES (?, ?, ?)",
                link_rows,
            )
        connection.execute(
            """
            INSERT INTO index_state
                (singleton, schema_version, rebuilt_at, document_count, link_count)
            VALUES (TRUE, ?, ?, ?, ?)
            """,
            [SCHEMA_VERSION, rebuilt_at, len(document_rows), len(link_rows)],
        )
        connection.commit()
    except duckdb.Error as error:
        connection.rollback()
        raise KnowledgeIndexError(f"could not rebuild index: {destination}") from error
    finally:
        connection.close()

    return IndexSummary(
        path=destination,
        document_count=len(document_rows),
        link_count=len(link_rows),
        rebuilt_at=rebuilt_at,
    )


def _create_schema(connection: duckdb.DuckDBPyConnection) -> None:
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS documents (
            path VARCHAR PRIMARY KEY,
            title VARCHAR NOT NULL,
            body VARCHAR NOT NULL,
            metadata JSON NOT NULL
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS document_links (
            source_path VARCHAR NOT NULL,
            ordinal INTEGER NOT NULL,
            target VARCHAR NOT NULL,
            PRIMARY KEY (source_path, ordinal)
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS index_state (
            singleton BOOLEAN PRIMARY KEY CHECK (singleton),
            schema_version INTEGER NOT NULL,
            rebuilt_at TIMESTAMPTZ NOT NULL,
            document_count BIGINT NOT NULL,
            link_count BIGINT NOT NULL
        )
        """
    )


def _validate_unique_paths(documents: Iterable[KnowledgeDocument]) -> None:
    seen: set[str] = set()
    for document in documents:
        if document.identifier in seen:
            raise KnowledgeIndexError(f"duplicate document path: {document.identifier}")
        seen.add(document.identifier)


def _resolved_directory(path: Path, *, label: str) -> Path:
    try:
        resolved = path.expanduser().resolve(strict=True)
    except OSError as error:
        raise KnowledgeIndexError(f"{label} cannot be resolved: {path}") from error
    if not resolved.is_dir():
        raise KnowledgeIndexError(f"{label} is not a directory: {path}")
    return resolved


def _is_within(path: Path, directory: Path) -> bool:
    try:
        path.relative_to(directory)
    except ValueError:
        return False
    return True


def _json_default(value: Any) -> str:
    return str(value)
