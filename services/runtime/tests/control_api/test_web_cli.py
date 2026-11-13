"""Real TLS shared-client and scoped durable stream acceptance."""

import asyncio
import json
import stat
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit
from uuid import UUID

import psycopg
import pytest

from markos_runtime.api.identity import challenge, opaque
from markos_runtime.client.cli import main
from markos_runtime.client.http import ClientError, HttpClient, SessionFile
from markos_runtime.control.store import SessionContext
from tests.control_api.test_api import SCOPE
from tests.integration.conftest import OWNER
from tests.integration.test_control_persistence import request
from tests.web_support import WebBrowserClient as BrowserClient
from tests.web_support import web_server


async def cli_client(server: dict[str, Any], directory: Path, who: str = "both") -> HttpClient:
    path = directory / (who + "-session.json")
    client = HttpClient(server["origin"], server["cert"], session_file=path, confirm_synthetic=True)
    verifier = opaque()
    flow = await asyncio.to_thread(
        client.request,
        "/auth/cli/start",
        {"schema_version": 1, "challenge": challenge(verifier)},
        public=True,
    )
    params = parse_qs(urlsplit(flow["authorization_url"]).query)
    code = server["issuer"].authorize(
        server["credentials"][who], params["code_challenge"][0], params["nonce"][0]
    )
    response = await asyncio.to_thread(
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
    import time

    assert client.session is not None
    client.session.write(
        {
            "schema_version": 1,
            "api_origin": server["origin"],
            "bearer": response["access_token"],
            "expires_at": int(time.time()) + 300,
        }
    )
    return client


def test_cli_and_browser_inspect_same_state_and_safe_configuration_preview(
    control: dict[str, Any], tmp_path: Path, capsys: Any
) -> None:
    async def scenario() -> None:
        async with web_server(control, tmp_path) as server:
            client = await cli_client(server, tmp_path)
            assert client.session is not None
            browser = BrowserClient(server["port"], server["cert"])
            await browser.login(server["issuer"], server["credentials"]["both"])
            for route in (
                "agents",
                "configuration",
                "status",
                "activity",
                "approvals",
                "conversations",
            ):
                remote = await asyncio.to_thread(client.request, "/api/v1/" + route + SCOPE)
                status, value = await browser.call("GET", "/api/v1/" + route + SCOPE)
                assert status == 200
                if isinstance(remote, dict) and "next_cursor" in remote:
                    assert remote["items"] == value["items"]
                else:
                    assert remote == value
            args = [
                "--origin",
                server["origin"],
                "--ca-file",
                str(server["cert"]),
                "--session-file",
                str(client.session.path),
                "--confirm-synthetic",
            ]
            assert (
                await asyncio.to_thread(
                    main, args + ["configuration", "--owner-id", OWNER, "--domain", "personal"]
                )
                == 0
            )
            config = await asyncio.to_thread(client.request, "/api/v1/configuration" + SCOPE)
            revised = {
                **config,
                "revision": 2,
                "previous_revision": 1,
                "reason": "Synthetic preview <script>ignored</script>",
            }
            bodyfile = tmp_path / "revision.json"
            bodyfile.write_text(json.dumps(revised))
            preview_args = args + [
                "configuration-preview",
                "--owner-id",
                OWNER,
                "--domain",
                "personal",
                "--input",
                str(bodyfile),
            ]
            capsys.readouterr()
            assert await asyncio.to_thread(main, preview_args) == 0
            preview = json.loads(capsys.readouterr().out)
            assert preview["applied"] is False
            assert (await asyncio.to_thread(client.request, "/api/v1/configuration" + SCOPE))[
                "revision"
            ] == 1
            apply_args = args + [
                "configuration-apply",
                "--owner-id",
                OWNER,
                "--domain",
                "personal",
                "--input",
                str(bodyfile),
                "--confirm-digest",
                preview["digest"],
            ]
            assert await asyncio.to_thread(main, apply_args) == 0
            assert (await browser.call("GET", "/api/v1/configuration" + SCOPE))[1]["revision"] == 2
            assert await asyncio.to_thread(main, apply_args) == 1
            output = capsys.readouterr()
            assert client.bearer() not in output.out + output.err
            assert stat.S_IMODE(client.session.path.stat().st_mode) == 0o600

    asyncio.run(scenario())


def test_activity_stream_reconnect_uses_committed_history_and_cursor_purpose(
    control: dict[str, Any], tmp_path: Path
) -> None:
    async def scenario() -> None:
        async with web_server(control, tmp_path) as server:
            client = await cli_client(server, tmp_path)
            assert client.session is not None
            browser = BrowserClient(server["port"], server["cert"])
            raw = await browser.login(server["issuer"], server["credentials"]["both"])
            from markos_runtime.api.identity import SecretCodec

            sid, secret = raw.split(".")
            ctx = SessionContext(UUID(sid), SecretCodec(b"s" * 32).lookup("session", secret))
            conversations = await server["store"].read_conversations(ctx, UUID(OWNER), "personal")
            conv = conversations[0].conversation.conversation_id
            await server["store"].stage_message(
                ctx, UUID(OWNER), request(conv, key="message:stream-one")
            )
            first = await asyncio.to_thread(
                lambda: list(client.activity(OWNER, "personal", max_events=1, seconds=10))
            )
            assert len(first) == 1 and first[0]["item"]["kind"] == "queued"
            async with asyncio.timeout(2):
                while True:
                    with psycopg.connect(control["migration"]) as conn:
                        if conn.execute(
                            "SELECT count(*) FROM markos_control.activity_streams"
                        ).fetchone() == (0,):
                            break
                    await asyncio.sleep(0.02)

            await server["store"].stage_message(
                ctx, UUID(OWNER), request(conv, key="message:stream-two")
            )
            resumed = await asyncio.to_thread(
                lambda: list(
                    client.activity(
                        OWNER, "personal", cursor=first[0]["cursor"], max_events=1, seconds=10
                    )
                )
            )
            assert (
                len(resumed) == 1 and resumed[0]["item"]["sequence"] > first[0]["item"]["sequence"]
            )
            status, messages = await browser.call(
                "GET", "/api/v1/conversations/" + str(conv) + "/messages" + SCOPE
            )
            assert status == 200
            assert (
                await browser.call(
                    "GET", "/api/v1/activity" + SCOPE + "&cursor=" + messages["next_cursor"]
                )
            )[0] == 400
            with psycopg.connect(control["migration"], autocommit=True) as conn:
                conn.execute("SELECT markos_runtime.fence_restored_state()")
            # New session after restore cannot silently reuse an old-generation position.
            client = await cli_client(server, tmp_path, "both")
            with pytest.raises(ClientError) as error:
                await asyncio.to_thread(
                    client.request, "/api/v1/activity" + SCOPE + "&cursor=" + first[0]["cursor"]
                )
            assert error.value.status == 409

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "fault", ["wrong-ca", "http", "remote", "scope", "expired-file", "unsafe-mode", "symlink"]
)
def test_explicit_cli_security_boundaries(
    control: dict[str, Any], tmp_path: Path, fault: str
) -> None:
    async def scenario() -> None:
        async with web_server(control, tmp_path) as server:
            client = await cli_client(server, tmp_path)
            assert client.session is not None
            if fault == "wrong-ca":
                with pytest.raises(ClientError):
                    HttpClient(server["origin"], tmp_path / "absent.pem", confirm_synthetic=True)
            elif fault in {"http", "remote"}:
                origin = (
                    server["origin"].replace("https:", "http:")
                    if fault == "http"
                    else "https://remote.example:18443"
                )
                with pytest.raises(ClientError):
                    HttpClient(origin, server["cert"], confirm_synthetic=True)
            elif fault == "scope":
                with pytest.raises(ClientError) as error:
                    await asyncio.to_thread(
                        client.request, "/api/v1/agents" + SCOPE.replace("personal", "work")
                    )
                assert error.value.status == 403
            else:
                assert client.session
                if fault == "expired-file":
                    value = client.session.read()
                    value["expires_at"] = 0
                    client.session.write(value)
                elif fault == "unsafe-mode":
                    client.session.path.chmod(0o644)
                else:
                    target = tmp_path / "target.json"
                    client.session.path.rename(target)
                    client.session.path.symlink_to(target)
                with pytest.raises(ClientError):
                    client.bearer()

    asyncio.run(scenario())


