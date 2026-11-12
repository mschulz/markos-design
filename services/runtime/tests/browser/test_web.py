"""Actual Chromium rendering, keyboard, scope and preview checks through local HTTPS."""

import asyncio
import base64
import hashlib
import json
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any
from uuid import UUID

import psycopg
from cryptography import x509  # type: ignore[import-not-found]
from cryptography.hazmat.primitives import serialization  # type: ignore[import-not-found]
from playwright.async_api import async_playwright, expect  # type: ignore[import-not-found]

from markos_runtime.control.conversation import ConversationWorker
from markos_runtime.store.database import DurableStore
from tests.control_api.test_conversation import handoff, rows
from tests.integration.conftest import OWNER
from tests.web_support import web_server


@asynccontextmanager
async def browser_page(server: dict[str, Any], *, mobile: bool = False) -> Any:
    cert = x509.load_pem_x509_certificate(server["cert"].read_bytes())
    public = cert.public_key().public_bytes(
        serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
    )
    pin = base64.b64encode(hashlib.sha256(public).digest()).decode()
    async with async_playwright() as driver:
        browser = await driver.chromium.launch(
            headless=True,
            args=[
                "--ignore-certificate-errors-spki-list=" + pin,
                "--disable-background-networking",
            ],
        )
        context = await browser.new_context(
            ignore_https_errors=False,
            viewport={"width": 375 if mobile else 1200, "height": 820},
            service_workers="block",
            accept_downloads=False,
        )

        async def local_only(route: Any) -> None:
            if route.request.url.startswith(server["origin"] + "/"):
                await route.continue_()
            else:
                await route.abort()

        await context.route("**/*", local_only)
        page = await context.new_page()
        try:
            yield page
        finally:
            await context.close()
            await browser.close()


async def login(page: Any, server: dict[str, Any], who: str = "both") -> None:
    await page.goto(server["origin"] + "/app/sign-in")
    await page.get_by_role("button", name="Sign in", exact=True).click()
    await page.get_by_label("Fixture credential", exact=True).fill(server["credentials"][who])
    await page.get_by_role("button", name="Continue", exact=True).click()
    await page.wait_for_url("**/app/conversations")
    await expect(page.get_by_label("Workspace", exact=True)).to_be_visible()


def test_browser_conversation_renders_plain_text_and_durable_reply(
    conversation: dict[str, Any], tmp_path: Path
) -> None:
    async def scenario() -> None:
        async with web_server(conversation, tmp_path) as server, browser_page(server) as page:
            await login(page, server)
            await expect(page.get_by_label("Message", exact=True)).to_be_visible()
            await page.get_by_role("button", name="New conversation", exact=True).focus()
            async with page.expect_response(
                lambda r: "/messages?" in r.url and r.request.method == "GET"
            ):
                await page.keyboard.press("Enter")
            await expect(page.get_by_label("Message", exact=True)).to_be_visible()
            payload = (
                '<img src=x onerror="window.markosInjected=1">'
                "<script>window.markosInjected=2</script>"
            )
            await page.get_by_label("Message", exact=True).fill(payload)
            async with page.expect_response(
                lambda r: "/messages?" in r.url and r.request.method == "POST" and r.status == 202
            ) as pending:
                await page.get_by_role("button", name="Send message", exact=True).click()
            receipt = await (await pending.value).json()
            await handoff(conversation, receipt)
            async with DurableStore.connect(conversation["chatworker"]) as store:
                assert await ConversationWorker(
                    store, server["issuer"], confirm_synthetic=True
                ).run_once()
            await expect(page.locator("#messages")).to_contain_text(
                "Synthetic acknowledgement:", timeout=10000
            )
            await expect(page.locator("#messages")).to_contain_text(payload)
            assert await page.evaluate("window.markosInjected") is None
            assert await page.locator("#messages img").count() == 0
            assert await page.evaluate("localStorage.length + sessionStorage.length") == 0
            assert "__Host-markos-session" not in await page.evaluate("document.cookie")
            assert rows(
                conversation,
                "SELECT count(*) FROM markos_control.messages WHERE author_kind='test-agent'",
            ) == [(1,)]
            for name, title in [
                ("Agents and health", "Agents and health"),
                ("Activity", "Activity"),
                ("Approvals", "Approvals"),
                ("Configuration", "Configuration"),
            ]:
                await page.get_by_role("link", name=name, exact=True).click()
                await expect(page.get_by_role("heading", name=title, exact=True)).to_be_visible()
                if name == "Approvals":
                    await expect(
                        page.get_by_role("button", name="Approve", exact=True)
                    ).to_be_disabled()
            await page.get_by_role("button", name="Sign out", exact=True).click()
            await page.wait_for_url("**/app/sign-in")
            assert not any(
                c["name"] == "__Host-markos-session" for c in await page.context.cookies()
            )

    asyncio.run(scenario())


