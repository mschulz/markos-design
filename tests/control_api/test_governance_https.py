"""End-to-end shared HTTPS approval and pinned MCP fixture execution/failure evidence."""

import asyncio
import importlib.util
import os
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import psycopg
import pytest

from markos_runtime.contracts.governance import ReviewedFixtureGrant
from markos_runtime.store.database import DurableStore
from markos_runtime.tools.transport import FixtureConnection, FixtureTransport
from markos_runtime.tools.worker import GovernedConversationWorker
from tests.control_api.test_api import SCOPE
from tests.control_api.test_conversation import conversation, handoff, request_value, rows
from tests.integration.conftest import OWNER
from tests.web_support import WebBrowserClient, web_server

if importlib.util.find_spec("mcp") is None:
    if os.environ.get("MARKOS_REQUIRE_TOOLS_TESTS") == "1":
        pytest.fail("required pinned MCP profile missing")
    pytest.skip("optional MCP profile not installed", allow_module_level=True)

from tests.tools_support import tool_server

__all__ = ["conversation"]


@pytest.mark.parametrize(
    "fault",
    [None, "response-loss", "scope", "schema", "audit-before", "audit-result", "upstream-revoked"],
)
def test_shared_api_durable_wait_resume_and_uncertain_outcomes(
    conversation: dict[str, Any], tmp_path: Path, fault: str | None
) -> None:
    async def scenario() -> None:
        async with (
            web_server(conversation, tmp_path, governance=True) as server,
            tool_server(tmp_path / "tool", UUID(OWNER), "personal") as remote,
            DurableStore.connect(conversation["chatworker"]) as runtime,
        ):
            operator = WebBrowserClient(server["port"], server["cert"])
            await operator.login(server["issuer"], server["credentials"]["both"])
            grant = ReviewedFixtureGrant(
                schema_version=1,
                owner_id=UUID(OWNER),
                domain="personal",
                grant_id=uuid4(),
                revision=1,
                previous_revision=0,
                endpoint=remote["endpoint"],
                enabled=True,
                reason="Explicit fixture review",
            )
            assert (
                await operator.call(
                    "POST", "/api/v1/tools/grant" + SCOPE, grant.model_dump(mode="json")
                )
            )[0] == 200
            status, page = await operator.call("GET", "/api/v1/conversations" + SCOPE)
            assert status == 200
            conv = UUID(page["items"][0]["conversation"]["conversation_id"])
            request = {
                **request_value(conv),
                "fixture_arguments": {"schema_version": 1, "label": "Synthetic test read"},
            }
            status, receipt = await operator.call("POST", "/api/v1/tool-requests" + SCOPE, request)
            assert status == 202, receipt
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
                runtime, server["issuer"], transport=transport, confirm_synthetic=True
            )
            assert await worker.run_once()
            assert rows(
                conversation,
                "SELECT count(*) FROM markos_runtime.execution_slots WHERE task_id IS NOT NULL",
            ) == [(0,)]
            status, view = await operator.call("GET", "/api/v1/approvals" + SCOPE)
            assert status == 200 and len(view["items"]) == 1
            item = view["items"][0]
            assert item["intent"]["arguments"] == request["fixture_arguments"]
            assert item["endpoint"] == remote["endpoint"]
            decision = {
                "schema_version": 1,
                "intent_id": item["intent_id"],
                "intent_sha256": item["intent_sha256"],
                "expected_revision": item["revision"],
                "decision": "approve",
            }
            path = "/api/v1/approvals/" + item["intent_id"] + "/decisions" + SCOPE
            assert (await operator.call("POST", path, decision))[0] == 403
            approver = WebBrowserClient(server["port"], server["cert"])
            await approver.login(server["issuer"], server["credentials"]["approver"])
            assert (await approver.call("POST", path, decision))[0] == 200
            if fault in {"audit-before", "audit-result"}:
                transition = "dispatched" if fault == "audit-before" else "succeeded"
                with psycopg.connect(conversation["migration"], autocommit=True) as conn:
                    conn.execute(
                        "CREATE FUNCTION markos_control.fault_audit() RETURNS trigger "
                        "LANGUAGE plpgsql AS $$BEGIN IF NEW.outcome='" + transition + "' THEN "
                        "RAISE EXCEPTION 'synthetic audit fault'; END IF; RETURN NEW;END $$"
                    )
                    conn.execute(
                        "CREATE TRIGGER audit_fault BEFORE INSERT ON markos_control.tool_audit "
                        "FOR EACH ROW EXECUTE FUNCTION markos_control.fault_audit()"
                    )
            elif fault == "upstream-revoked":
                for sid, (subject, _) in list(server["issuer"]._sessions.items()):
                    if subject == "both":
                        server["issuer"].revoke(sid)
            else:
                remote["state"]["fault"] = fault
            # A new object has no in-memory approval waiter, history or authority cache.
            worker = GovernedConversationWorker(
                runtime, server["issuer"], transport=transport, confirm_synthetic=True
            )
            completed = await worker.run_once()
            if fault is None:
                assert completed
                assert rows(conversation, "SELECT state FROM markos_control.chat_runs") == [
                    ("succeeded",)
                ]
                assert rows(
                    conversation,
                    "SELECT count(*) FROM markos_control.messages WHERE author_kind='test-agent'",
                ) == [(1,)]
                assert remote["state"]["methods"] == ["server/discover", "tools/list", "tools/call"]
            elif fault in {"schema", "audit-before", "upstream-revoked"}:
                assert not completed
                assert remote["state"]["operations"] == []
                assert rows(
                    conversation, "SELECT count(*) FROM markos_control.tool_dispatches"
                ) == [(0,)]
            else:
                assert not completed
                assert remote["state"]["ledger"] == {"Synthetic test read": 1}
                assert rows(
                    conversation, "SELECT state,reserved FROM markos_control.tool_dispatches"
                ) == [("unknown", True)]
                assert rows(
                    conversation, "SELECT operation_id FROM markos_control.tool_dispatches"
                )[0][0] == UUID(remote["state"]["operation_ids"][0])
                assert not await worker.run_once()
                assert remote["state"]["ledger"] == {"Synthetic test read": 1}
                status, attempts = await approver.call("GET", "/api/v1/tool-dispatches" + SCOPE)
                assert status == 200
                attempt = attempts["items"][0]
                reconciliation = {
                    "schema_version": 1,
                    "dispatch_id": attempt["dispatch_id"],
                    "expected_revision": attempt["revision"],
                    "outcome": "observed-succeeded",
                    "evidence": "synthetic-ledger-reviewed",
                }
                assert (
                    await approver.call(
                        "POST",
                        "/api/v1/tool-dispatches/" + attempt["dispatch_id"] + "/reconcile" + SCOPE,
                        reconciliation,
                    )
                )[0] == 200
                assert rows(
                    conversation, "SELECT state,reserved FROM markos_control.tool_dispatches"
                ) == [("resolved", False)]
                assert not await worker.run_once()
                assert remote["state"]["ledger"] == {"Synthetic test read": 1}

    asyncio.run(scenario())
