"""Exact CLI previews and mutations share the browser API and cannot bypass it."""

import asyncio
import json
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from markos_runtime.api.identity import FixtureIssuer
from markos_runtime.client.cli import main
from markos_runtime.contracts.governance import ReviewedFixtureGrant
from markos_runtime.control.conversation import ConversationWorker
from markos_runtime.store.database import DurableStore
from tests.control_api.test_api import SCOPE
from tests.control_api.test_conversation import conversation, handoff, request_value, rows
from tests.control_api.test_governance import claim
from tests.control_api.test_web_cli import cli_client
from tests.integration.conftest import OWNER
from tests.web_support import web_server

__all__ = ["conversation"]


def test_cli_grant_preview_send_and_exact_approval(
    conversation: dict[str, Any], tmp_path: Path, capsys: Any
) -> None:
    async def scenario() -> None:
        async with (
            web_server(conversation, tmp_path, governance=True) as server,
            DurableStore.connect(conversation["chatworker"]) as runtime,
        ):
            operator = await cli_client(server, tmp_path, "both")
            assert operator.session is not None
            flags = [
                "--origin",
                server["origin"],
                "--ca-file",
                str(server["cert"]),
                "--session-file",
                str(operator.session.path),
                "--confirm-synthetic",
            ]
            scope = ["--owner-id", OWNER, "--domain", "personal"]
            grant = ReviewedFixtureGrant(
                schema_version=1,
                owner_id=UUID(OWNER),
                domain="personal",
                grant_id=uuid4(),
                revision=1,
                previous_revision=0,
                endpoint="https://127.0.0.1:19443/mcp",
                enabled=True,
                reason="Explicit CLI fixture review",
            )
            path = tmp_path / "grant.json"
            path.write_bytes(grant.to_wire())
            capsys.readouterr()
            assert (
                await asyncio.to_thread(
                    main, flags + ["tool-grant-preview", *scope, "--input", str(path)]
                )
                == 0
            )
            preview = json.loads(capsys.readouterr().out)
            assert preview["applied"] is False
            assert rows(conversation, "SELECT count(*) FROM markos_control.tool_grants") == [(0,)]
            assert (
                await asyncio.to_thread(
                    main,
                    flags
                    + [
                        "tool-grant-apply",
                        *scope,
                        "--input",
                        str(path),
                        "--confirm-digest",
                        preview["digest"],
                    ],
                )
                == 0
            )
            capsys.readouterr()
            conversations = await asyncio.to_thread(
                operator.request, "/api/v1/conversations" + SCOPE
            )
            conv = UUID(conversations["items"][0]["conversation"]["conversation_id"])
            request = {
                **request_value(conv),
                "fixture_arguments": {"schema_version": 1, "label": "Synthetic CLI request"},
            }
            bodyfile = tmp_path / "tool-request.json"
            bodyfile.write_text(json.dumps(request))
            assert (
                await asyncio.to_thread(
                    main, flags + ["tool-send", *scope, "--input", str(bodyfile)]
                )
                == 0
            )
            receipt = json.loads(capsys.readouterr().out)
            await handoff(conversation, receipt)
            worker = ConversationWorker(runtime, FixtureIssuer(), confirm_synthetic=True)
            _, identity = await claim(worker)
            await worker._call("wait_tool", *identity)
            approver = await cli_client(server, tmp_path, "approver")
            assert approver.session is not None
            items = await asyncio.to_thread(approver.request, "/api/v1/approvals" + SCOPE)
            item = items["items"][0]
            decision = {
                "schema_version": 1,
                "intent_id": item["intent_id"],
                "intent_sha256": item["intent_sha256"],
                "expected_revision": item["revision"],
                "decision": "approve",
            }
            path = tmp_path / "decision.json"
            path.write_text(json.dumps(decision))
            flags[flags.index("--session-file") + 1] = str(approver.session.path)
            assert (
                await asyncio.to_thread(
                    main, flags + ["approval-decide", *scope, "--input", str(path)]
                )
                == 0
            )
            preview = json.loads(capsys.readouterr().out)
            assert (
                preview["preview"]["current"]["intent"]["arguments"] == request["fixture_arguments"]
            )
            assert preview["preview"]["current"]["endpoint"] == grant.endpoint
            assert rows(conversation, "SELECT status FROM markos_control.tool_intents") == [
                ("awaiting",)
            ]
            assert (
                await asyncio.to_thread(
                    main,
                    flags
                    + [
                        "approval-decide",
                        *scope,
                        "--input",
                        str(path),
                        "--confirm-digest",
                        preview["digest"],
                    ],
                )
                == 0
            )
            assert rows(conversation, "SELECT status FROM markos_control.tool_intents") == [
                ("approved",)
            ]
            output = capsys.readouterr()
            assert approver.bearer() not in output.out + output.err

    asyncio.run(scenario())
