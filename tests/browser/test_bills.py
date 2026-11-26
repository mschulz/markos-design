"""The first bill-to-answer user journey with real PostgreSQL and Chromium."""

import asyncio
from pathlib import Path
from typing import Any

from playwright.async_api import expect  # type: ignore[import-not-found]

from tests.browser.test_web import browser_page, login
from tests.web_support import web_server


def test_bill_summary_question_citation_and_original_survive_reload(
    control: dict[str, Any], tmp_path: Path
) -> None:
    async def scenario() -> None:
        async with (
            web_server(control, tmp_path, bills=True, prepare_agent=False) as server,
            browser_page(server, mobile=True) as page,
        ):
            await login(page, server)
            await page.get_by_role("link", name="Electricity bills", exact=True).click()
            await page.get_by_role("button", name="Try sample bill", exact=True).click()
            await expect(page.get_by_label("Bill name", exact=True)).to_have_value(
                "Sample electricity bill"
            )
            await page.get_by_role("button", name="Save bill", exact=True).click()
            detail = page.get_by_role("region", name="Bill details", exact=True)
            await expect(detail).to_contain_text("148.50 AUD")
            await page.get_by_role("button", name="How much do I owe?", exact=True).click()
            answer = page.get_by_role("region", name="Bill answer", exact=True)
            await expect(answer).to_contain_text("Amount due: 148.50 AUD.")
            await expect(answer).to_contain_text("Source line 9: Amount due: $148.50")
            await page.get_by_role("button", name="Show original text", exact=True).click()
            await expect(
                page.get_by_role("region", name="Original bill text", exact=True)
            ).to_contain_text("9: Amount due: $148.50")
            await page.screenshot(path=str(tmp_path / "bill-mobile.png"), full_page=True)
            await page.reload()
            await page.get_by_role("button", name="Sample electricity bill", exact=True).click()
            await page.get_by_label("Your question", exact=True).fill("Can you pay this bill?")
            await page.get_by_role("button", name="Ask", exact=True).click()
            await expect(
                page.get_by_role("region", name="Bill answer", exact=True)
            ).to_contain_text("I cannot answer that question")

    asyncio.run(scenario())