def test_keyboard_mobile_registration_and_exact_configuration_preview(
    control: dict[str, Any], tmp_path: Path
) -> None:
    async def scenario() -> None:
        async with (
            web_server(control, tmp_path, prepare_agent=False) as server,
            browser_page(server, mobile=True) as page,
        ):
            await login(page, server)
            await page.get_by_role("link", name="Agents and health", exact=True).click()
            await page.get_by_label("Agent description", exact=True).fill(
                "Synthetic mobile test agent"
            )
            await page.get_by_role("button", name="Review registration", exact=True).click()
            await expect(page.get_by_role("dialog")).to_be_visible()
            assert rows(control, "SELECT count(*) FROM markos_control.agents") == [(0,)]
            await page.get_by_role("button", name="Cancel", exact=True).click()
            assert rows(control, "SELECT count(*) FROM markos_control.agents") == [(0,)]
            await page.get_by_role("button", name="Review registration", exact=True).click()
            await page.get_by_role("button", name="Apply change", exact=True).focus()
            await page.keyboard.press("Enter")
            await page.get_by_label("Reason for state change", exact=True).fill(
                "Enable synthetic test"
            )
            await page.get_by_role("button", name="Review enable", exact=True).click()
            await expect(page.get_by_role("dialog")).to_contain_text("Expected revision")
            await page.get_by_role("button", name="Apply change", exact=True).click()
            await expect(
                page.get_by_role("button", name="Review disable", exact=True)
            ).to_be_visible()
            await page.get_by_role("link", name="Configuration", exact=True).click()
            await page.get_by_label("Reason for configuration revision", exact=True).fill(
                "Synthetic reviewed change"
            )
            await page.get_by_role("button", name="Review configuration", exact=True).click()
            assert rows(control, "SELECT count(*) FROM markos_control.configurations") == [(0,)]
            await expect(page.get_by_role("dialog")).to_contain_text("Agent revision")
            await page.get_by_role("button", name="Apply change", exact=True).click()
            await expect(page.locator("#view")).to_contain_text("Current configuration revision 1")
            assert await page.evaluate("document.documentElement.scrollWidth <= innerWidth")
            assert await page.get_by_label("Workspace", exact=True).input_value()
            if os.environ.get("MARKOS_UI_SCREENSHOT_DIR"):
                directory = Path(os.environ["MARKOS_UI_SCREENSHOT_DIR"])
                directory.mkdir(parents=True, exist_ok=True)
                await page.screenshot(
                    path=str(directory / "configuration-mobile.png"), full_page=True
                )

    asyncio.run(scenario())


def test_viewer_cannot_write_and_revocation_clears_private_view(
    control: dict[str, Any], tmp_path: Path
) -> None:
    async def scenario() -> None:
        async with web_server(control, tmp_path) as server, browser_page(server) as page:
            await login(page, server, "viewer")
            await page.get_by_role("link", name="Agents and health", exact=True).click()
            await expect(page.locator("#view")).to_contain_text("Synthetic deterministic agent")
            assert await page.get_by_role("button", name="Review disable", exact=True).count() == 0
            status = await page.evaluate(
                """async ([owner])=>{
                  const me=await (await fetch('/api/v1/me')).json();
                  return (await fetch('/api/v1/agents/control-test-agent/state?owner_id='
                    +owner+'&domain=personal', {method:'POST', headers:{
                      'Content-Type':'application/json','X-CSRF-Token':me.csrf_token},
                    body:JSON.stringify({schema_version:1,expected_revision:2,
                      enabled:false,reason:'forged viewer'})})).status;
                }""",
                [OWNER],
            )
            assert status == 403
            with psycopg.connect(control["migration"], autocommit=True) as conn:
                conn.execute(
                    "UPDATE markos_control.principals SET disabled=true WHERE subject='viewer'"
                )
            await expect(page.locator("#notice")).to_contain_text(
                "session has expired", timeout=15000
            )
            assert "Synthetic deterministic agent" not in await page.locator("#view").inner_text()
            await expect(page.get_by_role("link", name="Sign in", exact=True)).to_be_visible()

    asyncio.run(scenario())


