"""One mail worker pass: renew the watch and sync when due.

Pub/Sub pushes Gmail alerts to the MarkOS API, which records them with `note_alert`. A
recorded alert makes the poll due now. The 15-minute backstop poll covers lost alerts.
"""

import json
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from markos_runtime.mail.gmail import GmailReader
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


class MailWork(MailRecords, Protocol):
    async def note_alert(self, history_id: int) -> bool: ...

    async def claim_work(self, lease_seconds: int) -> WorkClaim | None: ...

    async def finish_work(
        self, claim: WorkClaim, synced: bool, watch_expires: datetime | None
    ) -> bool: ...


@dataclass(frozen=True)
class PassResult:
    renewed: bool = False
    synced: SyncResult | None = None


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


async def run_once(gmail: GmailReader, work: MailWork) -> PassResult:
    claim = await work.claim_work(LEASE_SECONDS)
    if claim is None:
        return PassResult()
    expires: datetime | None = None
    try:
        if claim.renew:
            expires = (await gmail.watch(claim.labels)).expires_at
        result = await sync(gmail, work) if claim.sync else None
    except Exception:
        # Keep a finished renewal. The sync stays due and is retried after the lease.
        if expires is not None:
            await work.finish_work(claim, False, expires)
        raise
    await work.finish_work(claim, claim.sync, expires)
    return PassResult(claim.renew, result)
