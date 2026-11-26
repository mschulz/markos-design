"""Migration 0022 on synthetic PostgreSQL: bills from the owner's bill label."""

import asyncio
import hashlib
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("pypdf")

from markos_runtime.control.store import ControlConflict, ControlDenied  # noqa: E402
from markos_runtime.knowledge.objects import BillObjects  # noqa: E402
from markos_runtime.knowledge.pdf import read_pages  # noqa: E402
from markos_runtime.knowledge.profiles import PDF_PROFILES  # noqa: E402
from markos_runtime.mail.gmail import Attachment  # noqa: E402
from markos_runtime.mail.optin import BillCounts  # noqa: E402
from markos_runtime.mail.store import MailStore  # noqa: E402
from markos_runtime.mail.worker import PassResult, run_once  # noqa: E402
from markos_runtime.store import StorageError  # noqa: E402
from tests.gmail_support import FakeGmail  # noqa: E402
from tests.integration.test_mail_store import grant_mail, rows  # noqa: E402
from tests.mail_optin_support import BILLS, OWN, as_owner, connect_mailbox  # noqa: E402
from tests.pdf_support import amber_pdf, make_pdf, origin_pdf  # noqa: E402

OTHER_LABEL = "Label_other"


def pdf(part: str, size: int = 4096) -> Attachment:
    return Attachment(part, "ANGjdJ_part" + part.replace(".", "_"), "application/pdf", size)


def bill_label(database: dict[str, str], label: str | None, who: str = "mail-admin") -> Any:
    return as_owner(
        database, lambda mail, ctx: mail.set_bill_label(ctx, OWN, "personal", label), who
    )


def folder(tmp_path: Path) -> BillObjects:
    root = tmp_path / "bills"
    root.mkdir(mode=0o700)
    return BillObjects(root)


def passes(
    database: dict[str, str], gmail: FakeGmail, objects: BillObjects | None
) -> list[PassResult]:
    async def run() -> list[PassResult]:
        done = []
        async with MailStore.connect(database["publisher"]) as store:
            # Gmail's alert for the newest change. It makes the poll due now.
            await store.note_alert(gmail.history_id)
            for _ in range(10):
                result = await run_once(gmail, store, objects)
                if result == PassResult():
                    break
                done.append(result)
        return done

    return asyncio.run(run())


def counts(database: dict[str, str]) -> BillCounts:
    return as_owner(database, lambda mail, ctx: mail.read(ctx, OWN, "personal")).bills


def test_only_an_administrator_sets_an_allowed_bill_label(database: dict[str, str]) -> None:
    grant_mail(database, "m0b_pub")
    connect_mailbox(database, labels=(BILLS, OTHER_LABEL))
    with pytest.raises(ControlDenied):
        bill_label(database, BILLS, "mail-viewer")
    with pytest.raises(ControlConflict):
        bill_label(database, "Label_unknown")
    assert bill_label(database, BILLS).bill_label == BILLS
    # A new allowlist without the bill label clears it.
    view = as_owner(
        database, lambda mail, ctx: mail.set_labels(ctx, OWN, "personal", [OTHER_LABEL])
    )
    assert view.bill_label is None
    assert bill_label(database, None).bill_label is None
    assert rows(
        database,
        "SELECT action,labels FROM markos_control.mail_history "
        "WHERE action='bill-label' ORDER BY recorded_at",
    ) == [("bill-label", [BILLS]), ("bill-label", [])]


