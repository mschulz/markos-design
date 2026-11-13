"""One-attempt closed MCP wire path; no SDK auto-retry, OAuth, discovery URLs or nested calls."""

import asyncio
import http.client
import json
import re
import secrets
import ssl
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit
from uuid import UUID

from markos_runtime.contracts.control import digest_document
from markos_runtime.contracts.governance import (
    INPUT_SCHEMA,
    PROTOCOL,
    FixtureResult,
    fixture_endpoint,
)


class ToolProtocolError(ValueError):
    def __init__(self) -> None:
        super().__init__("synthetic tool unavailable")


def strict_json(body: bytes) -> dict[str, Any]:
    def pairs(values: list[tuple[str, Any]]) -> dict[str, Any]:
        answer: dict[str, Any] = {}
        for key, value in values:
            if key in answer:
                raise ValueError
            answer[key] = value
        return answer

    def invalid(value: str) -> Any:
        raise ValueError

    try:
        if len(body) > 1048576:
            raise ValueError
        answer = json.loads(body, object_pairs_hook=pairs, parse_constant=invalid)
        if not isinstance(answer, dict):
            raise ValueError
        # This fixed profile has no float-valued fields; reject overflow/nonfinite recursively.
        json.dumps(answer, allow_nan=False)
        return dict(answer)
    except (ValueError, UnicodeError, RecursionError):
        raise ToolProtocolError() from None


@dataclass(frozen=True)
class FixtureConnection:
    endpoint: str
    owner_id: UUID
    domain: str
    ca_file: Path = field(repr=False)
    credential: str = field(repr=False)

    def __post_init__(self) -> None:
        fixture_endpoint(self.endpoint)
        if self.domain not in {"personal", "research", "work"} or not re.fullmatch(
            r"[A-Za-z0-9_-]{43}", self.credential
        ):
            raise ToolProtocolError()
        ssl.create_default_context(cafile=str(self.ca_file))


