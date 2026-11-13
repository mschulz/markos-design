"""Shared authenticated synthetic governance routes; decisions never dispatch from HTTP."""

import json
import re
import time
from typing import Any
from uuid import UUID

from psycopg.types.json import Jsonb

from markos_runtime.api.application import ApiFault
from markos_runtime.contracts.control import ApprovalDecisionRequest
from markos_runtime.contracts.governance import (
    GovernanceAudit,
    GovernedApproval,
    GovernedChatRequest,
    GovernedDispatch,
    ReconciliationRequest,
    ReviewedFixtureGrant,
)


class GovernanceRoutes:
    def __init__(self, api: Any) -> None:
        self.api = api

    @staticmethod
    def accepts(path: str) -> bool:
        return (
            path
            in {
                "/api/v1/approvals",
                "/api/v1/tool-requests",
                "/api/v1/tools/grant",
                "/api/v1/tools/audit",
                "/api/v1/tool-dispatches",
            }
            or path.startswith("/api/v1/approvals/")
            or path.startswith("/api/v1/tool-dispatches/")
        )

    async def handle(self, request: Any, body: bytes) -> Any:
        from fastapi.responses import JSONResponse  # type: ignore[import-not-found]

        api, path, method = self.api, request.url.path, request.method
        ctx, binding, raw = await api.authenticate(request)
        params = api.query(request, {"owner_id", "domain", "cursor", "limit"})
        owner, domain = api.scope(params)
        if method == "POST":
            if set(params) != {"owner_id", "domain"}:
                raise ApiFault("invalid", 400)
            await api.write(request, ctx, binding, raw)
        if path == "/api/v1/tools/grant":
            if method == "GET":
                value = await api.store._call(
                    "read_tool_grant", ctx.session_id, ctx.lookup_hash, owner, domain
                )
                return JSONResponse(value)
            value = ReviewedFixtureGrant.from_wire(body)
            if value.owner_id != owner or value.domain != domain:
                raise ApiFault("invalid", 400)
            result = await api.store._call(
                "save_tool_grant",
                ctx.session_id,
                ctx.lookup_hash,
                Jsonb(value.model_dump(mode="json")),
            )
            return JSONResponse(result)
        if path == "/api/v1/tool-requests" and method == "POST":
            value = GovernedChatRequest.from_wire(body)
            if value.domain != domain:
                raise ApiFault("invalid", 400)
            result = await api.store._call(
                "admit_governed_message",
                ctx.session_id,
                ctx.lookup_hash,
                owner,
                domain,
                Jsonb(value.model_dump(mode="json")),
            )
            if "denied" in result:
                raise ApiFault(
                    "limited" if result["denied"] == "budget" else "forbidden",
                    429 if result["denied"] == "budget" else 403,
                )
            return JSONResponse(result, status_code=202)
        one = re.fullmatch(
            r"/api/v1/(approvals|tool-dispatches)/([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})",
            path,
        )
        if one and method == "GET":
            if set(params) != {"owner_id", "domain"}:
                raise ApiFault("invalid", 400)
            result = await api.store._call(
                "read_tool_object",
                ctx.session_id,
                ctx.lookup_hash,
                owner,
                domain,
                "approvals" if one[1] == "approvals" else "dispatches",
                UUID(one[2]),
            )
            model: Any = GovernedApproval if one[1] == "approvals" else GovernedDispatch
            return JSONResponse(
                model.from_wire(json.dumps(result).encode()).model_dump(mode="json")
            )
        decision = re.fullmatch(
            r"/api/v1/approvals/([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})/decisions",
            path,
        )
        if decision and method == "POST":
            value = ApprovalDecisionRequest.from_wire(body)
            if value.intent_id != UUID(decision[1]):
                raise ApiFault("invalid", 400)
            await api.store._call(
                "read_tool_object",
                ctx.session_id,
                ctx.lookup_hash,
                owner,
                domain,
                "approvals",
                value.intent_id,
            )
            result = await api.store._call(
                "decide_tool",
                ctx.session_id,
                ctx.lookup_hash,
                owner,
                domain,
                value.intent_id,
                Jsonb(value.model_dump(mode="json")),
            )
            return JSONResponse(result)
        reconciliation = re.fullmatch(
            r"/api/v1/tool-dispatches/([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})/reconcile",
            path,
        )
        if reconciliation and method == "POST":
            value = ReconciliationRequest.from_wire(body)
            if value.dispatch_id != UUID(reconciliation[1]):
                raise ApiFault("invalid", 400)
            result = await api.store._call(
                "reconcile_tool",
                ctx.session_id,
                ctx.lookup_hash,
                owner,
                domain,
                Jsonb(value.model_dump(mode="json")),
            )
            return JSONResponse(result)
        kinds = {
            "/api/v1/approvals": "approvals",
            "/api/v1/tool-dispatches": "dispatches",
            "/api/v1/tools/audit": "audit",
        }
        if path not in kinds or method != "GET":
            raise ApiFault("not-found", 404)
        head = await api.store._call(
            "activity_head", ctx.session_id, ctx.lookup_hash, owner, domain
        )
        after, limit = 0, params.get("limit", "50")
        if (
            len(limit) > 3
            or not limit.isascii()
            or not limit.isdigit()
            or not 1 <= int(limit) <= 100
        ):
            raise ApiFault("invalid", 400)
        expected = {
            "resource": kinds[path],
            "principal_id": binding["principal_id"],
            "owner_id": str(owner),
            "domain": domain,
            "epoch": head["epoch"],
        }
        if params.get("cursor"):
            try:
                value = json.loads(api.codec.open(params["cursor"]))
                if (
                    set(value) != {*expected, "after", "expires"}
                    or any(value[k] != v for k, v in expected.items())
                    or type(value["after"]) is not int
                    or not 0 <= value["after"] <= 9223372036854775807
                    or type(value["expires"]) is not int
                ):
                    raise ValueError
                if value["expires"] <= time.time():
                    raise ApiFault("resync-required", 409)
                after = value["after"]
            except ApiFault:
                raise
            except Exception:
                raise ApiFault("invalid", 400) from None
        items = await api.store._call(
            "read_tool_records",
            ctx.session_id,
            ctx.lookup_hash,
            owner,
            domain,
            kinds[path],
            after,
            int(limit),
        )
        next_after = (
            (items[-1]["sequence"] if kinds[path] == "audit" else after + len(items))
            if items
            else after
        )
        model = {
            "approvals": GovernedApproval,
            "dispatches": GovernedDispatch,
            "audit": GovernanceAudit,
        }[kinds[path]]
        items = [
            model.from_wire(json.dumps({"schema_version": 1, **item}).encode()).model_dump(
                mode="json"
            )
            for item in items
        ]
        token = api.codec.seal(
            json.dumps({**expected, "after": next_after, "expires": int(time.time()) + 600})
        )
        return JSONResponse(
            {"schema_version": 1, "available": True, "items": items, "next_cursor": token}
        )
