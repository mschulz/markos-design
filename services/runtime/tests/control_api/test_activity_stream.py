"""Shared stream admission and current-authority expiry on disposable PostgreSQL/HTTPS."""

import asyncio
import threading
import time
from contextlib import suppress
from pathlib import Path
from typing import Any
from uuid import UUID

import psycopg
import pytest

from markos_runtime.control.store import ControlDenied, ControlLimited, ControlStore
from tests.control_api.test_web_cli import cli_client
from tests.integration.conftest import OTHER, OWNER
from tests.integration.test_control_persistence import request, session
from tests.web_support import web_server


def test_stream_limits_are_database_shared_and_release_only_with_possession(
    control: dict[str, Any],
) -> None:
    async def scenario() -> None:
        async with (
            ControlStore.connect(control["gateway"]) as first,
            ControlStore.connect(control["gateway"]) as second,
        ):
            opened = []
            for name in ("viewer", "operator", "approver", "administrator", "both"):
                for index in range(4):
                    ctx = await session(first, name)
                    store = first if index % 2 else second
                    ident = await store._call(
                        "open_activity_stream",
                        ctx.session_id,
                        ctx.lookup_hash,
                        UUID(OWNER),
                        "personal",
                    )
                    opened.append((ctx, ident))
            extra = await session(second, "other")
            with pytest.raises(ControlLimited):
                await second._call(
                    "open_activity_stream",
                    extra.session_id,
                    extra.lookup_hash,
                    UUID(OTHER),
                    "personal",
                )
            ctx, ident = opened[0]
            await second._call("close_activity_stream", extra.session_id, extra.lookup_hash, ident)
            with pytest.raises(ControlLimited):
                await second._call(
                    "open_activity_stream",
                    extra.session_id,
                    extra.lookup_hash,
                    UUID(OTHER),
                    "personal",
                )
            await second._call("close_activity_stream", ctx.session_id, ctx.lookup_hash, ident)
            assert await second._call(
                "open_activity_stream", extra.session_id, extra.lookup_hash, UUID(OTHER), "personal"
            )

    asyncio.run(scenario())


@pytest.mark.parametrize("bound", ["session", "principal"])
def test_each_stream_session_and_principal_bound(control: dict[str, Any], bound: str) -> None:
    async def scenario() -> None:
        async with ControlStore.connect(control["gateway"]) as store:
            contexts = [await session(store) for _ in range(5)]
            maximum = 1 if bound == "session" else 4
            for index in range(maximum):
                ctx = contexts[index]
                await store._call(
                    "open_activity_stream", ctx.session_id, ctx.lookup_hash, UUID(OWNER), "personal"
                )
            ctx = contexts[0] if bound == "session" else contexts[4]
            with pytest.raises(ControlLimited):
                await store._call(
                    "open_activity_stream", ctx.session_id, ctx.lookup_hash, UUID(OWNER), "personal"
                )

    asyncio.run(scenario())


@pytest.mark.parametrize("cause", ["lease", "absolute", "epoch", "membership", "session"])
def test_stream_renewal_cannot_resurrect_expired_or_revoked_authority(
    control: dict[str, Any], cause: str
) -> None:
    async def scenario() -> None:
        async with ControlStore.connect(control["gateway"]) as store:
            ctx = await session(store)
            ident = await store._call(
                "open_activity_stream", ctx.session_id, ctx.lookup_hash, UUID(OWNER), "personal"
            )
            with psycopg.connect(control["migration"], autocommit=True) as conn:
                if cause == "lease":
                    conn.execute(
                        "UPDATE markos_control.activity_streams "
                        "SET lease_until=clock_timestamp()-interval '1 second'"
                    )
                elif cause == "absolute":
                    conn.execute(
                        "UPDATE markos_control.activity_streams "
                        "SET created_at=clock_timestamp()-interval '600 seconds'"
                    )
                elif cause == "epoch":
                    conn.execute("SELECT markos_runtime.fence_restored_state()")
                elif cause == "membership":
                    conn.execute(
                        "DELETE FROM markos_control.memberships WHERE principal_id=%s",
                        (control["principals"]["both"],),
                    )
                else:
                    await store.revoke_session(ctx)
            with suppress(ControlDenied):
                assert not await store._call(
                    "renew_activity_stream", ctx.session_id, ctx.lookup_hash, ident
                )
            await store._call("close_activity_stream", ctx.session_id, ctx.lookup_hash, ident)
            with psycopg.connect(control["migration"]) as conn:
                assert conn.execute(
                    "SELECT count(*) FROM markos_control.activity_streams"
                ).fetchone() == (0,)

    asyncio.run(scenario())


