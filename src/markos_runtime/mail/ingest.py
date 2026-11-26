"""One sync pass: read Gmail changes since the stored cursor and store allowlisted messages once.

A lost cursor, or a mailbox with no cursor yet, starts a bounded resync: only the allowlisted
labels, back to BACKFILL_FROM. The new cursor is read before listing, so changes made during
the resync are read again by the next pass. Stored messages are keyed by ID, so repeats are
harmless.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from typing import Literal, Protocol

from markos_runtime.mail.gmail import GmailReader, HistoryGone, MessageGone, MessageMeta

BACKFILL_FROM = date(2020, 1, 1)
BATCH = 100

Reason = Literal["first-import", "history-lost"]


class MailError(Exception):
    """Stable mail sync failure. Never carries addresses, subjects or message content."""


@dataclass(frozen=True)
class Mailbox:
    labels: tuple[str, ...]
    history_id: int | None
    connected: bool = True


@dataclass(frozen=True)
class SyncResult:
    kind: Literal["incremental", "first-import", "history-lost", "stale"]
    stored: int


class MailRecords(Protocol):
    async def mailbox(self) -> Mailbox | None: ...

    async def store(self, messages: Sequence[MessageMeta]) -> int: ...

    async def advance(
        self, start: int | None, end: int, messages: Sequence[MessageMeta], reason: Reason | None
    ) -> int | None: ...


async def sync(gmail: GmailReader, records: MailRecords) -> SyncResult:
    box = await records.mailbox()
    if box is None or not box.connected:
        raise MailError("no connected mailbox")
    if box.history_id is None:
        return await _resync(gmail, records, box, "first-import")
    ids: list[str] = []
    page: str | None = None
    while True:
        try:
            result = await gmail.history(box.history_id, page)
        except HistoryGone:
            return await _resync(gmail, records, box, "history-lost")
        ids += [c.message_id for c in result.changes if set(c.label_ids) & set(box.labels)]
        end, page = result.history_id, result.next_page
        if page is None:
            break
    messages = await _fetch(gmail, ids, box.labels)
    # Earlier pages go first without the cursor. The last page and the cursor commit together.
    cut = max(len(messages) - BATCH, 0)
    stored = 0
    for start in range(0, cut, BATCH):
        stored += await records.store(messages[start : min(start + BATCH, cut)])
    final = await records.advance(box.history_id, end, messages[cut:], None)
    if final is None:
        return SyncResult("stale", stored)
    return SyncResult("incremental", stored + final)


async def _resync(
    gmail: GmailReader, records: MailRecords, box: Mailbox, reason: Reason
) -> SyncResult:
    end = await gmail.current_history_id()
    ids: list[str] = []
    for label in box.labels:
        page: str | None = None
        while True:
            listing = await gmail.list_messages(label, BACKFILL_FROM, page)
            ids += listing.message_ids
            if (page := listing.next_page) is None:
                break
    messages = await _fetch(gmail, ids, box.labels)
    stored = 0
    for start in range(0, len(messages), BATCH):
        stored += await records.store(messages[start : start + BATCH])
    final = await records.advance(box.history_id, end, [], reason)
    return SyncResult("stale" if final is None else reason, stored)


async def _fetch(
    gmail: GmailReader, ids: Sequence[str], labels: Sequence[str]
) -> list[MessageMeta]:
    """Fetch each named message once. Only IDs already matched to the allowlist reach here."""
    found: list[MessageMeta] = []
    for message_id in dict.fromkeys(ids):
        try:
            meta = await gmail.message(message_id)
        except MessageGone:
            continue
        if set(meta.label_ids) & set(labels):
            found.append(meta)
    return found
