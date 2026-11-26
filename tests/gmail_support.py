"""In-memory Gmail with invented mail. Models history IDs, paging and lost history."""

from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, date, datetime, timedelta

from markos_runtime.mail.gmail import (
    Attachment,
    Change,
    HistoryGone,
    HistoryPage,
    ListPage,
    MessageGone,
    MessageMeta,
    SealedToken,
    Watch,
)
from markos_runtime.mail.ingest import Mailbox, Reason
from markos_runtime.mail.worker import WorkClaim

PDF = Attachment("1", "ANGjdJ_synthetic", "application/pdf", 2048)


@dataclass
class FakeGmail:
    page_size: int = 2
    history_id: int = 1000
    oldest: int = 1000
    messages: dict[str, MessageMeta] = field(default_factory=dict)
    changes: list[tuple[int, Change]] = field(default_factory=list)
    fetched: Counter[str] = field(default_factory=Counter)
    watched: list[tuple[str, ...]] = field(default_factory=list)
    tokens: list[SealedToken | None] = field(default_factory=list)
    stopped: int = 0
    revoked: int = 0
    unreachable: bool = False

    def deliver(
        self,
        labels: Sequence[str],
        *,
        received: date = date(2024, 3, 1),
        attachments: Sequence[Attachment] = (),
    ) -> str:
        self.history_id += 1
        message_id = f"{self.history_id:016x}"
        when = datetime.combine(received, datetime.min.time(), UTC) + timedelta(hours=9)
        meta = MessageMeta(
            message_id, message_id, self.history_id, when, tuple(labels), tuple(attachments)
        )
        self.messages[message_id] = meta
        self.changes.append((self.history_id, Change(message_id, tuple(labels))))
        return message_id

    def relabel(self, message_id: str, labels: Sequence[str]) -> None:
        self.history_id += 1
        meta = replace(
            self.messages[message_id], label_ids=tuple(labels), history_id=self.history_id
        )
        self.messages[message_id] = meta
        self.changes.append((self.history_id, Change(message_id, tuple(labels))))

    def delete(self, message_id: str) -> None:
        self.history_id += 1
        del self.messages[message_id]

    def forget_history(self) -> None:
        """Gmail keeps history for a limited time. Older start IDs then return HTTP 404."""
        self.oldest = self.history_id

    async def current_history_id(self) -> int:
        return self.history_id

    async def history(self, start: int, page: str | None) -> HistoryPage:
        if start < self.oldest:
            raise HistoryGone
        found = [c for hid, c in self.changes if hid > start]
        offset = int(page or 0)
        chunk = found[offset : offset + self.page_size]
        more = offset + self.page_size < len(found)
        return HistoryPage(
            tuple(chunk), self.history_id, str(offset + self.page_size) if more else None
        )

    async def list_messages(self, label_id: str, after: date, page: str | None) -> ListPage:
        found = [
            m.message_id
            for m in sorted(self.messages.values(), key=lambda m: m.received_at, reverse=True)
            if label_id in m.label_ids and m.received_at.date() > after
        ]
        offset = int(page or 0)
        more = offset + self.page_size < len(found)
        chunk = tuple(found[offset : offset + self.page_size])
        return ListPage(chunk, str(offset + self.page_size) if more else None)

    async def message(self, message_id: str) -> MessageMeta:
        self.fetched[message_id] += 1
        if message_id not in self.messages:
            raise MessageGone
        return self.messages[message_id]

    async def watch(self, label_ids: Sequence[str]) -> Watch:
        self.watched.append(tuple(label_ids))
        return Watch(self.history_id, datetime.now(UTC) + timedelta(days=7))

    def client(self, token: SealedToken | None) -> "FakeGmail":
        self.tokens.append(token)
        return self

    async def stop(self) -> None:
        if self.unreachable:
            raise OSError("Gmail unreachable")
        self.stopped += 1

    async def revoke(self) -> None:
        if self.unreachable:
            raise OSError("Gmail unreachable")
        self.revoked += 1

    def alert(self) -> bytes:
        """The Pub/Sub payload Gmail sends. The address is invented."""
        return b'{"emailAddress": "owner@example.invalid", "historyId": %d}' % self.history_id


@dataclass
class MemoryRecords:
    """Mirrors the migration 0019 rules: keyed by ID, allowlist, compare-and-set cursor."""

    box: Mailbox | None
    stored: dict[str, MessageMeta] = field(default_factory=dict)
    resyncs: list[Reason] = field(default_factory=list)

    async def mailbox(self) -> Mailbox | None:
        return self.box

    async def store(self, messages: Sequence[MessageMeta]) -> int:
        assert self.box is not None and len(messages) <= 100
        assert all(set(m.label_ids) & set(self.box.labels) for m in messages)
        new = [m for m in messages if m.message_id not in self.stored]
        self.stored.update((m.message_id, m) for m in new)
        return len(new)

    async def advance(
        self, start: int | None, end: int, messages: Sequence[MessageMeta], reason: Reason | None
    ) -> int | None:
        assert self.box is not None
        if self.box.history_id != start:
            return None
        assert end >= (start or 1)
        count = await self.store(messages)
        if reason:
            self.resyncs.append(reason)
        self.box = replace(self.box, history_id=end)
        return count


@dataclass
class MemoryWork(MemoryRecords):
    """Records alerts and finished work. Work is due only when a test sets `due`."""

    due: WorkClaim | None = None
    alerts: list[int] = field(default_factory=list)
    finished: list[tuple[bool, datetime | None]] = field(default_factory=list)
    broken: bool = False

    async def note_alert(self, history_id: int) -> bool:
        if self.broken:
            raise OSError("database unavailable")
        self.alerts.append(history_id)
        return True

    async def claim_work(self, lease_seconds: int) -> WorkClaim | None:
        claim, self.due = self.due, None
        return claim

    async def finish_work(
        self, claim: WorkClaim, synced: bool, watch_expires: datetime | None
    ) -> bool:
        self.finished.append((synced, watch_expires))
        return True

    async def finish_disconnect(self, claim: WorkClaim) -> bool:
        self.finished.append((False, None))
        return True
