"""Mailbox state and message records in PostgreSQL, through the M2-A functions only."""

from collections.abc import Sequence
from datetime import timedelta
from typing import Any

from psycopg.types.json import Jsonb

from markos_runtime.mail.gmail import MessageMeta
from markos_runtime.mail.ingest import Mailbox, MailError, Reason
from markos_runtime.store.database import DurableStore, StorageError


def _record(meta: MessageMeta) -> dict[str, Any]:
    if meta.received_at.utcoffset() != timedelta(0):
        raise MailError("timezone-aware UTC required")
    return {
        "message_id": meta.message_id,
        "thread_id": meta.thread_id,
        "history_id": str(meta.history_id),
        "received_at": meta.received_at.isoformat(),
        "label_ids": list(meta.label_ids),
        "attachments": [
            {
                "part_id": a.part_id,
                "attachment_id": a.attachment_id,
                "mime_type": a.mime_type.lower(),
                "size": a.size,
            }
            for a in meta.attachments
        ],
    }


def _mailbox(state: dict[str, Any] | None) -> Mailbox | None:
    if state is None:
        return None
    cursor = state["history_id"]
    return Mailbox(
        tuple(state["labels"]), None if cursor is None else int(cursor), state["connected"]
    )


class MailStore(DurableStore):
    async def _one(self, query: str, params: tuple[Any, ...] = ()) -> Any:
        async with self._transaction() as connection:
            row = await (await connection.execute(query, params)).fetchone()
            if row is None:
                raise StorageError("storage operation failed")
            return row["result"]

    async def open_mailbox(self, address_hash: str, labels: Sequence[str]) -> Mailbox:
        state = await self._one(
            "SELECT markos_runtime.open_mailbox(%s,%s) AS result", (address_hash, list(labels))
        )
        box = _mailbox(state)
        assert box is not None
        return box

    async def mailbox(self) -> Mailbox | None:
        return _mailbox(await self._one("SELECT markos_runtime.mailbox_state() AS result"))

    async def store(self, messages: Sequence[MessageMeta]) -> int:
        batch = Jsonb([_record(m) for m in messages])
        return int(await self._one("SELECT markos_runtime.store_mail(%s) AS result", (batch,)))

    async def advance(
        self, start: int | None, end: int, messages: Sequence[MessageMeta], reason: Reason | None
    ) -> int | None:
        batch = Jsonb([_record(m) for m in messages])
        result = await self._one(
            "SELECT markos_runtime.advance_mail(%s::numeric,%s::numeric,%s,%s) AS result",
            (None if start is None else str(start), str(end), batch, reason),
        )
        return None if result is None else int(result)
