"""Owner opt-in through the control database: connect, labels, disconnect and status.

An administrator of the owner and domain makes each change, and each change is audited. The
gateway passes only a token that is already sealed for the mail worker.
"""

import hashlib
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from markos_runtime.contracts.base import Domain
from markos_runtime.control.store import ControlStore, SessionContext
from markos_runtime.mail.gmail import SealedToken

State = Literal["none", "connected", "disconnecting", "disconnected"]


class OptInError(ValueError):
    pass


@dataclass(frozen=True)
class BillCounts:
    """Mail bill outcomes. `review` counts unknown suppliers and bills over the folder limit."""

    waiting: int = 0
    imported: int = 0
    duplicate: int = 0
    review: int = 0
    rejected: int = 0


@dataclass(frozen=True)
class MailboxView:
    state: State
    labels: tuple[str, ...] | None
    watch_expires_at: datetime | None
    bill_label: str | None = None
    bills: BillCounts = BillCounts()


def address_hash(address: str) -> str:
    """The stored form of the Gmail address. The address itself is never stored."""
    return hashlib.sha256(address.strip().lower().encode()).hexdigest()


def _view(value: dict[str, Any]) -> MailboxView:
    labels = value["labels"]
    expires = value["watch_expires_at"]
    return MailboxView(
        value["state"],
        None if labels is None else tuple(labels),
        None if expires is None else datetime.fromisoformat(expires),
        value["bill_label"],
        BillCounts(**value["bills"]),
    )


class MailOptIn:
    def __init__(self, store: ControlStore) -> None:
        self._store = store

    async def _run(
        self, function: str, ctx: SessionContext, owner: UUID, domain: Domain, *args: object
    ) -> MailboxView:
        value = await self._store._call(
            function, ctx.session_id, ctx.lookup_hash, owner, domain, *args
        )
        return _view(value)

    async def connect(
        self,
        ctx: SessionContext,
        owner: UUID,
        domain: Domain,
        address: str,
        labels: Sequence[str],
        token: SealedToken,
    ) -> MailboxView:
        if (token.owner_id, token.domain) != (owner, domain):
            raise OptInError("token sealed for another mailbox")
        return await self._run(
            "connect_mailbox",
            ctx,
            owner,
            domain,
            address_hash(address),
            list(labels),
            token.sealed,
            token.key_id,
        )

    async def set_labels(
        self, ctx: SessionContext, owner: UUID, domain: Domain, labels: Sequence[str]
    ) -> MailboxView:
        return await self._run("set_mail_labels", ctx, owner, domain, list(labels))

    async def set_bill_label(
        self, ctx: SessionContext, owner: UUID, domain: Domain, label: str | None
    ) -> MailboxView:
        """Pick the one allowed label that holds bills, or None to stop bill imports."""
        return await self._run("set_mail_bill_label", ctx, owner, domain, label)

    async def disconnect(self, ctx: SessionContext, owner: UUID, domain: Domain) -> MailboxView:
        return await self._run("disconnect_mailbox", ctx, owner, domain)

    async def read(self, ctx: SessionContext, owner: UUID, domain: Domain) -> MailboxView:
        return await self._run("read_mailbox", ctx, owner, domain)
