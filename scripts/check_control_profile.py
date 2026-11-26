"""Optional dependency compatibility probe only. No sockets, sessions or MarkOS API."""

import asyncio
import importlib.util
import json
from datetime import UTC, datetime, timedelta
from typing import Any

import jwt
import uvicorn
from cryptography.hazmat.primitives.asymmetric import ec
from fastapi import FastAPI, HTTPException, Request
from jinja2 import Environment, StrictUndefined, select_autoescape

from markos_runtime.contracts.control import HTTP_BYTES, ChatRequest


async def asgi_probe() -> None:
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)

    @app.post("/fixture")
    async def fixture(request: Request) -> dict[str, str]:
        # Preserve strict JSON-mode UUID/time parsing; do not coerce a parsed Python dict.
        body = bytearray()
        async for chunk in request.stream():
            if len(body) + len(chunk) > HTTP_BYTES:
                raise HTTPException(status_code=413, detail="bounded fixture input required")
            body.extend(chunk)
        return {"text": ChatRequest.from_wire(bytes(body)).text}

    body = json.dumps(
        {
            "schema_version": 1,
            "domain": "personal",
            "conversation_id": "11111111-1111-4111-8111-111111111111",
            "operation_key": "compatibility:1",
            "agent_id": "control-test-agent",
            "expected_agent_revision": 1,
            "text": "Synthetic compatibility only",
        }
    ).encode()
    sent: list[dict[str, Any]] = []

    async def receive() -> dict[str, Any]:
        return {"type": "http.request", "body": body, "more_body": False}

    async def send(message: dict[str, Any]) -> None:
        sent.append(message)

    await app(
        {
            "type": "http",
            "asgi": {"version": "3.0"},
            "http_version": "1.1",
            "method": "POST",
            "scheme": "https",
            "path": "/fixture",
            "raw_path": b"/fixture",
            "query_string": b"",
            "root_path": "",
            "headers": [(b"content-type", b"application/json")],
            "client": ("127.0.0.1", 1),
            "server": ("fixture", 443),
        },
        receive,
        send,
    )
    assert sent[0]["status"] == 200
    assert json.loads(sent[1]["body"])["text"] == "Synthetic compatibility only"


def main() -> None:
    asyncio.run(asgi_probe())
    private = ec.generate_private_key(ec.SECP256R1())
    claims = {
        "sub": "fixture",
        "iss": "fixture",
        "aud": "control-test",
        "exp": datetime.now(UTC) + timedelta(minutes=1),
    }
    token = jwt.encode(claims, private, algorithm="ES256")
    assert (
        jwt.decode(
            token,
            private.public_key(),
            algorithms=["ES256"],
            issuer="fixture",
            audience="control-test",
        )["sub"]
        == "fixture"
    )
    try:
        jwt.decode(
            token, private.public_key(), algorithms=["ES256"], issuer="fixture", audience="wrong"
        )
    except jwt.InvalidTokenError:
        pass
    else:
        raise AssertionError("wrong audience accepted")
    env = Environment(autoescape=select_autoescape(default=True), undefined=StrictUndefined)
    assert env.from_string("{{ text }}").render(text="<script>") == "&lt;script&gt;"
    # Construct server config only. Never call Server.run/serve or bind a port.
    assert uvicorn.Config("fixture:app", workers=1, reload=False).workers == 1
    for package in ("markos", "duckdb", "fastapi_cli", "fastapi_cloud_cli"):
        assert importlib.util.find_spec(package) is None
    print("Optional control profile compatible; no MarkOS authentication/server implemented")


if __name__ == "__main__":
    main()
