"""Closeout regressions: shared admission, bounded sends and absolute socket lifetime."""

import asyncio
import http.client
import socket
import threading
import time
from pathlib import Path
from typing import Any
from uuid import UUID

import psycopg
import pytest

from markos_runtime.api.application import create_app
from markos_runtime.api.identity import FixtureIssuer, challenge, opaque
from markos_runtime.control.api_config import SyntheticApiConfig
from markos_runtime.control.store import ControlDenied, ControlStore
from markos_runtime.control_lifecycle.issuer import DurableFixtureIssuer, private_json, save_private
from markos_runtime.http_bounds import bounded_connection


def test_shared_short_request_budget_and_expired_process_leases(control: dict[str, Any]) -> None:
    async def scenario() -> None:
        async with (
            ControlStore.connect(control["gateway"]) as one,
            ControlStore.connect(control["gateway"]) as two,
        ):
            leases = [await (one if i % 2 else two)._call("claim_http_request") for i in range(16)]
            assert all(leases) and len(set(leases)) == 16
            assert await two._call("claim_http_request") is None
            await one._call("release_http_request", UUID(str(leases[0])))
            assert await two._call("claim_http_request") is not None
            with psycopg.connect(control["migration"], autocommit=True) as conn:
                conn.execute(
                    "UPDATE markos_control.http_requests "
                    "SET expires_at=clock_timestamp()-interval '1 second'"
                )
            assert await one._call("claim_http_request") is not None
            with psycopg.connect(control["migration"]) as conn:
                assert conn.execute(
                    "SELECT count(*) FROM markos_control.http_requests"
                ).fetchone() == (1,)
        with pytest.raises(ControlDenied):
            async with ControlStore.connect(control["unmapped"]) as bad:
                await bad._call("claim_http_request")

    asyncio.run(scenario())


def test_stalled_response_send_releases_local_admission() -> None:
    from types import SimpleNamespace

    async def scenario() -> None:
        app = create_app(
            SyntheticApiConfig(schema_version=1, api_origin="https://127.0.0.1:18443"),
            SimpleNamespace(),  # type: ignore[arg-type]
            FixtureIssuer(),
            session_key=b"s" * 32,
            confirm_synthetic=True,
        )

        async def receive() -> dict[str, Any]:
            return {"type": "http.request", "body": b"", "more_body": False}

        async def send(message: dict[str, Any]) -> None:
            await asyncio.sleep(60)

        started = time.monotonic()
        with pytest.raises(TimeoutError):
            async with asyncio.timeout(4):
                await app(
                    {
                        "type": "http",
                        "method": "GET",
                        "scheme": "https",
                        "path": "/live",
                        "query_string": b"",
                        "headers": [(b"host", b"127.0.0.1:18443")],
                    },
                    receive,
                    send,
                )
        assert time.monotonic() - started < 3
        assert app.active == 0

    asyncio.run(scenario())


@pytest.mark.parametrize("phase", ["headers", "body", "line"])
def test_absolute_deadline_closes_drip_feed_reader_thread(phase: str) -> None:
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    finished = threading.Event()

    def serve() -> None:
        with listener:
            peer, _ = listener.accept()
            with peer:
                peer.settimeout(2)
                peer.recv(8192)
                try:
                    if phase != "headers":
                        peer.sendall(
                            b"HTTP/1.1 200 OK\r\nConnection: close\r\nContent-Length: 8192\r\n\r\n"
                        )
                    for _ in range(40):
                        peer.sendall(b"x")
                        time.sleep(0.04)
                except OSError:
                    pass

    server = threading.Thread(target=serve, daemon=True)
    server.start()
    # HTTPConnection deliberately exercises the same stdlib socket machinery without TLS setup.
    connection = http.client.HTTPConnection("127.0.0.1", listener.getsockname()[1], timeout=1)

    def read() -> None:
        try:
            with bounded_connection(connection, time.monotonic() + 0.3):  # type: ignore[arg-type]
                connection.request("GET", "/")
                response = connection.getresponse()
                if phase == "line":
                    response.readline(8193)
                else:
                    response.read(8193)
        except (OSError, http.client.HTTPException):
            pass
        finally:
            finished.set()

    worker = threading.Thread(target=read, daemon=True)
    worker.start()
    assert finished.wait(1), "deadline must terminate actual blocking reader, not only its awaiter"
    worker.join(0.1)
    assert not worker.is_alive()
    server.join(2)
    assert not server.is_alive()


def test_issuer_restart_preserves_provider_session_rotation_and_revocation(tmp_path: Path) -> None:
    tmp_path.chmod(0o700)
    seed = tmp_path / "seed.json"
    state = tmp_path / "issuer.json"
    credential = opaque()
    save_private(
        seed, {"schema_version": 1, "accounts": [{"subject": "both", "credential": credential}]}
    )
    first = DurableFixtureIssuer(state, seed)
    verifier = opaque()
    code = first.authorize(credential, challenge(verifier), opaque())
    first.commit()
    second = DurableFixtureIssuer(state, seed)
    token, refresh = asyncio.run(second.exchange(code, verifier))
    second.commit()
    session_id = next(iter(second._sessions))
    key = second.kid
    third = DurableFixtureIssuer(state, seed)
    assert third.kid == key and asyncio.run(third.session_active("both", session_id))
    assert asyncio.run(third.refresh(refresh))[0] != token
    third.revoke(session_id)
    third.rotate_key()
    third.commit()
    fourth = DurableFixtureIssuer(state, seed)
    assert fourth.kid != key and not asyncio.run(fourth.session_active("both", session_id))
    assert private_json(state)["schema_version"] == 1
    state.chmod(0o644)
    with pytest.raises(ValueError):
        private_json(state)


def test_runtime_readiness_and_heartbeat_share_the_current_epoch(control: dict[str, Any]) -> None:
    from uuid import uuid4

    from psycopg.types.json import Jsonb

    from markos_runtime.api.schema import EXPECTED_MIGRATIONS
    from markos_runtime.store.database import DurableStore

    async def scenario() -> None:
        async with DurableStore.connect(control["publisher"], max_connections=2) as store:
            async with store._transaction() as conn:
                row = await (
                    await conn.execute(
                        "SELECT markos_control.runtime_process_probe(%s) AS epoch",
                        (Jsonb(EXPECTED_MIGRATIONS),),
                    )
                ).fetchone()
            assert row is not None
            epoch = row["epoch"]
            assert epoch == await store.current_epoch()
            await store.heartbeat(
                component="publisher", instance_id=uuid4(), epoch=epoch, status="ready"
            )

    asyncio.run(scenario())
