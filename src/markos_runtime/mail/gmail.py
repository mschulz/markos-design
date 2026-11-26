"""Read-only Gmail access as a narrow interface. M2-A has no network client: tests use a fake."""

from dataclasses import dataclass
from datetime import date, datetime
from typing import Protocol


class HistoryGone(Exception):
    """Gmail no longer holds history from the requested start ID (HTTP 404 on history.list)."""


class MessageGone(Exception):
    """The message was deleted after a change named it."""


@dataclass(frozen=True)
class Attachment:
    part_id: str
    attachment_id: str
    mime_type: str
    size: int


@dataclass(frozen=True)
class MessageMeta:
    """Metadata only. The mail body and attachment names are never read into this record."""

    message_id: str
    thread_id: str
    history_id: int
    received_at: datetime
    label_ids: tuple[str, ...]
    attachments: tuple[Attachment, ...] = ()


@dataclass(frozen=True)
class Change:
    """A history record that names a message and its labels at that point."""

    message_id: str
    label_ids: tuple[str, ...]


@dataclass(frozen=True)
class HistoryPage:
    changes: tuple[Change, ...]
    history_id: int
    next_page: str | None = None


@dataclass(frozen=True)
class ListPage:
    message_ids: tuple[str, ...]
    next_page: str | None = None


class GmailReader(Protocol):
    async def current_history_id(self) -> int: ...

    async def history(self, start: int, page: str | None) -> HistoryPage: ...

    async def list_messages(self, label_id: str, after: date, page: str | None) -> ListPage: ...

    async def message(self, message_id: str) -> MessageMeta: ...
