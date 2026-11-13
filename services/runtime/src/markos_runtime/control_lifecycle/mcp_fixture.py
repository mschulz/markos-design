"""Independent persisted synthetic SDK server; controller faults require a separate credential."""

import asyncio
import hmac
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit
from uuid import UUID

from markos_runtime.api.identity import strict_json
from markos_runtime.contracts.governance import INPUT_SCHEMA
from markos_runtime.control_lifecycle.issuer import private_json, save_private


def mcp_app(
    state_file: Path, owner: UUID, domain: str, endpoint: str, credential: str, admin_token: str
) -> Any:
    from mcp import types  # type: ignore[import-not-found]
    from mcp.server.lowlevel import Server  # type: ignore[import-not-found]
    from starlette.requests import Request  # type: ignore[import-not-found]
    from starlette.responses import JSONResponse  # type: ignore[import-not-found]

    state: dict[str, Any] = (
        private_json(state_file) if state_file.exists() else {"schema_version": 1, "operations": {}}
    )
    if (
        set(state) != {"schema_version", "operations"}
        or state["schema_version"] != 1
        or len(state["operations"]) > 1000
    ):
        raise ValueError("fixed synthetic ledger required")
    lock = asyncio.Lock()
    gate = asyncio.Event()
    gate.set()
    failed = False

    async def listing(context: Any, params: Any) -> Any:
        return types.ListToolsResult(
            tools=[
                types.Tool(
                    name="fixture.read",
                    input_schema=INPUT_SCHEMA,
                    description="Synthetic scoped read",
                )
            ]
        )

    async def call(context: Any, params: Any) -> Any:
        nonlocal failed
        if failed or params.name != "fixture.read" or set(params.arguments or {}) != {"label"}:
            raise ValueError("fixed synthetic request required")
        label = params.arguments["label"]
        operation = str(context.request_id)
        if not isinstance(label, str) or not 1 <= len(label) <= 256:
            raise ValueError("bounded synthetic label")
        async with lock:
            operations = state["operations"]
            if operation not in operations:
                if len(operations) >= 1000:
                    raise ValueError("synthetic ledger full")
                operations[operation] = {"label": label, "requests": 0, "effects": 1}
            if operations[operation]["label"] != label:
                raise ValueError("immutable operation required")
            operations[operation]["requests"] += 1
            try:
                save_private(state_file, state)
            except Exception:
                failed = True
                raise
        await gate.wait()
        return types.CallToolResult(
            content=[],
            structured_content={
                "schema_version": 1,
                "owner_id": str(owner),
                "domain": domain,
                "kind": "synthetic",
                "label": label,
            },
        )

    sdk: Any = Server(
        "synthetic-mcp-fixture",
        version="1",
        on_list_tools=listing,
        on_call_tool=call,
        get_tool_input_schema=lambda name: INPUT_SCHEMA,
    )
    app = sdk.streamable_http_app(
        json_response=True, stateless_http=True, max_request_body_size=65536
    )

    async def guarded(scope: Any, receive: Any, send: Any) -> None:
        if scope["type"] != "http":
            await app(scope, receive, send)
            return
        headers = dict(scope["headers"])
        auth = headers.get(b"authorization", b"")
        common = (
            scope.get("scheme") == "https"
            and headers.get(b"host") == urlsplit(endpoint).netloc.encode()
            and not headers.get(b"origin")
            and scope["method"] == "POST"
            and headers.get(b"x-markos-resource") == endpoint.encode()
        )
        if (
            common
            and scope["path"] == "/fixture-control"
            and hmac.compare_digest(auth, ("Bearer " + admin_token).encode())
        ):
            try:
                async with asyncio.timeout(3):
                    request = Request(scope, receive)
                    body = bytearray()
                    async for chunk in request.stream():
                        if len(body) + len(chunk) > 1024:
                            raise ValueError
                        body.extend(chunk)
                    value = strict_json(bytes(body), 1024)
                    if value == {"operation": "ledger"}:
                        result = state
                    elif value == {"operation": "health"}:
                        result = {"ready": not failed}
                    elif (
                        set(value) == {"operation", "enabled"}
                        and value["operation"] == "hold"
                        and type(value["enabled"]) is bool
                    ):
                        if value["enabled"]:
                            gate.clear()
                        else:
                            gate.set()
                        result = {"held": not gate.is_set()}
                    else:
                        raise ValueError
                    await JSONResponse(result, headers={"Cache-Control": "no-store"})(
                        scope, receive, send
                    )
            except Exception:
                await JSONResponse({"error": "unavailable"}, status_code=503)(scope, receive, send)
            return
        if (
            not common
            or scope["path"] != "/mcp"
            or failed
            or headers.get(b"x-markos-owner") != str(owner).encode()
            or headers.get(b"x-markos-domain") != domain.encode()
            or not hmac.compare_digest(auth, ("Bearer " + credential).encode())
        ):
            await JSONResponse({"error": "denied"}, status_code=401)(scope, receive, send)
            return
        await app(scope, receive, send)

    return guarded
