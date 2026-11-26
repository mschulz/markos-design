"""Bills from the owner's bill label: PDF attachments through the K1 PDF import.

The database queues stored messages in the bill label that have a PDF attachment. Each pass
reads a few of them, oldest first. Every PDF attachment gets one result: a report to save, an
unknown supplier, a full bill folder, or a rejection with a short reason. The database decides
whether a report is new or a duplicate by SHA-256. Attachment names and mail bodies are never read.
"""

import asyncio
import hashlib
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol
from uuid import UUID

from markos_runtime.knowledge.objects import BillObjects, BillStorageFull
from markos_runtime.knowledge.pdf import MAX_PDF_BYTES, PdfError, read_pages
from markos_runtime.knowledge.profiles import PDF_PROFILES
from markos_runtime.mail.gmail import Attachment, GmailReader, MessageGone

BATCH = 5
PDF = "application/pdf"


@dataclass(frozen=True)
class BillMessage:
    message_id: str
    received_at: datetime
    attachments: tuple[Attachment, ...]


class BillWork(Protocol):
    async def bills_due(self, fence: int, limit: int) -> list[BillMessage] | None: ...

    async def finish_bill(
        self, fence: int, message_id: str, results: Sequence[dict[str, Any]]
    ) -> list[str] | None: ...


def bill_name(received_at: datetime) -> str:
    return f"Gmail bill {received_at:%Y-%m-%d}"


async def _result(
    gmail: GmailReader,
    objects: BillObjects,
    owner: UUID,
    domain: str,
    message: BillMessage,
    part: Attachment,
) -> dict[str, Any]:
    rejected = {"part_id": part.part_id, "kind": "rejected"}
    if part.size > MAX_PDF_BYTES:
        return {**rejected, "reason": "too-large"}
    try:
        body = await gmail.attachment(message.message_id, part.attachment_id)
    except MessageGone:
        return {**rejected, "reason": "gone"}
    try:
        pages = await read_pages(body)
    except PdfError as error:
        return {**rejected, "reason": error.code}
    profile = next((p for p in PDF_PROFILES.values() if p[0](pages)), None)
    if profile is None:
        return {"part_id": part.part_id, "kind": "unsupported"}
    digest = hashlib.sha256(body).hexdigest()
    report = profile[1](pages, digest)
    try:
        await asyncio.to_thread(objects.put, owner, domain, body, ".pdf")
    except BillStorageFull:
        return {"part_id": part.part_id, "kind": "limit"}
    return {
        "part_id": part.part_id,
        "kind": "save",
        "source_sha256": digest,
        "name": bill_name(message.received_at),
        "report": report.model_dump(mode="json"),
    }


async def import_bills(
    gmail: GmailReader, work: BillWork, objects: BillObjects, fence: int, owner: UUID, domain: str
) -> list[str] | None:
    """One batch of queued bill messages. Returns each outcome, or None if the lease is gone."""
    due = await work.bills_due(fence, BATCH)
    if due is None:
        return None
    outcomes: list[str] = []
    for message in due:
        results = [
            await _result(gmail, objects, owner, domain, message, part)
            for part in message.attachments
            if part.mime_type == PDF
        ]
        done = await work.finish_bill(fence, message.message_id, results)
        if done is None:
            return None
        outcomes += done
    return outcomes