class FixtureTransport:
    def __init__(self, config: FixtureConnection, *, confirm_synthetic: bool = False) -> None:
        if confirm_synthetic is not True or not isinstance(config, FixtureConnection):
            raise ToolProtocolError()
        self.config = config

    def _request(
        self, method: str, params: dict[str, Any], deadline: float, operation_id: UUID | None = None
    ) -> dict[str, Any]:
        if method not in {"server/discover", "tools/list", "tools/call"}:
            raise ToolProtocolError()
        from mcp import types  # type: ignore[import-not-found]

        # SDK model validation is used without high-level automatic negotiation/retry drivers.
        model: Any = {
            "server/discover": types.DiscoverRequest,
            "tools/list": types.ListToolsRequest,
            "tools/call": types.CallToolRequest,
        }[method]
        ident = str(operation_id) if operation_id else secrets.token_urlsafe(16)
        meta = {
            "io.modelcontextprotocol/protocolVersion": PROTOCOL,
            "io.modelcontextprotocol/clientInfo": {"name": "markos-synthetic", "version": "1"},
            "io.modelcontextprotocol/clientCapabilities": {},
        }
        request = model.model_validate({"method": method, "params": {**params, "_meta": meta}})
        document = {
            "jsonrpc": "2.0",
            "id": ident,
            **request.model_dump(mode="json", by_alias=True, exclude_none=True),
        }
        body = json.dumps(document, ensure_ascii=True, allow_nan=False).encode()
        if len(body) > 65536 or deadline <= time.monotonic():
            raise ToolProtocolError()
        parsed = urlsplit(self.config.endpoint)
        context = ssl.create_default_context(cafile=str(self.config.ca_file))
        connection = http.client.HTTPSConnection(
            "127.0.0.1", parsed.port, context=context, timeout=min(5, deadline - time.monotonic())
        )
        headers = {
            "Authorization": "Bearer " + self.config.credential,
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            "Accept-Encoding": "identity",
            "MCP-Protocol-Version": PROTOCOL,
            "Mcp-Method": method,
            "X-Markos-Owner": str(self.config.owner_id),
            "X-Markos-Domain": self.config.domain,
            "X-Markos-Resource": self.config.endpoint,
        }
        if method == "tools/call":
            if params.get("name") != "fixture.read":
                raise ToolProtocolError()
            headers["Mcp-Name"] = "fixture.read"
        try:
            connection.request("POST", "/mcp", body=body, headers=headers)
            if connection.sock:
                connection.sock.settimeout(max(0.001, min(10, deadline - time.monotonic())))
            response = connection.getresponse()
            if (
                response.status != 200
                or response.getheader("Content-Encoding", "identity") != "identity"
            ):
                raise ToolProtocolError()
            kind = response.getheader("Content-Type", "").split(";")[0]
            if kind not in {"application/json", "text/event-stream"}:
                raise ToolProtocolError()
            size, buffer = 0, bytearray()
            final: dict[str, Any] | None = None
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise ToolProtocolError()
                if connection.sock:
                    connection.sock.settimeout(min(10, remaining))
                chunk = (
                    response.readline(8193) if kind == "text/event-stream" else response.read(8192)
                )
                if not chunk:
                    break
                size += len(chunk)
                if size > 1048576:
                    raise ToolProtocolError()
                buffer.extend(chunk)
                if kind == "text/event-stream":
                    if len(buffer) > 65536:
                        raise ToolProtocolError()
                    if chunk not in {b"\n", b"\r\n"}:
                        continue
                    lines = bytes(buffer).replace(b"\r\n", b"\n").splitlines()
                    data = b"\n".join(
                        line[5:].lstrip() for line in lines if line.startswith(b"data:")
                    )
                    buffer.clear()
                    if not data:
                        continue
                    value = strict_json(data)
                    if value.get("method") == "notifications/progress" and "id" not in value:
                        continue
                    if final is not None:
                        raise ToolProtocolError()
                    final = value
                    # The request is complete; never follow a subscription or server-requested URL.
                    break
            if kind == "application/json":
                final = strict_json(bytes(buffer))
            if (
                not final
                or set(final) != {"jsonrpc", "id", "result"}
                or final["jsonrpc"] != "2.0"
                or final["id"] != ident
                or not isinstance(final["result"], dict)
            ):
                raise ToolProtocolError()
            result = dict(final["result"])
            expected_meta = {
                "io.modelcontextprotocol/serverInfo": {
                    "name": "synthetic-mcp-fixture",
                    "version": "1",
                }
            }
            allowed_keys = {
                "server/discover": {
                    "_meta",
                    "ttlMs",
                    "cacheScope",
                    "supportedVersions",
                    "capabilities",
                    "instructions",
                    "resultType",
                },
                "tools/list": {"_meta", "ttlMs", "cacheScope", "nextCursor", "tools", "resultType"},
                "tools/call": {"_meta", "content", "structuredContent", "isError", "resultType"},
            }[method]
            if (
                result.get("_meta") != expected_meta
                or not set(result) <= allowed_keys
                or result.get("cacheScope", "private") != "private"
            ):
                raise ToolProtocolError()
            if result.get("resultType", "complete") != "complete":
                raise ToolProtocolError()
            return result
        except (OSError, ValueError, http.client.HTTPException):
            raise ToolProtocolError() from None
        finally:
            connection.close()

    async def inspect(self, deadline: float) -> None:
        from mcp import types  # type: ignore[import-not-found]

        try:
            raw = await asyncio.to_thread(self._request, "server/discover", {}, deadline)
            discovered = types.DiscoverResult.model_validate(raw)
            caps = discovered.capabilities.model_dump(mode="json", by_alias=True, exclude_none=True)
            if (
                raw.get("capabilities") not in ({"tools": {}}, {"tools": {"listChanged": False}})
                or discovered.supported_versions != [PROTOCOL]
                or caps not in ({"tools": {}}, {"tools": {"listChanged": False}})
                or discovered.instructions
            ):
                raise ToolProtocolError()
            raw = await asyncio.to_thread(self._request, "tools/list", {}, deadline)
            listing = types.ListToolsResult.model_validate(raw)
            if listing.next_cursor or len(listing.tools) != 1:
                raise ToolProtocolError()
            tool = listing.tools[0]
            if (
                tool.name != "fixture.read"
                or tool.input_schema != INPUT_SCHEMA
                or tool.execution
                or tool.meta
                or tool.icons
                or tool.output_schema
            ):
                raise ToolProtocolError()
            if len(tool.description or "") > 512:
                raise ToolProtocolError()
        except Exception:
            raise ToolProtocolError() from None

    async def call(self, label: str, operation_id: UUID, deadline: float) -> FixtureResult:
        from mcp import types  # type: ignore[import-not-found]

        try:
            raw = await asyncio.to_thread(
                self._request,
                "tools/call",
                {"name": "fixture.read", "arguments": {"label": label}},
                deadline,
                operation_id,
            )
            result = types.CallToolResult.model_validate(raw)
            if result.is_error or result.content:
                raise ToolProtocolError()
            encoded = json.dumps(result.structured_content, allow_nan=False).encode()
            value = FixtureResult.from_wire(encoded)
            if (
                value.owner_id != self.config.owner_id
                or value.domain != self.config.domain
                or value.label != label
            ):
                raise ToolProtocolError()
            return value
        except Exception:
            raise ToolProtocolError() from None

    @staticmethod
    def result_hash(value: FixtureResult) -> str:
        return digest_document(value.model_dump(mode="json"))