@pytest.mark.parametrize("cause", ["membership", "provider", "database"])
def test_open_https_stream_stops_before_disclosing_revoked_activity(
    control: dict[str, Any], tmp_path: Path, cause: str
) -> None:
    async def scenario() -> None:
        async with web_server(control, tmp_path) as server:
            admin = await session(server["store"])
            conversations = await server["store"].read_conversations(admin, UUID(OWNER), "personal")
            conv = conversations[0].conversation.conversation_id
            await server["store"].stage_message(
                admin, UUID(OWNER), request(conv, key="message:visible")
            )
            client = await cli_client(server, tmp_path, "viewer")
            arrived = threading.Event()

            def read() -> list[dict[str, Any]]:
                items = []
                for item in client.activity(OWNER, "personal", max_events=2, seconds=15):
                    items.append(item)
                    arrived.set()
                return items

            task = asyncio.create_task(asyncio.to_thread(read))
            assert await asyncio.to_thread(arrived.wait, 5)
            with psycopg.connect(control["migration"], autocommit=True) as conn:
                if cause == "membership":
                    conn.execute(
                        "DELETE FROM markos_control.memberships WHERE principal_id=%s",
                        (control["principals"]["viewer"],),
                    )
                elif cause == "provider":
                    provider = conn.execute(
                        "SELECT provider_session_id FROM markos_control.sessions "
                        "WHERE principal_id=%s",
                        (control["principals"]["viewer"],),
                    ).fetchone()
                    assert provider
                    server["issuer"].revoke(provider[0])
            await server["store"].stage_message(
                admin, UUID(OWNER), request(conv, key="message:hidden")
            )
            if cause == "database":
                await server["store"]._pool.close()
            started = time.monotonic()
            items = await asyncio.wait_for(task, 15)
            assert len(items) == 1 and time.monotonic() - started < 15

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "fault",
    [
        "resource",
        "principal",
        "owner",
        "domain",
        "after-type",
        "expired",
        "epoch",
        "head",
        "legacy",
    ],
)
def test_activity_cursor_requires_exact_current_resource_and_scope(fault: str) -> None:
    import json
    from types import SimpleNamespace

    from markos_runtime.api.activity import cursor, resume
    from markos_runtime.api.application import ApiFault
    from markos_runtime.api.identity import SecretCodec

    api: Any = SimpleNamespace(codec=SecretCodec(b"c" * 32))
    binding = {"principal_id": OWNER}
    head = {"epoch": OWNER, "head": 7}
    value = json.loads(api.codec.open(cursor(api, binding, UUID(OWNER), "personal", OWNER, 7)))
    assert (
        resume(api, api.codec.seal(json.dumps(value)), binding, UUID(OWNER), "personal", head) == 7
    )
    if fault == "legacy":
        del value["resource"]
        del value["epoch"]
    else:
        key, replacement = {
            "resource": ("resource", "messages-v1"),
            "principal": ("principal_id", OTHER),
            "owner": ("owner_id", OTHER),
            "domain": ("domain", "work"),
            "after-type": ("after", True),
            "expired": ("expires", 0),
            "epoch": ("epoch", OTHER),
            "head": ("after", 8),
        }[fault]
        value[key] = replacement
    with pytest.raises(ApiFault) as error:
        resume(api, api.codec.seal(json.dumps(value)), binding, UUID(OWNER), "personal", head)
    assert error.value.status == (409 if fault in {"expired", "epoch", "head", "legacy"} else 400)
