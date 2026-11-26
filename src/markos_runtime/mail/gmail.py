"""Gmail access as a narrow interface: read mail, keep a watch, and stop at disconnect.

There is no network client yet: tests use a fake.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime
from typing import Protocol
from uuid import UUID


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


@dataclass(frozen=True)
class Watch:
    """A Gmail watch sends change alerts for the given labels until it expires (about 7 days)."""

    history_id: int
    expires_at: datetime


class GmailReader(Protocol):
    async def current_history_id(self) -> int: ...

    async def history(self, start: int, page: str | None) -> HistoryPage: ...

    async def list_messages(self, label_id: str, after: date, page: str | None) -> ListPage: ...

    async def message(self, message_id: str) -> MessageMeta: ...

    async def watch(self, label_ids: Sequence[str]) -> Watch: ...


class GmailClient(GmailReader, Protocol):
    """Adds the two calls a disconnect needs. Both succeed when already done."""

    async def stop(self) -> None: ...

    async def revoke(self) -> None: ...


@dataclass(frozen=True)
class SealedToken:
    """A Gmail refresh token sealed for this owner and domain. Only the mail worker can open it."""

    owner_id: UUID
    domain: str
    key_id: str
    sealed: bytes


class GmailAccess(Protocol):
    def client(self, token: SealedToken | None) -> GmailClient: ...
