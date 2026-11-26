"""Packaged mail against the fixture Google: connect, alert, sync and disconnect."""

import asyncio
import http.client
import json
import ssl
from typing import Any

from playwright.async_api import expect  # type: ignore[import-not-found]

from tests.browser.test_web import browser_page, login
from tests.control_process.support import CERTS, ORIGIN, data
from tests.control_process.test_preview import scalar, until

GOOGLE = "https://127.0.0.1:19445"
LABEL = "Label_bills"
BOX = "markos_runtime.mailboxes"


def https(port: int, path: str, body: dict[str, Any], headers: dict[str, str]) -> tuple[int, Any]:
    conn = http.client.HTTPSConnection(
        "127.0.0.1",
        port,
        context=ssl.create_default_context(cafile=str(CERTS / "ca.pem")),
        timeout=5,
    )
    try:
        conn.request(
            "POST",
            path,
            body=json.dumps(body).encode(),
            headers={"Content-Type": "application/json", **headers},
        )
        response = conn.getresponse()
        raw = response.read(65536)
        return response.status, json.loads(raw) if raw else None
    finally:
        conn.close()


def google(operation: str, **args: Any) -> Any:
    token = data()["tokens"]["google_admin"]
    status, value = https(
        19445,
        "/fixture-control",
        {"operation": operation, **args},
        {"Authorization": "Bearer " + token},
    )
    assert status == 200
    return value


def test_mail_connect_alert_sync_and_disconnect_across_processes() -> None:
    state = data()
    server = {"origin": ORIGIN, "cert": CERTS / "ca.pem", "credentials": state["credentials"]}

    async def scenario() -> None:
        async with browser_page(server) as page:
            # Page routes win over the context route that keeps the browser on the gateway.
            await page.route(GOOGLE + "/**", lambda route: route.continue_())
            await login(page, server)
            await page.goto(ORIGIN + "/app/mail")
            await page.get_by_label("Allowed Gmail label IDs, separated by commas").fill(LABEL)
            await page.get_by_role("button", name="Connect Gmail", exact=True).click()
            await page.wait_for_url("**/app/mail#connected")

            # The mail process imports, then starts the watch through the fixture Google.
            await until(f"SELECT count(*) FROM {BOX} WHERE connected", 1)
            await until(f"SELECT count(*) FROM {BOX} WHERE history_id IS NOT NULL", 1)
            await until(f"SELECT count(*) FROM {BOX} WHERE watch_expires_at IS NOT NULL", 1)
            assert google("state")["watched"][-1] == [LABEL]

            # New mail, then Google's signed push to the gateway. The mail process syncs it.
            kept = google("deliver", labels=[LABEL])["message_id"]
            google("deliver", labels=["INBOX"])
            push = google("push")
            status, _ = https(
                18443,
                "/api/v1/mail/alerts",
                push["body"],
                {"Authorization": "Bearer " + push["token"]},
            )
            assert status == 204
            await until("SELECT count(*) FROM markos_runtime.mail_messages", 1)
            stored = scalar("SELECT message_id FROM markos_runtime.mail_messages")
            assert stored == kept

            # A forged push is refused before the database is touched.
            status, _ = https(
                18443,
                "/api/v1/mail/alerts",
                push["body"],
                {"Authorization": "Bearer " + push["token"][:-4] + "AAAA"},
            )
            assert status == 401

            await page.goto(ORIGIN + "/app/mail")
            await page.get_by_role("button", name="Disconnect Gmail", exact=True).click()
            await page.locator("#preview-apply").click()
            await expect(page.get_by_text("Disconnect started.")).to_be_visible()
            await until(
                f"SELECT count(*) FROM {BOX} WHERE NOT connected AND disconnect_deadline IS NULL",
                1,
            )
            assert scalar("SELECT count(*) FROM markos_runtime.mail_tokens") == 0
            after = google("state")
            assert after["revoked"] == 1 and after["grants"] == 0 and after["stopped"] >= 1

    asyncio.run(scenario())
