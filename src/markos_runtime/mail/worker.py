"""One mail worker pass: renew the watch and sync when due.

Pub/Sub pushes Gmail alerts to the MarkOS API, which records them with `note_alert`. A
recorded alert makes the poll due now. The 15-minute backstop poll covers lost alerts.
After the owner disconnects, one pass stops the watch, revokes the token and deletes it. A
failed attempt is retried after the lease. After 1 day the database deletes the token anyway.
A worker with a bill folder also imports queued bills, a few messages in each pass.
"""

import json
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from markos_runtime.knowledge.objects import BillObjects
from markos_runtime.mail.bills import BillWork, import_bills
from markos_runtime.mail.gmail import GmailAccess, SealedToken
from markos_runtime.mail.ingest import MailRecords, SyncResult, sync

MAX_ALERT_BYTES = 1024
LEASE_SECONDS = 120


@dataclass(frozen=True)
class WorkClaim:
    fence: int
    renew: bool
    sync: bool
    alert_seq: int
    labels: tuple[str, ...]
    disconnect: bool = False
    token: SealedToken | None = None
    bills: bool = False


class MailWork(MailRecords, BillWork, Protocol):
    async def note_alert(self, history_id: int) -> bool: ...

    async def claim_work(self, lease_seconds: int, bills: bool = False) -> WorkClaim | None: ...

    async def finish_work(
        self, claim: WorkClaim, synced: bool, watch_expires: datetime | None
    ) -> bool: ...

    async def finish_disconnect(self, claim: WorkClaim) -> bool: ...


@dataclass(frozen=True)
class PassResult:
    renewed: bool = False
    synced: SyncResult | None = None
    disconnected: bool = False
    bills: tuple[str, ...] = ()


def alert_history(data: bytes) -> int | None:
    """The history ID of a Gmail alert, or None if malformed. The address is never read."""
    if len(data) > MAX_ALERT_BYTES:
        return None
    try:
        document = json.loads(data)
    except (ValueError, UnicodeDecodeError):
        return None
    value = document.get("historyId") if isinstance(document, dict) else None
    if isinstance(value, str) and value.isascii() and value.isdigit() and len(value) <= 20:
        value = int(value)
    if type(value) is not int or not 0 < value < 2**64:
        return None
    return value


async def run_once(
    access: GmailAccess, work: MailWork, objects: BillObjects | None = None
) -> PassResult:
    claim = await work.claim_work(LEASE_SECONDS, objects is not None)
    if claim is None:
        return PassResult()
    gmail = access.client(claim.token)
    if claim.disconnect:
        await gmail.stop()
        await gmail.revoke()
        return PassResult(disconnected=await work.finish_disconnect(claim))
    expires: datetime | None = None
    bills: list[str] | None = []
    try:
        if claim.renew:
            expires = (await gmail.watch(claim.labels)).expires_at
        result = await sync(gmail, work) if claim.sync else None
        if claim.bills and objects is not None and claim.token is not None:
            owner, domain = claim.token.owner_id, claim.token.domain
            bills = await import_bills(gmail, work, objects, claim.fence, owner, domain)
    except Exception:
        # Keep a finished renewal. The sync stays due and is retried after the lease.
        if expires is not None:
            await work.finish_work(claim, False, expires)
        raise
    await work.finish_work(claim, claim.sync, expires)
    return PassResult(claim.renew, result, bills=tuple(bills or ()))
