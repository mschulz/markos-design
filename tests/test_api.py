import asyncio
import json
from pathlib import Path, PurePath
from typing import Any

from fastapi import FastAPI
from starlette.types import Message, Scope

from markos.api import create_app
from markos.domain import KnowledgeDocument
from markos.index import rebuild_index
from markos.markdown import IngestionIssue


def _build_api_index(path: Path) -> None:
    rebuild_index(
        path,
        [
            KnowledgeDocument(
                path=PurePath("Projects/MarkOS.md"),
                title="MarkOS",
                body="# MarkOS\n\nA local-first knowledge engine.",
                metadata={"status": "active"},
                links=("Vision", "Missing"),
            ),
            KnowledgeDocument(
                path=PurePath("Vision.md"),
                title="Vision",
                body="# Vision\n\nKnowledge should outlive its tools.",
            ),
        ],
    )


def _request(app: FastAPI, path: str, query: str = "") -> tuple[int, Any]:
    messages: list[Message] = []

    async def receive() -> Message:
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message: Message) -> None:
        messages.append(message)

    scope: Scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "GET",
        "scheme": "http",
        "path": path,
        "raw_path": path.encode(),
        "query_string": query.encode(),
        "headers": [],
        "client": ("test", 1),
        "server": ("test", 80),
        "root_path": "",
    }
    asyncio.run(app(scope, receive, send))
    start = next(message for message in messages if message["type"] == "http.response.start")
    body = b"".join(
        message.get("body", b"")
        for message in messages
        if message["type"] == "http.response.body"
    )
    return int(start["status"]), json.loads(body)


def test_health_reports_rebuild_state(tmp_path: Path) -> None:
    index_path = tmp_path / "index.duckdb"
    _build_api_index(index_path)

    status, body = _request(create_app(index_path), "/health")

    assert status == 200
    assert body["status"] == "ok"
    assert body["schema_version"] == 3
    assert body["document_count"] == 2
    assert body["link_count"] == 2
    assert body["rejected_count"] == 0


def test_search_and_document_routes(tmp_path: Path) -> None:
    index_path = tmp_path / "index.duckdb"
    _build_api_index(index_path)
    app = create_app(index_path)

    search_status, results = _request(app, "/search", "q=knowledge&limit=1")
    document_status, document = _request(app, "/documents/Projects/MarkOS.md")
    missing_status, missing = _request(app, "/documents/Missing.md")

    assert search_status == 200
    assert results[0]["path"] == "Projects/MarkOS.md"
    assert document_status == 200
    assert document["metadata"] == {"status": "active"}
    assert document["links"] == ["Vision", "Missing"]
    assert missing_status == 404
    assert missing["detail"] == "document not found"


def test_graph_routes_return_resolved_and_unresolved_links(tmp_path: Path) -> None:
    index_path = tmp_path / "index.duckdb"
    _build_api_index(index_path)
    app = create_app(index_path)

    outgoing_status, outgoing = _request(
        app,
        "/graph/outgoing",
        "path=Projects%2FMarkOS.md",
    )
    backlink_status, backlinks = _request(app, "/graph/backlinks", "path=Vision.md")

    assert outgoing_status == 200
    assert [(link["target"], link["resolved_path"]) for link in outgoing] == [
        ("Vision", "Vision.md"),
        ("Missing", None),
    ]
    assert backlink_status == 200
    assert backlinks[0]["source_path"] == "Projects/MarkOS.md"


def test_invalid_or_missing_index_returns_service_unavailable(tmp_path: Path) -> None:
    status, body = _request(create_app(tmp_path / "missing.duckdb"), "/health")

    assert status == 503
    assert "does not exist" in body["detail"]


def test_api_requests_do_not_modify_index(tmp_path: Path) -> None:
    index_path = tmp_path / "index.duckdb"
    _build_api_index(index_path)
    before = index_path.read_bytes()
    app = create_app(index_path)

    _request(app, "/health")
    _request(app, "/search", "q=knowledge")
    _request(app, "/documents/Vision.md")
    _request(app, "/graph/backlinks", "path=Vision.md")

    assert index_path.read_bytes() == before


def test_api_exposes_no_mutating_http_methods(tmp_path: Path) -> None:
    app = create_app(tmp_path / "index.duckdb")

    exposed_methods = {
        method
        for route in app.routes
        for method in (getattr(route, "methods", None) or set())
    }

    assert exposed_methods <= {"GET", "HEAD"}


def test_graph_summary_and_path_routes(tmp_path: Path) -> None:
    index_path = tmp_path / "index.duckdb"
    _build_api_index(index_path)
    app = create_app(index_path)

    summary_status, summary = _request(app, "/graph/summary")
    path_status, path = _request(
        app,
        "/graph/path",
        "source=Projects%2FMarkOS.md&target=Vision.md",
    )
    missing_status, missing = _request(
        app,
        "/graph/path",
        "source=Vision.md&target=Projects%2FMarkOS.md",
    )

    assert summary_status == 200
    assert summary["document_count"] == 2
    assert summary["resolved_link_count"] == 1
    assert summary["unresolved_link_count"] == 1
    assert path_status == 200
    assert path["path"] == ["Projects/MarkOS.md", "Vision.md"]
    assert missing_status == 404
    assert "no directed path" in missing["detail"]


def test_issues_route_lists_rejected_documents(tmp_path: Path) -> None:
    index_path = tmp_path / "index.duckdb"
    rebuild_index(
        index_path,
        [KnowledgeDocument(path=PurePath("Valid.md"), title="Valid")],
        issues=[IngestionIssue(path="Broken.md", error="invalid frontmatter")],
    )

    status, issues = _request(create_app(index_path), "/issues")

    assert status == 200
    assert issues == [{"path": "Broken.md", "error": "invalid frontmatter"}]