def test_configuration_conflict_requires_new_review(
    control: dict[str, Any], tmp_path: Path
) -> None:
    from markos_runtime.contracts.control import ConfigRevision
    from tests.integration.test_control_persistence import session

    async def scenario() -> None:
        async with web_server(control, tmp_path) as server, browser_page(server) as page:
            await login(page, server)
            await page.get_by_role("link", name="Configuration", exact=True).click()
            await page.get_by_label("Reason for configuration revision", exact=True).fill(
                "UI exact preview"
            )
            await page.get_by_role("button", name="Review configuration", exact=True).click()
            await expect(page.get_by_role("dialog")).to_contain_text("UI exact preview")
            ctx = await session(server["store"])
            current = await server["store"].read_configuration(ctx, UUID(OWNER), "personal")
            assert current
            changed = {
                **current.model_dump(mode="json"),
                "revision": 2,
                "previous_revision": 1,
                "reason": "Concurrent reviewed change",
            }
            await server["store"].save_configuration(
                ctx, ConfigRevision.from_wire(json.dumps(changed).encode())
            )
            await page.get_by_role("button", name="Apply change", exact=True).click()
            await expect(page.locator("#notice")).to_contain_text("item changed")
            assert rows(control, "SELECT revision FROM markos_control.configurations") == [(2,)]
            await page.get_by_role("link", name="Configuration", exact=True).click()
            await page.get_by_label("Reason for configuration revision", exact=True).fill(
                "Reviewed again after conflict"
            )
            await page.get_by_role("button", name="Review configuration", exact=True).click()
            await expect(page.get_by_role("dialog")).to_contain_text("Previous revision")
            await page.get_by_role("button", name="Apply change", exact=True).click()
            await expect(page.locator("#view")).to_contain_text("Current configuration revision 3")

    asyncio.run(scenario())


def test_cli_browser_pkce_handoff_keeps_bearer_out_of_browser(
    control: dict[str, Any], tmp_path: Path
) -> None:
    from urllib.parse import parse_qs, urlencode, urlsplit

    from markos_runtime.api.identity import challenge, opaque
    from markos_runtime.client.http import HttpClient

    async def scenario() -> None:
        async with web_server(control, tmp_path) as server, browser_page(server) as page:
            client = HttpClient(server["origin"], server["cert"], confirm_synthetic=True)
            verifier = opaque()
            flow = await asyncio.to_thread(
                client.request,
                "/auth/cli/start",
                {"schema_version": 1, "challenge": challenge(verifier)},
                public=True,
            )
            params = parse_qs(urlsplit(flow["authorization_url"]).query)
            address = (
                server["origin"]
                + "/app/authorize?"
                + urlencode(
                    {
                        "state": flow["state"],
                        "nonce": params["nonce"][0],
                        "code_challenge": challenge(verifier),
                        "mode": "cli",
                    }
                )
            )
            await page.goto(address)
            await page.get_by_label("Fixture credential", exact=True).fill(
                server["credentials"]["operator"]
            )
            await page.get_by_role("button", name="Continue", exact=True).click()
            await expect(page.locator("#cli-code")).to_be_visible()
            code = await page.locator("#cli-code").inner_text()
            result = await asyncio.to_thread(
                client.request,
                "/auth/cli/exchange",
                {
                    "schema_version": 1,
                    "state": flow["state"],
                    "binding": flow["binding"],
                    "code": code,
                    "verifier": verifier,
                },
                public=True,
            )
            assert result["token_type"] == "Bearer"
            assert result["access_token"] not in await page.content()
            assert not any(
                c["name"] == "__Host-markos-session" for c in await page.context.cookies()
            )
            assert await page.evaluate("localStorage.length + sessionStorage.length") == 0

    asyncio.run(scenario())


def test_reviewed_cancellation_and_browser_outage_clear_private_state(
    conversation: dict[str, Any], tmp_path: Path
) -> None:
    async def scenario() -> None:
        async with web_server(conversation, tmp_path) as server, browser_page(server) as page:
            await login(page, server)
            await page.get_by_label("Message", exact=True).fill("Synthetic cancellable request")
            async with page.expect_response(
                lambda r: "/messages?" in r.url and r.request.method == "POST" and r.status == 202
            ):
                await page.get_by_role("button", name="Send message", exact=True).click()
            await page.get_by_role("button", name="Check request status", exact=True).click()
            await page.get_by_role("button", name="Review cancellation", exact=True).click()
            await expect(page.get_by_role("dialog")).to_contain_text(
                "Synthetic cancellable request"
            )
            assert rows(conversation, "SELECT state FROM markos_control.chat_runs") == [("queued",)]
            await page.locator("#preview-cancel").click()
            assert rows(conversation, "SELECT state FROM markos_control.chat_runs") == [("queued",)]
            await page.get_by_role("button", name="Review cancellation", exact=True).click()
            await page.locator("#preview-apply").click()
            await expect(page.locator("#notice")).to_contain_text("cancelled")
            assert rows(conversation, "SELECT state FROM markos_control.chat_runs") == [
                ("cancelled",)
            ]
            assert rows(
                conversation,
                "SELECT count(*) FROM markos_control.messages WHERE author_kind='test-agent'",
            ) == [(0,)]
            await page.route("**/api/v1/me", lambda route: route.abort())
            await expect(page.locator("#notice")).to_contain_text("unavailable", timeout=15000)
            await expect(page.locator("#view")).not_to_contain_text("Synthetic cancellable request")
            await expect(page.get_by_role("button", name="Reload workspace")).to_be_visible()

    asyncio.run(scenario())
