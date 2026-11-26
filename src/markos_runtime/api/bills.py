"""Authenticated bill imports and grounded answers through the shared API."""

import asyncio
import json
import re
from typing import Any
from uuid import UUID

from psycopg.types.json import Jsonb

from markos_runtime.api.application import ApiFault
from markos_runtime.knowledge.bills import (
    SAMPLE,
    BillImport,
    BillQuestion,
    BillRecord,
    answer,
    extract,
)
from markos_runtime.knowledge.objects import BillObjectError, BillObjects


class BillRoutes:
    def __init__(self, api: Any, objects: BillObjects) -> None:
        self.api, self.objects = api, objects

    async def handle(self, request: Any, body: bytes) -> Any:
        from fastapi.responses import JSONResponse  # type: ignore[import-not-found]

        api = self.api
        path, method = request.url.path, request.method
        params = api.query(request, {"owner_id", "domain"})
        owner, domain = api.scope(params)
        ctx, binding, raw = await api.authenticate(request)
        if method == "POST":
            await api.write(request, ctx, binding, raw)
        await api.store._call(
            "bill_access",
            ctx.session_id,
            ctx.lookup_hash,
            owner,
            domain,
            method == "POST" and path == "/api/v1/bills",
        )
        if path == "/api/v1/bills/sample" and method == "GET":
            return JSONResponse({"name": "Sample electricity bill", "text": SAMPLE})
        if path == "/api/v1/bills":
            if method == "GET":
                items = await api.store._call(
                    "list_bills", ctx.session_id, ctx.lookup_hash, owner, domain
                )
                return JSONResponse({"items": items})
            if method != "POST":
                raise ApiFault("not-found", 404)
            value = BillImport.from_wire(body)
            report = extract(value.text)
            try:
                digest = await asyncio.to_thread(
                    self.objects.put, owner, domain, value.text.encode()
                )
            except (BillObjectError, OSError):
                raise ApiFault("bill-source-unavailable", 503) from None
            result = await api.store._call(
                "save_bill",
                ctx.session_id,
                ctx.lookup_hash,
                owner,
                domain,
                value.name,
                digest,
                Jsonb(report.model_dump(mode="json")),
            )
            return JSONResponse(result, status_code=201)
        match = re.fullmatch(r"/api/v1/bills/([a-f0-9-]{36})(/source|/questions)?", path)
        if not match:
            raise ApiFault("not-found", 404)
        try:
            bill_id = UUID(match[1])
        except ValueError:
            raise ApiFault("invalid", 400) from None
        record = await api.store._call(
            "read_bill", ctx.session_id, ctx.lookup_hash, owner, domain, bill_id
        )
        bill = BillRecord.from_wire(json.dumps(record).encode())
        # Every result verifies the original; corrupt/missing evidence cannot produce an answer.
        try:
            original = await asyncio.to_thread(self.objects.read, owner, domain, bill.source_sha256)
        except (BillObjectError, OSError):
            raise ApiFault("bill-source-unavailable", 503) from None
        if bill.report != extract(original.decode()):
            raise ApiFault("bill-evidence-unavailable", 503)
        if match[2] == "/questions" and method == "POST":
            question = BillQuestion.from_wire(body)
            return JSONResponse(answer(bill.report, question.question))
        if method != "GET":
            raise ApiFault("not-found", 404)
        if match[2] == "/source":
            return JSONResponse(
                {"source_sha256": bill.source_sha256, "lines": original.decode().splitlines()}
            )
        if match[2]:
            raise ApiFault("not-found", 404)
        return JSONResponse(record)
