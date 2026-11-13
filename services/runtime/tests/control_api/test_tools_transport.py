"""Actual SDK interoperability over authenticated local HTTPS, both JSON and streamed responses."""

import asyncio
import importlib.util
import os
import time
from pathlib import Path
from uuid import UUID, uuid4

import pytest

from markos_runtime.tools.transport import FixtureConnection, FixtureTransport, ToolProtocolError
from tests.integration.conftest import OWNER

if importlib.util.find_spec("mcp") is None:
    if os.environ.get("MARKOS_REQUIRE_TOOLS_TESTS") == "1":
        pytest.fail("required pinned MCP profile missing")
    pytest.skip("optional MCP profile not installed", allow_module_level=True)

from tests.tools_support import tool_server


def connection(server: dict[str, object]) -> FixtureTransport:
    return FixtureTransport(
        FixtureConnection(
            endpoint=str(server["endpoint"]),
            owner_id=UUID(OWNER),
            domain="personal",
            ca_file=Path(str(server["cert"])),
            credential=str(server["credential"]),
        ),
        confirm_synthetic=True,
    )


@pytest.mark.parametrize("sse", [False, True])
def test_official_sdk_fixture_interoperability(tmp_path: Path, sse: bool) -> None:
    async def scenario() -> None:
        async with tool_server(tmp_path, UUID(OWNER), "personal", sse=sse) as server:
            transport = connection(server)
            await transport.inspect(time.monotonic() + 5)
            value = await transport.call(
                "Inert <script>instructions</script>", uuid4(), time.monotonic() + 5
            )
            assert value.label == "Inert <script>instructions</script>"
            assert server["state"]["methods"] == ["server/discover", "tools/list", "tools/call"]
            assert sum(server["state"]["ledger"].values()) == 1

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "fault",
    ["schema", "version", "redirect", "compressed", "oversize", "nested", "capability", "identity"],
)
def test_discovery_and_protocol_faults_never_call_tool(tmp_path: Path, fault: str) -> None:
    async def scenario() -> None:
        async with tool_server(tmp_path, UUID(OWNER), "personal") as server:
            server["state"]["fault"] = fault
            transport = connection(server)
            with pytest.raises(ToolProtocolError):
                await transport.inspect(time.monotonic() + 3)
            assert server["state"]["operations"] == []
            assert "tools/call" not in server["state"]["methods"]

    asyncio.run(scenario())


@pytest.mark.parametrize("fault", ["credential", "owner", "domain"])
def test_fixture_authentication_binds_resource_and_scope_before_data(
    tmp_path: Path, fault: str
) -> None:
    async def scenario() -> None:
        async with tool_server(tmp_path, UUID(OWNER), "personal") as server:
            transport = FixtureTransport(
                FixtureConnection(
                    endpoint=server["endpoint"],
                    owner_id=uuid4() if fault == "owner" else UUID(OWNER),
                    domain="research" if fault == "domain" else "personal",
                    ca_file=server["cert"],
                    credential="x" * 43 if fault == "credential" else server["credential"],
                ),
                confirm_synthetic=True,
            )
            with pytest.raises(ToolProtocolError):
                await transport.inspect(time.monotonic() + 3)
            assert server["state"]["operations"] == []

    asyncio.run(scenario())


def test_untrusted_description_cannot_add_calls_or_permissions(tmp_path: Path) -> None:
    async def scenario() -> None:
        async with tool_server(tmp_path, UUID(OWNER), "personal") as server:
            server["state"]["fault"] = "injection"
            transport = connection(server)
            await transport.inspect(time.monotonic() + 3)
            result = await transport.call("Synthetic inert input", uuid4(), time.monotonic() + 3)
            assert result.label == "Synthetic inert input"
            assert server["state"]["methods"] == ["server/discover", "tools/list", "tools/call"]

    asyncio.run(scenario())


@pytest.mark.parametrize("sse", [False, True])
def test_absolute_deadline_bounds_slow_sdk_response(tmp_path: Path, sse: bool) -> None:
    async def scenario() -> None:
        async with tool_server(tmp_path, UUID(OWNER), "personal", sse=sse) as server:
            server["state"]["fault"] = "slow"
            started = time.monotonic()
            with pytest.raises(ToolProtocolError):
                await connection(server).call("Synthetic bounded wait", uuid4(), started + 0.15)
            assert time.monotonic() - started < 1
            assert server["state"]["ledger"] == {"Synthetic bounded wait": 1}

    asyncio.run(scenario())
