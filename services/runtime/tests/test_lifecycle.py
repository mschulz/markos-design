import asyncio
import json
from pathlib import Path
from typing import Any, cast

import pytest
from pydantic import ValidationError

from markos_runtime.lifecycle.config import ServiceConfig, load_service
from markos_runtime.lifecycle.health import HealthServer, HealthState


def service_document() -> dict[str, Any]:
    return cast(
        dict[str, Any],
        json.loads(
            (
                Path(__file__).resolve().parents[3] / "config/runtime/lifecycle/worker.json"
            ).read_text()
        ),
    )


@pytest.mark.parametrize(
    "change",
    [
        {"environment": "production"},
        {"schema_version": True},
        {"component": "agent"},
        {"health_port": 80},
        {"tick_seconds": 0},
        {"shutdown_seconds": 100},
        {"synthetic_work_seconds": 11},
        {"cleanup_enabled": "true"},
        {"audit_url": "http://remote.example"},
        {"audit_url": "https://u:secret@rabbitmq"},
        {"audit_url": "https://rabbitmq?secret=x"},
        {"role": "live"},
        {"database_secret_ref": "HOME"},
        {"publisher_users": {"x": "private/password"}},
    ],
)
def test_service_config_cannot_enable_production_or_unbounded_work(change: dict[str, Any]) -> None:
    doc = service_document()
    doc.update(change)
    with pytest.raises(ValidationError):
        ServiceConfig.model_validate_json(json.dumps(doc))


def test_all_synthetic_service_examples_match_manifest() -> None:
    directory = Path(__file__).resolve().parents[3] / "config/runtime/lifecycle"
    for path in directory.glob("*.json"):
        if path.name != "subscriptions.json":
            load_service(path)


def test_private_health_readiness_liveness_draining_and_no_input_echo() -> None:
    async def scenario() -> None:
        state = HealthState()
        server = HealthServer(state, 0)
        await server.start()
        assert server.server and server.server.sockets
        port = server.server.sockets[0].getsockname()[1]

        async def get(path: bytes) -> bytes:
            reader, writer = await asyncio.open_connection("127.0.0.1", port)
            writer.write(b"GET " + path + b" HTTP/1.1\r\n\r\n")
            await writer.drain()
            data = await reader.read()
            writer.close()
            await writer.wait_closed()
            return data

        try:
            assert b"503" in (await get(b"/ready")).splitlines()[0]
            assert b"200" in (await get(b"/live")).splitlines()[0]
            state.ready = True
            assert b"200" in (await get(b"/ready")).splitlines()[0]
            state.draining = True
            assert b"503" in (await get(b"/ready")).splitlines()[0]
            response = await get(b"/PRIVATE_PASSWORD")
            assert b"PRIVATE_PASSWORD" not in response and b"404" in response.splitlines()[0]
            state.last_progress -= 50
            assert b"503" in (await get(b"/live")).splitlines()[0]
        finally:
            await server.close()

    asyncio.run(scenario())
