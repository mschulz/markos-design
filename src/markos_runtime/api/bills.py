"""Authenticated bill imports and grounded answers through the shared API."""

import asyncio
import hashlib
import json
import re
from collections.abc import Callable
from typing import Any
from uuid import UUID

from psycopg.types.json import Jsonb

from markos_runtime.api.application import ApiFault
from markos_runtime.knowledge.amber import extract_amber, is_amber
from markos_runtime.knowledge.bills import (
    SAMPLE,
    BillFacts,
    BillImport,
    BillQuestion,
    BillRecord,
    answer,
    extract,
    plain_text,
)
from markos_runtime.knowledge.corrections import CorrectionRequest, effective, normalise
from markos_runtime.knowledge.history import answer as history_answer
from markos_runtime.knowledge.history import history, row
from markos_runtime.knowledge.objects import BillObjectError, BillObjects
from markos_runtime.knowledge.origin import extract_origin, is_origin
from markos_runtime.knowledge.pdf import PdfError, read_pages

PDF_PATH = "/api/v1/bills/pdf"
HISTORY_PATH = "/api/v1/bills/history"

# Supplier PDF profiles by extractor name: (recognises the pages, extracts the facts).
PDF_PROFILES: dict[
    str, tuple[Callable[[list[str]], bool], Callable[[list[str], str], BillFacts]]
] = {
    "amber-pdf-v1": (is_amber, extract_amber),
    "origin-pdf-v1": (is_origin, extract_origin),
}


def present(record: dict[str, Any]) -> dict[str, Any]:
    """A stored bill plus its effective view: corrections applied, totals checked again."""
    bill = BillRecord.from_wire(json.dumps(record).encode())
    view = effective(bill.report, bill.corrections)
    return {**record, "effective": view.model_dump(mode="json")}


class BillRoutes:
    def __init__(self, api: Any, objects: BillObjects) -> None:
        self.api, self.objects = api, objects

    async def handle(self, request: Any, body: bytes) -> Any:
        from fastapi.responses import JSONResponse  # type: ignore[import-not-found]

        api = self.api
        path, method = request.url.path, request.method
        params = api.query(
            request, {"owner_id", "domain", "name"} if path == PDF_PATH else {"owner_id", "domain"}
        )
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
            method == "POST"
            and (path in {"/api/v1/bills", PDF_PATH} or path.endswith("/corrections")),
        )
        if path == PDF_PATH:
            if method != "POST":
                raise ApiFault("not-found", 404)
            return await self.import_pdf(ctx, owner, domain, params.get("name", ""), body)
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
            return JSONResponse(present(result), status_code=201)
        if path == HISTORY_PATH and method == "GET":
            return JSONResponse(await self.history(ctx, owner, domain))
        if path == HISTORY_PATH + "/questions" and method == "POST":
            question = BillQuestion.from_wire(body)
            view = await self.history(ctx, owner, domain)
            return JSONResponse(history_answer(view, question.question))
        match = re.fullmatch(
            r"/api/v1/bills/([a-f0-9-]{36})(/source|/questions|/corrections)?", path
        )
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
        pdf = bill.report.extractor in PDF_PROFILES
        try:
            original = await asyncio.to_thread(
                self.objects.read, owner, domain, bill.source_sha256, ".pdf" if pdf else ".txt"
            )
        except (BillObjectError, OSError):
            raise ApiFault("bill-source-unavailable", 503) from None
        pages: list[str] = []
        if pdf:
            try:
                pages = await read_pages(original)
            except PdfError:
                raise ApiFault("bill-evidence-unavailable", 503) from None
        current = (
            PDF_PROFILES[bill.report.extractor][1](pages, bill.source_sha256)
            if pdf
            else extract(original.decode())
        )
        if bill.report != current:
            raise ApiFault("bill-evidence-unavailable", 503)
        if match[2] == "/questions" and method == "POST":
            question = BillQuestion.from_wire(body)
            return JSONResponse(answer(effective(bill.report, bill.corrections), question.question))
        if match[2] == "/corrections" and method == "POST":
            change = CorrectionRequest.from_wire(body)
            if change.key not in {f.key for f in bill.report.facts}:
                raise ApiFault("invalid", 400)
            try:
                corrected = None if change.value is None else normalise(change.key, change.value)
            except ValueError:
                raise ApiFault("invalid", 400) from None
            result = await api.store._call(
                "correct_bill",
                ctx.session_id,
                ctx.lookup_hash,
                owner,
                domain,
                bill_id,
                change.key,
                corrected,
                change.reason.strip(),
            )
            return JSONResponse(present(result), status_code=201)
        if method != "GET":
            raise ApiFault("not-found", 404)
        if match[2] == "/source" and pdf:
            return JSONResponse(
                {
                    "source_sha256": bill.source_sha256,
                    "pages": [
                        {"page": n, "lines": page.splitlines()} for n, page in enumerate(pages, 1)
                    ],
                }
            )
        if match[2] == "/source":
            return JSONResponse(
                {"source_sha256": bill.source_sha256, "lines": original.decode().splitlines()}
            )
        if match[2]:
            raise ApiFault("not-found", 404)
        return JSONResponse(present(record))

    async def history(self, ctx: Any, owner: UUID, domain: str) -> dict[str, Any]:
        """All bills in period order. Each original must pass its integrity check.

        The history does not parse every PDF again: one request may cover 500 bills. A bill whose
        original is missing or changed is listed without figures. The bill page still rechecks its
        report against the original in full.
        """
        records = await self.api.store._call(
            "bill_history", ctx.session_id, ctx.lookup_hash, owner, domain
        )
        rows = []
        for record in records:
            bill = BillRecord.from_wire(json.dumps(record).encode())
            suffix = ".pdf" if bill.report.extractor in PDF_PROFILES else ".txt"
            try:
                await asyncio.to_thread(
                    self.objects.read, owner, domain, bill.source_sha256, suffix
                )
                view: BillFacts | None = effective(bill.report, bill.corrections)
            except (BillObjectError, OSError):
                view = None
            rows.append(row(bill.bill_id, bill.name, view))
        return history(rows)

    async def import_pdf(self, ctx: Any, owner: UUID, domain: str, name: str, body: bytes) -> Any:
        from fastapi.responses import JSONResponse

        api = self.api
        try:
            name = plain_text(name)
        except ValueError:
            raise ApiFault("invalid", 400) from None
        if len(name) > 120:
            raise ApiFault("invalid", 400)
        try:
            pages = await read_pages(body)
        except PdfError as error:
            raise ApiFault(
                "bill-pdf-" + error.code, 413 if error.code == "too-large" else 422
            ) from None
        profile = next((p for p in PDF_PROFILES.values() if p[0](pages)), None)
        if profile is None:
            raise ApiFault("bill-pdf-unsupported-supplier", 422)
        report: BillFacts = profile[1](pages, hashlib.sha256(body).hexdigest())
        try:
            digest = await asyncio.to_thread(self.objects.put, owner, domain, body, ".pdf")
        except (BillObjectError, OSError):
            raise ApiFault("bill-source-unavailable", 503) from None
        result = await api.store._call(
            "save_bill",
            ctx.session_id,
            ctx.lookup_hash,
            owner,
            domain,
            name,
            digest,
            Jsonb(report.model_dump(mode="json")),
        )
        return JSONResponse(present(result), status_code=201)
