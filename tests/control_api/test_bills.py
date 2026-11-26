"""Actual scoped SQL + HTTPS workflow, with no broker, model or deployed preview."""

import asyncio
from pathlib import Path
from typing import Any

import psycopg
import pytest
from psycopg.types.json import Jsonb

from markos_runtime.knowledge.bills import SAMPLE
from tests.control_api.test_api import SCOPE
from tests.integration.conftest import OTHER
from tests.web_support import WebBrowserClient, web_server


def test_bill_import_restart_duplicate_source_questions_and_scope(
    control: dict[str, Any], tmp_path: Path
) -> None:
    async def scenario() -> None:
        async with web_server(control, tmp_path, bills=True, prepare_agent=False) as server:
            sender = WebBrowserClient(server["port"], server["cert"])
            await sender.login(server["issuer"], server["credentials"]["both"])
            body = {"schema_version": 1, "name": "October sample", "text": SAMPLE}
            status, saved = await sender.call("POST", "/api/v1/bills" + SCOPE, body)
            assert status == 201, saved
            bill_id = saved["bill_id"]
            path = "/api/v1/bills/" + bill_id
            status, same = await sender.call("POST", "/api/v1/bills" + SCOPE, body)
            assert status == 201 and same["bill_id"] == bill_id
            # A new connection/session reads durable records and the original object.
            viewer = WebBrowserClient(server["port"], server["cert"])
            await viewer.login(server["issuer"], server["credentials"]["viewer"])
            status, reread = await viewer.call("GET", path + SCOPE)
            assert status == 200 and reread == saved
            status, source = await viewer.call("GET", path + "/source" + SCOPE)
            assert status == 200 and source["lines"] == SAMPLE.splitlines()
            status, result = await viewer.call(
                "POST",
                path + "/questions" + SCOPE,
                {"schema_version": 1, "question": "How much do I owe?"},
            )
            assert status == 200 and result["answer"] == "Amount due: 148.50 AUD."
            assert result["citations"] == [{"line": 9, "quote": "Amount due: $148.50"}]
            status, _ = await viewer.call("POST", "/api/v1/bills" + SCOPE, body)
            assert status == 403
            other = WebBrowserClient(server["port"], server["cert"])
            await other.login(server["issuer"], server["credentials"]["other"])
            other_scope = "?owner_id=" + OTHER + "&domain=personal"
            status, page = await other.call("GET", "/api/v1/bills" + other_scope)
            assert status == 200 and page["items"] == []
            for suffix in ("", "/source", "/questions"):
                status, _ = await other.call(
                    "POST" if suffix == "/questions" else "GET",
                    path + suffix + SCOPE,
                    {"schema_version": 1, "question": "How much do I owe?"}
                    if suffix == "/questions"
                    else None,
                )
                assert status == 403
                status, _ = await sender.call(
                    "GET", path + SCOPE.replace("domain=personal", "domain=research")
                )
                assert status == 404
            # Even a well-shaped but altered stored figure cannot outrun its source evidence.
            with psycopg.connect(control["migration"]) as conn:
                conn.execute(
                    "UPDATE markos_control.bill_records "
                    "SET report=jsonb_set(report,'{facts,5,value}',%s)",
                    (Jsonb("0.00"),),
                )
            status, _ = await viewer.call(
                "POST",
                path + "/questions" + SCOPE,
                {"schema_version": 1, "question": "How much do I owe?"},
            )
            assert status == 503
            with psycopg.connect(control["migration"]) as conn:
                conn.execute(
                    "UPDATE markos_control.bill_records SET report=%s", (Jsonb(saved["report"]),)
                )
            # Tampering with the original must block even a stored-figure answer.
            original = next((tmp_path / "bill-objects").rglob("*.txt"))
            original.write_bytes(b"tampered")
            status, _ = await viewer.call(
                "POST",
                path + "/questions" + SCOPE,
                {"schema_version": 1, "question": "How much do I owe?"},
            )
            assert status == 503
            with (
                psycopg.connect(control["gateway"]) as conn,
                pytest.raises(psycopg.errors.InsufficientPrivilege),
            ):
                conn.execute("SELECT * FROM markos_control.bill_records")
            with psycopg.connect(control["migration"]) as conn:
                assert conn.execute(
                    "SELECT count(*) FROM markos_control.bill_records"
                ).fetchone() == (1,)

    asyncio.run(scenario())


@pytest.mark.parametrize("enabled", [False, True])
def test_bill_routes_disabled_by_default_and_require_csrf(
    control: dict[str, Any], tmp_path: Path, enabled: bool
) -> None:
    async def scenario() -> None:
        async with web_server(control, tmp_path, bills=enabled, prepare_agent=False) as server:
            client = WebBrowserClient(server["port"], server["cert"])
            await client.login(server["issuer"], server["credentials"]["both"])
            if not enabled:
                assert (await client.call("GET", "/api/v1/bills" + SCOPE))[0] == 404
                assert (await client.call("GET", "/app/bills"))[0] == 404
            else:
                client.csrf = "invalid"
                assert (
                    await client.call(
                        "POST",
                        "/api/v1/bills" + SCOPE,
                        {"schema_version": 1, "name": "test", "text": SAMPLE},
                    )
                )[0] == 403

    asyncio.run(scenario())
