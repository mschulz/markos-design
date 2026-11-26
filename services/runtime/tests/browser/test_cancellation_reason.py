"""The approver sees a useful explanation without gaining any new decision rights."""

import asyncio
from pathlib import Path
from typing import Any

import pytest
from playwright.async_api import expect  # type: ignore[import-not-found]

from tests.browser.test_web import browser_page, login
from tests.control_api.test_conversation import mutate
from tests.control_api.test_governance import ready_flow
from tests.web_support import web_server


@pytest.mark.parametrize(
    "cause,text",
    [
        ("sign-out", "Cancelled because the person who sent this request signed out."),
        ("unknown", "This request was cancelled, but the reason wasn’t recorded."),
    ],
)
def test_cancelled_request_explains_reason_and_cannot_be_approved(
    conversation: dict[str, Any], tmp_path: Path, cause: str, text: str
) -> None:
    async def scenario() -> None:
        async with ready_flow(conversation, decide=False) as flow:
            if cause == "sign-out":
                await flow["store"].revoke_session(flow["ctx"])
            else:
                mutate(
                    conversation,
                    "UPDATE markos_control.tool_intents "
                    "SET status='revoked',terminal_at=clock_timestamp()",
                )
            async with (
                web_server(conversation, tmp_path, prepare_agent=False, governance=True) as server,
                browser_page(server, mobile=True) as page,
            ):
                await login(page, server, "approver")
                await page.get_by_role("link", name="Approvals", exact=True).click()
                await expect(
                    page.get_by_role("heading", name="Request cancelled", exact=True)
                ).to_be_visible()
                await expect(page.locator("#view")).to_contain_text(text)
                await expect(page.locator("#view")).to_contain_text(
                    "Send a new request to try again."
                )
                assert (
                    await page.get_by_role("button", name="Review approve", exact=True).count() == 0
                )
                assert (
                    await page.get_by_role("button", name="Review revoke", exact=True).count() == 0
                )
                assert "Approval · revoked" not in await page.locator("#view").inner_text()

    asyncio.run(scenario())
