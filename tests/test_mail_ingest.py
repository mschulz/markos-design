"""Mail sync on a fake Gmail: imports, changes, repeats, allowlist, lost history, stale cursor."""

import asyncio
from dataclasses import dataclass
from datetime import date

import pytest

from markos_runtime.mail.ingest import Mailbox, MailError, SyncResult, sync
from tests.gmail_support import PDF, FakeGmail, MemoryRecords

BILLS = "Label_bills"


def run(gmail: FakeGmail, records: MemoryRecords) -> SyncResult:
    return asyncio.run(sync(gmail, records))


def connected(gmail: FakeGmail) -> MemoryRecords:
    records = MemoryRecords(Mailbox((BILLS,), None))
    assert run(gmail, records) == SyncResult("first-import", len(records.stored))
    return records


def test_first_import_reads_only_allowlisted_labels_since_2020() -> None:
    gmail = FakeGmail()
    bills = [gmail.deliver([BILLS, "INBOX"], attachments=[PDF]) for _ in range(3)]
    private = [gmail.deliver(["INBOX"]) for _ in range(2)]
    old = gmail.deliver([BILLS], received=date(2019, 6, 1))
    records = MemoryRecords(Mailbox((BILLS,), None))

    assert run(gmail, records) == SyncResult("first-import", 3)

    assert set(records.stored) == set(bills)
    assert records.stored[bills[0]].attachments == (PDF,)
    assert not any(gmail.fetched[m] for m in [*private, old])
    assert records.box == Mailbox((BILLS,), gmail.history_id)
    assert records.resyncs == ["first-import"]


def test_changes_are_read_across_pages_and_other_labels_are_not_fetched() -> None:
    gmail = FakeGmail()
    records = connected(gmail)
    new = [gmail.deliver([BILLS]) for _ in range(3)]
    private = gmail.deliver(["INBOX"])

    assert run(gmail, records) == SyncResult("incremental", 3)

    assert set(records.stored) == set(new)
    assert gmail.fetched[private] == 0
    assert records.box is not None and records.box.history_id == gmail.history_id


def test_repeated_changes_store_one_record_and_fetch_once_per_pass() -> None:
    gmail = FakeGmail()
    records = connected(gmail)
    message = gmail.deliver([BILLS])
    gmail.relabel(message, [BILLS, "STARRED"])
    gmail.relabel(message, [BILLS, "IMPORTANT"])

    assert run(gmail, records) == SyncResult("incremental", 1)
    assert gmail.fetched[message] == 1

    gmail.relabel(message, [BILLS])
    assert run(gmail, records) == SyncResult("incremental", 0)
    assert list(records.stored) == [message]


def test_message_moved_into_allowlist_is_stored_and_moved_out_is_not() -> None:
    gmail = FakeGmail()
    records = connected(gmail)
    moved_in = gmail.deliver(["INBOX"])
    moved_out = gmail.deliver([BILLS])
    gmail.relabel(moved_in, [BILLS])
    gmail.relabel(moved_out, ["INBOX"])

    assert run(gmail, records) == SyncResult("incremental", 1)
    assert list(records.stored) == [moved_in]


def test_deleted_message_is_skipped() -> None:
    gmail = FakeGmail()
    records = connected(gmail)
    gone = gmail.deliver([BILLS])
    kept = gmail.deliver([BILLS])
    gmail.delete(gone)

    assert run(gmail, records) == SyncResult("incremental", 1)
    assert list(records.stored) == [kept]


def test_lost_history_resyncs_allowlisted_labels_and_finds_each_message_once() -> None:
    gmail = FakeGmail()
    records = connected(gmail)
    before = gmail.deliver([BILLS])
    assert run(gmail, records).stored == 1
    missed = [gmail.deliver([BILLS]) for _ in range(3)]
    private = gmail.deliver(["INBOX"])
    gmail.forget_history()

    assert run(gmail, records) == SyncResult("history-lost", 3)

    assert set(records.stored) == {before, *missed}
    assert gmail.fetched[private] == 0
    assert records.resyncs == ["first-import", "history-lost"]
    assert records.box is not None and records.box.history_id == gmail.history_id


def test_more_than_one_batch_commits_the_last_batch_with_the_cursor() -> None:
    gmail = FakeGmail(page_size=50)
    records = connected(gmail)
    new = [gmail.deliver([BILLS]) for _ in range(230)]

    assert run(gmail, records) == SyncResult("incremental", 230)
    assert set(records.stored) == set(new)


@dataclass
class StaleRecords(MemoryRecords):
    """Another worker moved the cursor after this worker read the mailbox."""

    snapshot: Mailbox | None = None

    async def mailbox(self) -> Mailbox | None:
        return self.snapshot


def test_stale_cursor_writes_nothing() -> None:
    gmail = FakeGmail()
    records = connected(gmail)
    old = records.box
    gmail.deliver([BILLS])
    assert run(gmail, records).stored == 1
    stale = StaleRecords(records.box, dict(records.stored), snapshot=old)
    gmail.deliver([BILLS])

    assert run(gmail, stale) == SyncResult("stale", 0)
    assert stale.stored == records.stored


def test_no_connected_mailbox_is_an_error() -> None:
    with pytest.raises(MailError):
        run(FakeGmail(), MemoryRecords(None))
    with pytest.raises(MailError):
        run(FakeGmail(), MemoryRecords(Mailbox((BILLS,), 1001, connected=False)))
