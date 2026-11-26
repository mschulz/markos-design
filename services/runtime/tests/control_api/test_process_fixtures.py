"""Real TLS subprocess fixtures keep synthetic identity and effect state across restarts."""

import asyncio
import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, cast
from urllib.request import urlopen
from uuid import UUID, uuid4

from markos_runtime.api.identity import challenge, opaque
from markos_runtime.control_lifecycle.config import ControlServiceConfig
from markos_runtime.control_lifecycle.issuer import RemoteFixtureIssuer, save_private
from markos_runtime.tools.transport import FixtureConnection, FixtureTransport
from tests.control_api.test_https import certificate


def port() -> int:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def test_persisted_issuer_and_mcp_launch_from_independent_package(tmp_path: Path) -> None:
    tmp_path.chmod(0o700)
    cert, key = certificate(tmp_path)
    issuer_port, tool_port = port(), port()
    api, worker, admin, tool, control = (opaque() for _ in range(5))
    credential = opaque()
    owner = UUID("11111111-1111-4111-8111-111111111111")
    env = {
        **os.environ,
        "MARKOS_CONTROL_ISSUER_API_TOKEN": api,
        "MARKOS_CONTROL_ISSUER_WORKER_TOKEN": worker,
        "MARKOS_CONTROL_ISSUER_ADMIN_TOKEN": admin,
        "MARKOS_CONTROL_TOOL_TOKEN": tool,
        "MARKOS_CONTROL_FIXTURE_ADMIN_TOKEN": control,
    }
    files = []
    health_ports = []
    for component, service in [("issuer", "control-gateway"), ("mcp", "control-test-agent")]:
        folder = tmp_path / component
        folder.mkdir(mode=0o700)
        save_private(
            folder / "seed.json",
            {"schema_version": 1, "accounts": [{"subject": "both", "credential": credential}]},
        )
        health_port = port()
        health_ports.append(health_port)
        profile = ControlServiceConfig(
            schema_version=1,
            profile="control-process-fixture-v1",
            component=cast(Any, component),
            service_id=cast(Any, service),
            owner_id=owner,
            domain="personal",
            health_port=health_port,
            issuer_endpoint=f"https://127.0.0.1:{issuer_port}/fixture",
            tool_endpoint=f"https://127.0.0.1:{tool_port}/mcp",
            ca_file=str(cert),
            certificate_file=str(cert),
            private_key_file=str(key),
            fixture_state_file=str(folder / "state.json"),
            fixture_seed_file=str(folder / "seed.json"),
            broker_publisher_user="synthetic-publisher",
        )  # type: ignore[arg-type]
        config = folder / "config.json"
        config.write_bytes(profile.to_wire())
        files.append(config)

    def start(config: Path) -> Any:
        return subprocess.Popen(
            [
                sys.executable,
                "-I",
                "-m",
                "markos_runtime",
                "serve-control-synthetic",
                "--confirm-synthetic",
                "--config",
                str(config),
            ],
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            cwd=tmp_path,
        )

    processes = [start(file) for file in files]
    remote = RemoteFixtureIssuer(f"https://127.0.0.1:{issuer_port}/fixture", cert, api)

    def healthy(port: int) -> bool:
        try:
            with urlopen(f"http://127.0.0.1:{port}/ready", timeout=0.5) as response:
                return bool(json.loads(response.read(4096)).get("ready"))
        except (OSError, ValueError):
            return False

    async def ready() -> None:
        async with asyncio.timeout(15):
            while True:
                if any(process.poll() is not None for process in processes):
                    raise AssertionError("synthetic fixture exited before readiness")
                checks = await asyncio.gather(
                    *(asyncio.to_thread(healthy, port) for port in health_ports)
                )
                if all(checks):
                    return
                await asyncio.sleep(0.1)

    async def scenario() -> None:
        await ready()
        verifier = opaque()
        code = await remote.authorize_async(credential, challenge(verifier), opaque())
        token, refresh = await remote.exchange(code, verifier)
        assert token and refresh
        transport = FixtureTransport(
            FixtureConnection(f"https://127.0.0.1:{tool_port}/mcp", owner, "personal", cert, tool),
            confirm_synthetic=True,
        )
        await transport.inspect(time.monotonic() + 5)
        operation = uuid4()
        result = await transport.call("Synthetic process read", operation, time.monotonic() + 5)
        assert result.label == "Synthetic process read"
        for process in processes:
            process.terminate()
        for process in processes:
            assert await asyncio.to_thread(process.wait, timeout=15) == 0
        processes[:] = [start(file) for file in files]
        await ready()
        assert await remote.refresh(refresh)
        assert (
            json.loads((tmp_path / "mcp/state.json").read_text())["operations"][str(operation)][
                "effects"
            ]
            == 1
        )

    try:
        asyncio.run(scenario())
    finally:
        for process in processes:
            if process.poll() is None:
                process.terminate()
        for process in processes:
            try:
                process.wait(timeout=15)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=2)
