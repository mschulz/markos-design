"""Synthetic governance commands use only the same scoped HTTPS API and exact previews."""

import hmac
from typing import Any
from urllib.parse import urlencode
from uuid import UUID

from markos_runtime.client.http import ClientError, HttpClient
from markos_runtime.contracts.control import ApprovalDecisionRequest, digest_document
from markos_runtime.contracts.governance import (
    GovernedChatRequest,
    ReconciliationRequest,
    ReviewedFixtureGrant,
)


def execute(
    client: HttpClient, args: Any, owner: UUID, query: dict[str, str], load: Any, output: Any
) -> int:
    command = args.command

    def scoped(path: str) -> str:
        return path + "?" + urlencode(query)

    reads = {
        "tool-grant": "/api/v1/tools/grant",
        "tool-dispatches": "/api/v1/tool-dispatches",
        "tool-audit": "/api/v1/tools/audit",
    }
    if command in reads:
        if command != "tool-grant":
            if not 1 <= args.limit <= 100:
                raise ClientError("invalid-page-bound", 400)
            query["limit"] = str(args.limit)
            if args.cursor:
                query["cursor"] = args.cursor
        output(client.request(scoped(reads[command])))
        return 0
    models = {
        "tool-grant-preview": ReviewedFixtureGrant,
        "tool-grant-apply": ReviewedFixtureGrant,
        "tool-send": GovernedChatRequest,
        "approval-decide": ApprovalDecisionRequest,
        "tool-reconcile": ReconciliationRequest,
    }
    value = load(models[command], args.input)
    if hasattr(value, "owner_id") and value.owner_id != owner:
        raise ClientError("input-scope-mismatch", 400)
    if hasattr(value, "domain") and value.domain != args.domain:
        raise ClientError("input-scope-mismatch", 400)
    details: dict[str, Any] = {}
    if command.startswith("tool-grant-"):
        current = client.request(scoped("/api/v1/tools/grant"))
        if value.previous_revision != (current["revision"] if current else 0):
            raise ClientError("conflict", 409)
        details = current or {}
        path = "/api/v1/tools/grant"
    elif command == "approval-decide":
        path = "/api/v1/approvals/" + str(value.intent_id)
        details = client.request(scoped(path))
        if (
            details["intent_sha256"] != value.intent_sha256
            or details["revision"] != value.expected_revision
        ):
            raise ClientError("conflict", 409)
        path += "/decisions"
    elif command == "tool-reconcile":
        path = "/api/v1/tool-dispatches/" + str(value.dispatch_id)
        details = client.request(scoped(path))
        if details["revision"] != value.expected_revision:
            raise ClientError("conflict", 409)
        path += "/reconcile"
    else:
        path = "/api/v1/tool-requests"
    data = value.model_dump(mode="json")
    if command != "tool-send":
        preview = {"request": data, "current": details}
        digest = digest_document(preview)
        confirmed = getattr(args, "confirm_digest", None)
        if confirmed is None or command == "tool-grant-preview":
            output({"preview": preview, "digest": digest, "applied": False})
            return 0
        if not hmac.compare_digest(digest, confirmed):
            raise ClientError("preview-digest-mismatch", 409)
    output(client.request(scoped(path), value))
    return 0
