"""Connect the synthetic mailbox as its administrator, through the control functions.

The sealed token is a placeholder of the right shape: the database never opens it.
"""

import asyncio
import hashlib
from collections.abc import Awaitable, Callable, Sequence
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import psycopg

from markos_runtime.control.store import SessionContext
from markos_runtime.mail.gmail import SealedToken
from markos_runtime.mail.optin import MailboxView, MailOptIn
from tests.control_store_support import HarnessStore
from tests.integration.conftest import OTHER, OWNER

OWN = UUID(OWNER)
ELSE = UUID(OTHER)
BILLS = "Label_bills"
ADDRESS = "owner@example.invalid"
KEY_ID = "0" * 32
ADMIN = UUID("6d61696c-0000-4000-8000-000000000001")
VIEWER = UUID("6d61696c-0000-4000-8000-000000000002")


def token(owner: UUID = OWN, domain: str = "personal", fill: int = 7) -> SealedToken:
    return SealedToken(owner, domain, KEY_ID, bytes([1]) + bytes([fill]) * 61)


def enrol(database: dict[str, str]) -> None:
    """An administrator and a viewer of both synthetic owners' personal domains."""
    with psycopg.connect(database["migration"], autocommit=True) as conn:
        conn.execute(
            "INSERT INTO markos_control.gateway_logins VALUES('m1b_gateway') ON CONFLICT DO NOTHING"
        )
        people = ((ADMIN, "mail-admin", "administrator"), (VIEWER, "mail-viewer", "viewer"))
        for principal, name, role in people:
            conn.execute(
                "INSERT INTO markos_control.principals VALUES(%s,'synthetic-issuer',%s,false) "
                "ON CONFLICT DO NOTHING",
                (principal, name),
            )
            for owner in (OWN, ELSE):
                conn.execute(
                    "INSERT INTO markos_control.memberships VALUES(%s,%s,'personal',1,%s) "
                    "ON CONFLICT DO NOTHING",
                    (principal, owner, [role]),
                )


async def session(store: HarnessStore, who: str = "mail-admin") -> SessionContext:
    opened = await store.open_session(
        issuer="synthetic-issuer",
        subject=who,
        provider_session_id=uuid4(),
        lookup_hash=hashlib.sha256(uuid4().bytes).hexdigest(),
        kind="browser",
        provider_expires_at=datetime.now(UTC) + timedelta(hours=1),
    )
    return opened.context


def as_owner[T](
    database: dict[str, str],
    action: Callable[[MailOptIn, SessionContext], Awaitable[T]],
    who: str = "mail-admin",
) -> T:
    enrol(database)

    async def run() -> T:
        async with HarnessStore.connect(database["gateway"]) as store:
            return await action(MailOptIn(store), await session(store, who))

    return asyncio.run(run())


def connect_mailbox(
    database: dict[str, str],
    labels: Sequence[str] = (BILLS,),
    address: str = ADDRESS,
    owner: UUID = OWN,
) -> MailboxView:
    return as_owner(
        database,
        lambda mail, ctx: mail.connect(ctx, owner, "personal", address, labels, token(owner)),
    )


def disconnect_mailbox(database: dict[str, str]) -> MailboxView:
    return as_owner(database, lambda mail, ctx: mail.disconnect(ctx, OWN, "personal"))
