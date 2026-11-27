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
from markos_runtime.api.identity import (
    FixtureIssuer,
    IdentityDenied,
    IdentityUnavailable,
    SecretCodec,
    challenge,
    opaque,
)
from markos_runtime.control.api_config import SyntheticApiConfig
from markos_runtime.control.store import ControlDenied
from markos_runtime.control_lifecycle.issuer import IssuerStore, StoredIssuer, save_private
from markos_runtime.http_bounds import bounded_connection
from tests.control_store_support import HarnessStore


def test_shared_short_request_budget_and_expired_process_leases(control: dict[str, Any]) -> None:
    async def scenario() -> None:
        async with (
            HarnessStore.connect(control["gateway"]) as one,
            HarnessStore.connect(control["gateway"]) as two,
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
            async with HarnessStore.connect(control["unmapped"]) as bad:
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


def test_issuer_restart_preserves_provider_session_rotation_and_revocation(
    tmp_path: Path, control: dict[str, Any]
) -> None:
    import os

    import jwt  # type: ignore[import-not-found]

    tmp_path.chmod(0o700)
    seed = tmp_path / "seed.json"
    credential = opaque()
    save_private(
        seed, {"schema_version": 1, "accounts": [{"subject": "both", "credential": credential}]}
    )
    key = os.urandom(32)

    async def scenario() -> None:
        async def issuer(store: IssuerStore, codec_key: bytes = key) -> StoredIssuer:
            stored = StoredIssuer(store, SecretCodec(codec_key), seed)
            await stored.start()
            return stored

        # Each block is a fresh process: nothing survives except PostgreSQL rows.
        async with IssuerStore.connect(control["issuer"]) as store:
            first = await issuer(store)
            verifier = opaque()
            code = await first.authorize(credential, challenge(verifier), opaque())
        async with IssuerStore.connect(control["issuer"]) as store:
            second = await issuer(store)
            token, refresh = await second.exchange(code, verifier)
            with pytest.raises(IdentityDenied):
                await second.exchange(code, verifier)
            kid = jwt.get_unverified_header(token)["kid"]
            session_id = UUID(jwt.decode(token, options={"verify_signature": False})["sid"])
        async with IssuerStore.connect(control["issuer"]) as store:
            third = await issuer(store)
            assert kid.encode() in await third.keys()
            assert await third.session_active("both", session_id)
            assert (await third.refresh(refresh))[0] != token
            with pytest.raises(IdentityDenied):
                await third.refresh(refresh)
            await third.revoke(session_id)
            await third.rotate_key()
        async with IssuerStore.connect(control["issuer"]) as store:
            fourth = await issuer(store)
            assert kid.encode() not in await fourth.keys()
            assert not await fourth.session_active("both", session_id)
            # A different state key cannot open the stored signing key.
            with pytest.raises(IdentityUnavailable):
                await (await issuer(store, os.urandom(32))).keys()
        # The runtime login cannot reach issuer state.
        with psycopg.connect(control["publisher"]) as conn, pytest.raises(psycopg.Error):
            conn.execute("SELECT markos_issuer.issuer_key()")

    asyncio.run(scenario())


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
