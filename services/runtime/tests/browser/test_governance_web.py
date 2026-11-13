"""Mobile keyboard approval flow through shared HTTPS and the actual SDK fixture."""

import asyncio
import importlib.util
import os
from pathlib import Path
from typing import Any
from uuid import UUID

import pytest
from playwright.async_api import expect  # type: ignore[import-not-found]

from markos_runtime.store.database import DurableStore
from markos_runtime.tools.transport import FixtureConnection, FixtureTransport
from markos_runtime.tools.worker import GovernedConversationWorker
from tests.browser.test_web import browser_page, login
from tests.control_api.test_conversation import handoff, rows
from tests.integration.conftest import OWNER
from tests.web_support import web_server

if importlib.util.find_spec("mcp") is None:
    if os.environ.get("MARKOS_REQUIRE_TOOLS_TESTS") == "1":
        pytest.fail("required governance browser fixture missing")
    pytest.skip("optional MCP fixture not installed", allow_module_level=True)

from tests.tools_support import tool_server


def test_mobile_exact_approval_and_inert_result(
    conversation: dict[str, Any], tmp_path: Path
) -> None:
    async def scenario() -> None:
        async with (
            web_server(conversation, tmp_path, governance=True) as server,
            tool_server(tmp_path / "tool", UUID(OWNER), "personal") as remote,
            browser_page(server, mobile=True) as page,
            DurableStore.connect(conversation["chatworker"]) as store,
        ):
            await login(page, server)
            await page.get_by_role("link", name="Configuration", exact=True).click()
            await page.get_by_label("Test service address", exact=True).fill(remote["endpoint"])
            await page.get_by_label("Reason for access", exact=True).fill(
                "Synthetic browser review"
            )
            await page.get_by_label("Allow this test service", exact=True).check()
            await page.get_by_role("button", name="Review test access", exact=True).click()
            await expect(page.get_by_role("dialog")).to_contain_text(remote["endpoint"])
            await page.locator("#preview-cancel").click()
            assert rows(conversation, "SELECT count(*) FROM markos_control.tool_grants") == [(0,)]
            await page.get_by_role("button", name="Review test access", exact=True).click()
            await page.locator("#preview-apply").click()
            await expect(page.locator("#notice")).to_contain_text("Test access updated")
            await page.get_by_role("link", name="Conversations", exact=True).click()
            await page.get_by_label("Message", exact=True).fill(
                "Synthetic governed browser request"
            )
            label = '<img src=x onerror="window.toolInjected=1">'
            await page.get_by_label("Test label", exact=True).fill(label)
            await page.get_by_role("button", name="Review test request", exact=True).click()
            await expect(page.get_by_role("dialog")).to_contain_text(label)
            async with page.expect_response(
                lambda r: "/api/v1/tool-requests?" in r.url and r.status == 202
            ) as pending:
                await page.locator("#preview-apply").click()
            receipt = await (await pending.value).json()
            await handoff(conversation, receipt)
            transport = FixtureTransport(
                FixtureConnection(
                    endpoint=remote["endpoint"],
                    owner_id=UUID(OWNER),
                    domain="personal",
                    ca_file=remote["cert"],
                    credential=remote["credential"],
                ),
                confirm_synthetic=True,
            )
            worker = GovernedConversationWorker(
                store, server["issuer"], transport=transport, confirm_synthetic=True
            )
            assert await worker.run_once()
            await page.get_by_role("link", name="Approvals", exact=True).click()
            await expect(page.locator("#view")).to_contain_text(label)
            assert await page.get_by_role("button", name="Review approve", exact=True).count() == 0
            async with browser_page(server, mobile=True) as approver_page:
                await login(approver_page, server, "approver")
                await approver_page.get_by_role("link", name="Approvals", exact=True).click()
                await approver_page.get_by_role("button", name="Review approve", exact=True).focus()
                await approver_page.keyboard.press("Enter")
                await expect(approver_page.get_by_role("dialog")).to_contain_text(label)
                await expect(approver_page.get_by_role("dialog")).to_contain_text("fixture.read")
                await expect(approver_page.get_by_role("dialog")).to_contain_text(
                    remote["endpoint"]
                )
                await approver_page.locator("#preview-cancel").click()
                assert rows(conversation, "SELECT status FROM markos_control.tool_intents") == [
                    ("awaiting",)
                ]
                await approver_page.get_by_role("button", name="Review approve", exact=True).click()
                await approver_page.locator("#preview-apply").click()
                await expect(approver_page.locator("#notice")).to_contain_text("Decision recorded")
                assert await worker.run_once()
                await approver_page.get_by_role(
                    "button", name="Refresh approvals", exact=True
                ).click()
                await expect(approver_page.locator("#view")).to_contain_text("Outcome: succeeded")
                await expect(approver_page.locator("#view")).to_contain_text(label)
                assert await approver_page.locator("#view img").count() == 0
                assert await approver_page.evaluate("window.toolInjected") is None
                assert await approver_page.evaluate(
                    "document.documentElement.scrollWidth <= window.innerWidth"
                )
                assert remote["state"]["ledger"] == {label: 1}
                assert (
                    await approver_page.evaluate("localStorage.length + sessionStorage.length") == 0
                )

    asyncio.run(scenario())