def test_worker_imports_each_bill_once_and_counts_the_rest(
    database: dict[str, str], tmp_path: Path
) -> None:
    grant_mail(database, "m0b_pub")
    connect_mailbox(database, labels=(BILLS, OTHER_LABEL))
    objects = folder(tmp_path)
    amber, origin = amber_pdf(), origin_pdf()
    gmail = FakeGmail()
    # Mail stored before the label is picked is still read: the import goes back to 2020.
    gmail.deliver([BILLS], attachments=[pdf("1")], bodies=[amber])
    passes(database, gmail, objects)
    assert counts(database) == BillCounts()
    assert bill_label(database, BILLS).bills == BillCounts(waiting=1)
    gmail.deliver(
        [BILLS],
        attachments=[
            pdf("1"),
            pdf("2"),
            pdf("3"),
            pdf("4", size=3 * 1024 * 1024),
            Attachment("5", "ANGjdJ_png", "image/png", 100),
        ],
        bodies=[origin, make_pdf(["Invented Water Co\nAmount due $5.00"]), b"not a pdf"],
    )
    gmail.deliver([BILLS], attachments=[pdf("1")], bodies=[amber])
    gmail.deliver([OTHER_LABEL], attachments=[pdf("1")], bodies=[origin])
    alert = gmail.deliver([BILLS])
    results = passes(database, gmail, objects)

    outcomes = sorted(o for r in results for o in r.bills)
    assert outcomes == ["duplicate", "imported", "imported", "rejected", "rejected", "unsupported"]
    assert counts(database) == BillCounts(imported=2, duplicate=1, review=1, rejected=2)
    assert alert not in gmail.downloaded and sum(gmail.downloaded.values()) == 5
    assert rows(
        database,
        "SELECT reason FROM markos_runtime.mail_bill_outcomes "
        "WHERE outcome='rejected' ORDER BY reason",
    ) == [("not-pdf",), ("too-large",)]
    bills = rows(
        database,
        "SELECT source,created_by,mail_message_id IS NOT NULL,name,source_sha256,report "
        "FROM markos_control.bill_records ORDER BY report->>'extractor'",
    )
    assert [(b[0], b[1], b[2], b[3]) for b in bills] == [
        ("mail", None, True, "Gmail bill 2024-03-01"),
        ("mail", None, True, "Gmail bill 2024-03-01"),
    ]
    assert {b[4] for b in bills} == {hashlib.sha256(x).hexdigest() for x in (amber, origin)}
    assert rows(
        database,
        "SELECT actor_id::text,count(*) FROM markos_control.audit "
        "WHERE operation='bill-import.mail' GROUP BY 1",
    ) == [("00000000-0000-0000-0000-000000000000", 2)]
    # The K1 read-time recheck: the stored original, parsed again, gives the stored report.
    for b in bills:
        body = objects.read(OWN, "personal", b[4], ".pdf")
        pages = asyncio.run(read_pages(body))
        report = PDF_PROFILES[b[5]["extractor"]][1](pages, b[4])
        assert report.model_dump(mode="json") == b[5]
    # Nothing is read twice.
    assert passes(database, gmail, objects) == []
    assert sum(gmail.downloaded.values()) == 5


def test_worker_without_a_bill_folder_leaves_bills_queued(
    database: dict[str, str], tmp_path: Path
) -> None:
    grant_mail(database, "m0b_pub")
    connect_mailbox(database)
    bill_label(database, BILLS)
    gmail = FakeGmail()
    gmail.deliver([BILLS], attachments=[pdf("1")], bodies=[amber_pdf()])
    assert [r.bills for r in passes(database, gmail, None)] == [()]
    assert counts(database) == BillCounts(waiting=1)
    assert [r.bills for r in passes(database, gmail, folder(tmp_path))] == [("imported",)]


def test_bill_results_need_the_current_lease_and_every_pdf(database: dict[str, str]) -> None:
    grant_mail(database, "m0b_pub")
    connect_mailbox(database)
    bill_label(database, BILLS)
    gmail = FakeGmail()
    message = gmail.deliver([BILLS], attachments=[pdf("1"), pdf("2")], bodies=[b"x", b"y"])
    passes(database, gmail, None)
    rejected = {"kind": "rejected", "reason": "not-pdf"}

    async def run() -> None:
        async with MailStore.connect(database["publisher"]) as store:
            claim = await store.claim_work(30, True)
            assert claim is not None and claim.bills
            [due] = await store.bills_due(claim.fence, 5) or []
            assert due.message_id == message
            assert await store.bills_due(claim.fence - 1, 5) is None
            with pytest.raises(StorageError):
                await store.finish_bill(claim.fence, message, [{"part_id": "1", **rejected}])
            stale = await store.finish_bill(
                claim.fence - 1, message, [{"part_id": p, **rejected} for p in ("1", "2")]
            )
            assert stale is None
            both = [{"part_id": p, **rejected} for p in ("1", "2")]
            assert await store.finish_bill(claim.fence, message, both) == ["rejected", "rejected"]
            assert await store.finish_bill(claim.fence, message, both) is None

    asyncio.run(run())
