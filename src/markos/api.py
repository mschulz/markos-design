"""Read-only HTTP API backed exclusively by a MarkOS index."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel, ConfigDict

from markos import __version__
from markos.query import (
    KnowledgeQueryError,
    backlinks,
    index_status,
    outgoing_links,
    read_document,
    search_index,
)


class ApiModel(BaseModel):
    """Strict base model for stable API responses."""

    model_config = ConfigDict(extra="forbid")


class HealthResponse(ApiModel):
    status: str
    schema_version: int
    rebuilt_at: datetime
    document_count: int
    link_count: int


class SearchItem(ApiModel):
    path: str
    title: str
    snippet: str


class DocumentResponse(ApiModel):
    path: str
    title: str
    body: str
    metadata: dict[str, Any]
    links: tuple[str, ...]


class LinkResponse(ApiModel):
    source_path: str
    ordinal: int
    target: str
    resolved_path: str | None


def create_app(index_path: Path) -> FastAPI:
    """Create an API that opens ``index_path`` read-only for every request."""

    app = FastAPI(
        title="MarkOS",
        description="Read-only access to a rebuildable MarkOS knowledge index.",
        version=__version__,
    )

    @app.get("/health", response_model=HealthResponse)
    def health() -> HealthResponse:
        try:
            state = index_status(index_path)
        except KnowledgeQueryError as error:
            raise HTTPException(status_code=503, detail=str(error)) from error
        return HealthResponse(status="ok", **state.__dict__)

    @app.get("/search", response_model=list[SearchItem])
    def search(
        q: str = Query(min_length=1),
        limit: int = Query(default=20, ge=1, le=100),
    ) -> list[SearchItem]:
        if not q.strip():
            raise HTTPException(status_code=400, detail="search query must not be blank")
        try:
            results = search_index(index_path, q, limit=limit)
        except KnowledgeQueryError as error:
            raise HTTPException(status_code=503, detail=str(error)) from error
        return [SearchItem(**result.__dict__) for result in results]

    @app.get("/documents/{identifier:path}", response_model=DocumentResponse)
    def document(identifier: str) -> DocumentResponse:
        try:
            indexed_document = read_document(index_path, identifier)
        except KnowledgeQueryError as error:
            raise HTTPException(status_code=503, detail=str(error)) from error
        if indexed_document is None:
            raise HTTPException(status_code=404, detail="document not found")
        return DocumentResponse(
            path=indexed_document.identifier,
            title=indexed_document.title,
            body=indexed_document.body,
            metadata=indexed_document.metadata,
            links=indexed_document.links,
        )

    @app.get("/graph/outgoing", response_model=list[LinkResponse])
    def outgoing(path: str = Query(min_length=1)) -> list[LinkResponse]:
        return _link_response(index_path, path, incoming=False)

    @app.get("/graph/backlinks", response_model=list[LinkResponse])
    def incoming(path: str = Query(min_length=1)) -> list[LinkResponse]:
        return _link_response(index_path, path, incoming=True)

    return app


def _link_response(index_path: Path, path: str, *, incoming: bool) -> list[LinkResponse]:
    try:
        results = backlinks(index_path, path) if incoming else outgoing_links(index_path, path)
    except KnowledgeQueryError as error:
        raise HTTPException(status_code=503, detail=str(error)) from error
    return [LinkResponse(**result.__dict__) for result in results]