def test_cli_login_command_persists_only_explicit_private_session(
    control: dict[str, Any], tmp_path: Path, capsys: Any, monkeypatch: Any
) -> None:
    import sys

    async def scenario() -> None:
        async with web_server(control, tmp_path) as server:
            session_path = tmp_path / "command-session.json"
            monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
            printed = []

            def authorize(prompt: str) -> str:
                text = capsys.readouterr().out
                printed.append(text)
                url = next(line for line in text.splitlines() if line.startswith("https://"))
                values = parse_qs(urlsplit(url).query)
                code = server["issuer"].authorize(
                    server["credentials"]["both"], values["code_challenge"][0], values["nonce"][0]
                )
                return str(code)

            monkeypatch.setattr("markos_runtime.client.cli.getpass.getpass", authorize)
            args = [
                "--origin",
                server["origin"],
                "--ca-file",
                str(server["cert"]),
                "--session-file",
                str(session_path),
                "--confirm-synthetic",
            ]
            assert await asyncio.to_thread(main, args + ["login"]) == 0
            value = SessionFile(session_path).read()
            text = "".join(printed) + capsys.readouterr().out
            assert value["bearer"] not in text and server["credentials"]["both"] not in text
            assert await asyncio.to_thread(main, args + ["me"]) == 0
            assert await asyncio.to_thread(main, args + ["logout"]) == 0
            assert not session_path.exists()

    asyncio.run(scenario())
