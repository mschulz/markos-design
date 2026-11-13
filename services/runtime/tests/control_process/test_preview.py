"""Packaged synthetic MVP: browser/CLI, durable approval/restarts, outages and restore."""

import asyncio
import http.client
import json
import ssl
import time
from pathlib import Path
from typing import Any
from uuid import UUID

import psycopg
from playwright.async_api import expect  # type: ignore[import-not-found]

from markos_runtime.client.http import ClientError
from markos_runtime.control_lifecycle.issuer import RemoteFixtureIssuer
from tests.browser.test_web import browser_page, login
from tests.control_api.test_api import SCOPE
from tests.control_api.test_conversation import request_value
from tests.control_api.test_web_cli import cli_client
from tests.control_process.support import (
    APPS,
    CERTS,
    FOLDER,
    ORIGIN,
    admin,
    command,
    compose,
    container,
    data,
    wait_healthy,
)


def scalar(query: str, *, database: str = "markos_m1g") -> Any:
    from psycopg.conninfo import make_conninfo

    with psycopg.connect(make_conninfo(admin(), dbname=database)) as conn:
        row = conn.execute(query).fetchone()
        assert row is not None
        return row[0]


async def until(query: str, expected: Any, *, seconds: int = 60) -> None:
    async with asyncio.timeout(seconds):
        while scalar(query) != expected:
            await asyncio.sleep(0.2)


def fixture_control(operation: str, **args: Any) -> Any:
    state = data()
    conn = http.client.HTTPSConnection(
        "127.0.0.1",
        19443,
        context=ssl.create_default_context(cafile=str(CERTS / "ca.pem")),
        timeout=3,
    )
    try:
        conn.request(
            "POST",
            "/fixture-control",
            body=json.dumps({"operation": operation, **args}).encode(),
            headers={
                "Authorization": "Bearer " + state["tokens"]["tool_admin"],
                "Content-Type": "application/json",
                "X-Markos-Resource": "https://127.0.0.1:19443/mcp",
            },
        )
        response = conn.getresponse()
        assert response.status == 200
        return json.loads(response.read(65536))
    finally:
        conn.close()


