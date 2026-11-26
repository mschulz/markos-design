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


def test_pdf_bill_upload_limits_page_citations_and_tamper_check(
    control: dict[str, Any], tmp_path: Path
) -> None:
    from tests.pdf_support import amber_pdf, make_pdf

    pdf = {"content-type": "application/pdf"}
    upload = "/api/v1/bills/pdf" + SCOPE + "&name=November%20Amber"

    async def scenario() -> None:
        async with web_server(control, tmp_path, bills=True, prepare_agent=False) as server:
            sender = WebBrowserClient(server["port"], server["cert"])
            await sender.login(server["issuer"], server["credentials"]["both"])
            status, saved = await sender.call("POST", upload, headers=pdf, raw=amber_pdf())
            assert status == 201, saved
            assert saved["name"] == "November Amber"
            assert saved["report"]["extractor"] == "amber-pdf-v1"
            assert {f["state"] for f in saved["report"]["facts"]} == {"found"}
            path = "/api/v1/bills/" + saved["bill_id"]
            viewer = WebBrowserClient(server["port"], server["cert"])
            await viewer.login(server["issuer"], server["credentials"]["viewer"])
            status, reread = await viewer.call("GET", path + SCOPE)
            assert status == 200 and reread == saved
            status, source = await viewer.call("GET", path + "/source" + SCOPE)
            assert status == 200 and [p["page"] for p in source["pages"]] == [1, 2]
            assert source["pages"][1]["lines"][26] == "BILL TOTAL $47.99"
            status, result = await viewer.call(
                "POST",
                path + "/questions" + SCOPE,
                {"schema_version": 1, "question": "How much do I owe?"},
            )
            assert status == 200, result
            assert result["citations"][0] == {"page": 2, "line": 27, "quote": "BILL TOTAL $47.99"}
            # Viewers cannot upload, and only this route accepts a PDF body over 64 KiB.
            assert (await viewer.call("POST", upload, headers=pdf, raw=amber_pdf()))[0] == 403
            for body, headers, expected in [
                (amber_pdf(), None, (400, "invalid")),
                (b"not a pdf", pdf, (422, "bill-pdf-not-pdf")),
                (
                    make_pdf(["Another Energy retailer"]),
                    pdf,
                    (422, "bill-pdf-unsupported-supplier"),
                ),
                (make_pdf([""]), pdf, (422, "bill-pdf-no-text")),
            ]:
                status, value = await sender.call("POST", upload, headers=headers, raw=body)
                assert (status, value["code"]) == expected
            # Declared sizes are rejected before the body is read.
            for route, size in [(upload, 2 * 1024 * 1024 + 1), ("/api/v1/bills" + SCOPE, 65537)]:
                status, value = await sender.call(
                    "POST", route, headers={**pdf, "content-length": str(size)}, raw=b"%PDF-"
                )
                assert (status, value["code"]) == (413, "too-large")
            # A changed PDF original blocks every answer.
            original = next((tmp_path / "bill-objects").rglob("*.pdf"))
            original.write_bytes(make_pdf(["tampered"]))
            status, _ = await viewer.call("GET", path + SCOPE)
            assert status == 503

    asyncio.run(scenario())


def test_origin_pdf_upload_is_stored_and_rechecked(control: dict[str, Any], tmp_path: Path) -> None:
    from tests.pdf_support import origin_pdf

    upload = "/api/v1/bills/pdf" + SCOPE + "&name=Origin%20Q1"

    async def scenario() -> None:
        async with web_server(control, tmp_path, bills=True, prepare_agent=False) as server:
            client = WebBrowserClient(server["port"], server["cert"])
            await client.login(server["issuer"], server["credentials"]["both"])
            headers = {"content-type": "application/pdf"}
            status, saved = await client.call("POST", upload, headers=headers, raw=origin_pdf())
            assert status == 201, saved
            assert saved["report"]["extractor"] == "origin-pdf-v1"
            assert {f["state"] for f in saved["report"]["facts"]} == {"found"}
            status, reread = await client.call("GET", "/api/v1/bills/" + saved["bill_id"] + SCOPE)
            assert status == 200 and reread == saved

    asyncio.run(scenario())


def test_corrections_keep_the_report_and_change_answers(
    control: dict[str, Any], tmp_path: Path
) -> None:
    async def scenario() -> None:
        async with web_server(control, tmp_path, bills=True, prepare_agent=False) as server:
            operator = WebBrowserClient(server["port"], server["cert"])
            await operator.login(server["issuer"], server["credentials"]["both"])
            status, saved = await operator.call(
                "POST",
                "/api/v1/bills" + SCOPE,
                {"schema_version": 1, "name": "fix", "text": SAMPLE},
            )
            assert status == 201 and saved["corrections"] == []
            assert saved["effective"] == saved["report"]
            path = "/api/v1/bills/" + saved["bill_id"]
            fix = {"schema_version": 1, "key": "total_due", "value": "150", "reason": "Paper copy"}
            status, fixed = await operator.call("POST", path + "/corrections" + SCOPE, fix)
            assert status == 201, fixed
            assert fixed["report"] == saved["report"]  # the extracted report never changes
            assert [(c["key"], c["value"]) for c in fixed["corrections"]] == [
                ("total_due", "150.00")
            ]
            total = next(f for f in fixed["effective"]["facts"] if f["key"] == "total_due")
            assert (total["state"], total["value"]) == ("corrected", "150.00")
            for bad in ({**fix, "key": "demand_charge"}, {**fix, "key": "usage_charge", "value": "-1"}):
                assert (await operator.call("POST", path + "/corrections" + SCOPE, bad))[0] == 400
            viewer = WebBrowserClient(server["port"], server["cert"])
            await viewer.login(server["issuer"], server["credentials"]["viewer"])
            assert (await viewer.call("POST", path + "/corrections" + SCOPE, fix))[0] == 403
            status, reread = await viewer.call("GET", path + SCOPE)
            assert status == 200 and reread == fixed
            status, result = await viewer.call(
                "POST",
                path + "/questions" + SCOPE,
                {"schema_version": 1, "question": "How much do I owe?"},
            )
            assert status == 200 and "150.00" in result["answer"]
            assert "You corrected this figure" in result["answer"]
            withdraw = {**fix, "value": None, "reason": "Paper copy was wrong"}
            status, back = await operator.call("POST", path + "/corrections" + SCOPE, withdraw)
            assert status == 201 and back["effective"] == back["report"]
            assert len(back["corrections"]) == 2

    asyncio.run(scenario())
