"""Bounded loopback-only read-only health; no URLs, credentials, payloads or owner IDs."""

import asyncio
import json
import time
from dataclasses import dataclass, field
from uuid import uuid4


@dataclass
class HealthState:
    ready: bool = False
    draining: bool = False
    passes: int = 0
    faults: int = 0
    reconnects: int = 0
    last_progress: float = field(default_factory=time.monotonic)
    instance: str = field(default_factory=lambda: str(uuid4()))

    @property
    def live(self) -> bool:
        return time.monotonic() - self.last_progress < 45

    def touch(self) -> None:
        self.last_progress = time.monotonic()

    def snapshot(self) -> dict[str, object]:
        return {
            "live": self.live,
            "ready": self.ready and not self.draining and self.live,
            "draining": self.draining,
            "passes": self.passes,
            "faults": self.faults,
            "reconnects": self.reconnects,
            "instance": self.instance,
        }


class HealthServer:
    def __init__(self, state: HealthState, port: int) -> None:
        self.state, self.port = state, port
        self.server: asyncio.Server | None = None
        self._clients: set[asyncio.Task[None]] = set()

    async def start(self) -> None:
        self.server = await asyncio.start_server(self._handle, "127.0.0.1", self.port, limit=1024)

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        task = asyncio.current_task()
        assert task is not None
        if len(self._clients) >= 16:
            writer.close()
            return
        self._clients.add(task)
        try:
            async with asyncio.timeout(1):
                line = await reader.readline()
                path = line.split(b" ")
                snap = self.state.snapshot()
                status = 200
                if (
                    len(path) != 3
                    or path[0] != b"GET"
                    or path[1] not in {b"/live", b"/ready", b"/metrics"}
                ):
                    status = 404
                elif (path[1] == b"/live" and not snap["live"]) or (
                    path[1] == b"/ready" and not snap["ready"]
                ):
                    status = 503
                body = json.dumps(
                    snap if status != 404 else {"error": "not_found"}, separators=(",", ":")
                ).encode()
                writer.write(
                    (
                        f"HTTP/1.1 {status} status\r\nContent-Type: application/json\r\n"
                        f"Content-Length: {len(body)}\r\nConnection: close\r\n\r\n"
                    ).encode()
                    + body
                )
                await writer.drain()
        except (ValueError, OSError, TimeoutError):
            pass
        finally:
            self._clients.discard(task)
            writer.close()

    async def close(self) -> None:
        if self.server is not None:
            self.server.close()
            await self.server.wait_closed()
        tasks = tuple(self._clients)
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.wait(tasks, timeout=1)