def test_packaged_preview_restart_outages_and_fresh_database_restore(tmp_path: Path) -> None:
    state = data()
    container("gateway")
    measurements: dict[str, Any] = {}
    server = {
        "origin": ORIGIN,
        "cert": CERTS / "ca.pem",
        "credentials": state["credentials"],
        "issuer": RemoteFixtureIssuer(
            "https://127.0.0.1:19444/fixture", CERTS / "ca.pem", state["tokens"]["api"]
        ),
    }

    async def scenario() -> None:
        client = await cli_client(server, tmp_path)
        approver = await cli_client(server, tmp_path, "approver")
        async with browser_page(server, mobile=True) as page:
            await login(page, server)
            await expect(
                page.get_by_role("heading", name="Conversations", exact=True)
            ).to_be_visible()
            for name in (
                "Agents and health",
                "Activity",
                "Approvals",
                "Configuration",
                "Conversations",
            ):
                await page.get_by_role("link", name=name, exact=True).click()
                await expect(page.get_by_role("heading", name=name, exact=True)).to_be_visible()
            assert await page.evaluate("localStorage.length + sessionStorage.length") == 0
            await page.screenshot(path=str(tmp_path / "synthetic-preview.png"), full_page=True)
        conv = UUID(state["conversation_id"])
        value = request_value(conv, "preview:plain")
        started = time.monotonic()
        receipt = await asyncio.to_thread(
            client.request, "/api/v1/conversations/" + str(conv) + "/messages" + SCOPE, value
        )
        await until(
            "SELECT count(*) FROM markos_control.messages WHERE author_kind='test-agent'", 1
        )
        measurements["plain_chat_seconds"] = round(time.monotonic() - started, 3)
        assert (
            await asyncio.to_thread(
                client.request, "/api/v1/conversations/" + str(conv) + "/messages" + SCOPE, value
            )
            == receipt
        )
        # Gateway and issuer restart preserve the committed reply and CLI session.
        await asyncio.to_thread(compose, "stop", *APPS)
        await asyncio.to_thread(compose, "up", "-d", "--no-build", "--force-recreate", *APPS)
        await asyncio.to_thread(wait_healthy)
        assert await asyncio.to_thread(client.request, "/api/v1/agents" + SCOPE)

        async def tool_request(key: str) -> Any:
            return await asyncio.to_thread(
                client.request,
                "/api/v1/tool-requests" + SCOPE,
                {
                    **request_value(conv, key),
                    "fixture_arguments": {"schema_version": 1, "label": "Synthetic preview read"},
                },
            )

        await tool_request("preview:approved")
        await until("SELECT count(*) FROM markos_control.tool_intents WHERE status='awaiting'", 1)
        assert (
            scalar("SELECT count(*) FROM markos_runtime.execution_slots WHERE task_id IS NOT NULL")
            == 0
        )
        await asyncio.to_thread(compose, "stop", *APPS)
        await asyncio.to_thread(compose, "up", "-d", "--no-build", "--force-recreate", *APPS)
        await asyncio.to_thread(wait_healthy)
        approvals = await asyncio.to_thread(approver.request, "/api/v1/approvals" + SCOPE)
        item = approvals["items"][0]

        async def approve(item: Any) -> None:
            await asyncio.to_thread(
                approver.request,
                "/api/v1/approvals/" + item["intent_id"] + "/decisions" + SCOPE,
                {
                    "schema_version": 1,
                    "intent_id": item["intent_id"],
                    "intent_sha256": item["intent_sha256"],
                    "expected_revision": item["revision"],
                    "decision": "approve",
                },
            )

        await approve(item)
        await until(
            "SELECT count(*) FROM markos_control.messages WHERE author_kind='test-agent'", 2
        )
        ledger = await asyncio.to_thread(fixture_control, "ledger")
        assert len(ledger["operations"]) == 1
        assert next(iter(ledger["operations"].values()))["requests"] == 1
        # The external fixture records its effect before withholding its response. Kill the caller.
        await asyncio.to_thread(compose, "stop", "worker-two")
        await asyncio.to_thread(fixture_control, "hold", enabled=True)
        await tool_request("preview:unknown")
        await until("SELECT count(*) FROM markos_control.tool_intents WHERE status='awaiting'", 1)
        items = (await asyncio.to_thread(approver.request, "/api/v1/approvals" + SCOPE))["items"]
        await approve(next(x for x in items if x["status"] == "awaiting"))
        async with asyncio.timeout(20):
            while len((await asyncio.to_thread(fixture_control, "ledger"))["operations"]) != 2:
                await asyncio.sleep(0.1)
        await asyncio.to_thread(command, ["docker", "kill", "--signal=KILL", container("worker")])
        await asyncio.to_thread(fixture_control, "hold", enabled=False)
        await asyncio.to_thread(compose, "start", "worker", "worker-two")
        await until(
            "SELECT count(*) FROM markos_control.tool_dispatches WHERE state='unknown'",
            1,
            seconds=60,
        )
        ledger = await asyncio.to_thread(fixture_control, "ledger")
        assert all(x["requests"] == 1 and x["effects"] == 1 for x in ledger["operations"].values())
        measurements["external_effects"] = 2
        measurements["unknown_not_repeated"] = True
        # Dependency loss is recoverable without losing PostgreSQL-backed history.
        for dependency in ("rabbitmq", "postgres"):
            await asyncio.to_thread(compose, "stop", dependency)
            if dependency == "postgres":
                try:
                    await asyncio.to_thread(client.request, "/api/v1/agents" + SCOPE)
                except ClientError:
                    pass
                else:
                    raise AssertionError("database outage must fail closed")
            await asyncio.to_thread(compose, "start", dependency)
            await asyncio.to_thread(wait_healthy)
        assert (
            scalar("SELECT count(*) FROM markos_control.messages WHERE author_kind='test-agent'")
            == 2
        )
        # Quiesced fresh restore reapplies independently current deny facts.
        await asyncio.to_thread(compose, "stop", *APPS)
        snapshot = await asyncio.to_thread(
            compose, "exec", "-T", "postgres", "pg_dump", "-U", "postgres", "-Fc", "markos_m1g"
        )
        await asyncio.to_thread(
            compose,
            "exec",
            "-T",
            "postgres",
            "createdb",
            "-U",
            "postgres",
            "-O",
            "markos_migrator",
            "markos_m1g_restored",
        )
        await asyncio.to_thread(
            compose,
            "exec",
            "-T",
            "postgres",
            "pg_restore",
            "-U",
            "postgres",
            "--no-owner",
            "--role=markos_migrator",
            "-d",
            "markos_m1g_restored",
            body=snapshot,
        )
        del snapshot
        from psycopg.conninfo import make_conninfo

        with psycopg.connect(
            make_conninfo(admin(), dbname="markos_m1g_restored", user="markos_migrator"),
            autocommit=True,
        ) as conn:
            old = conn.execute("SELECT markos_runtime.current_epoch()").fetchone()
            fresh = conn.execute("SELECT markos_runtime.fence_restored_state()").fetchone()
            assert fresh != old
            # Test-only deny manifest represents revocations current outside the older SQL snapshot.
            conn.execute(
                "UPDATE markos_control.principals SET disabled=true WHERE principal_id=%s",
                (state["principals"]["both"],),
            )
            conn.execute("UPDATE markos_control.tool_grants SET enabled=false")
            conn.execute("DELETE FROM markos_control.http_requests")
        for file in FOLDER.glob("*.env"):
            file.write_text(
                file.read_text().replace("dbname=markos_m1g ", "dbname=markos_m1g_restored ")
            )
        await asyncio.to_thread(compose, "up", "-d", "--no-build", "--force-recreate", *APPS)
        await asyncio.to_thread(wait_healthy)
        try:
            await asyncio.to_thread(client.request, "/api/v1/agents" + SCOPE)
        except ClientError:
            pass
        else:
            raise AssertionError("pre-restore session must fail closed")
        viewer = await cli_client(server, tmp_path, "viewer")
        page = await asyncio.to_thread(
            viewer.request, "/api/v1/conversations/" + str(conv) + "/messages" + SCOPE
        )
        assert len(page["items"]) >= 5
        assert (
            scalar(
                "SELECT count(*) FROM markos_control.tool_dispatches WHERE state='unknown'",
                database="markos_m1g_restored",
            )
            == 1
        )
        ledger = await asyncio.to_thread(fixture_control, "ledger")
        assert len(ledger["operations"]) == 2 and all(
            x["requests"] == 1 for x in ledger["operations"].values()
        )
        measurements["fresh_database_restore"] = True
        measurements["production_ready"] = False

    asyncio.run(scenario())
    # Only coarse synthetic measurements are published. No DB dump, cookies, IDs or fixture keys.
    (tmp_path / "measurements.json").write_text(json.dumps(measurements, sort_keys=True) + "\n")
