"""Mail worker pass on fakes: alert parsing, renewal, sync and kept renewals."""

import asyncio

import pytest

from markos_runtime.mail.ingest import Mailbox, SyncResult
from markos_runtime.mail.worker import PassResult, WorkClaim, alert_history, run_once
from tests.gmail_support import FakeGmail, MemoryWork

BILLS = "Label_bills"


@pytest.mark.parametrize(
    ("data", "expected"),
    [
        (b'{"emailAddress": "a@example.invalid", "historyId": 1234}', 1234),
        (b'{"historyId": "1234"}', 1234),
        (b'{"historyId": 0}', None),
        (b'{"historyId": -5}', None),
        (b'{"historyId": 1.5}', None),
        (b'{"historyId": true}', None),
        (b'{"historyId": "12a"}', None),
        (b'{"historyId": 18446744073709551616}', None),
        (b'{"emailAddress": "a@example.invalid"}', None),
        (b"[1234]", None),
        (b"not json", None),
        (b"\xff\xfe", None),
        (b'{"historyId": 1, "pad": "' + b"x" * 1024 + b'"}', None),
    ],
)
def test_alert_history_reads_only_a_valid_history_id(data: bytes, expected: int | None) -> None:
    assert alert_history(data) == expected


def test_due_work_renews_the_allowlisted_watch_and_syncs() -> None:
    gmail = FakeGmail()
    work = MemoryWork(Mailbox((BILLS,), 1000), due=WorkClaim(1, True, True, 0, (BILLS,)))
    gmail.deliver([BILLS])

    result = asyncio.run(run_once(gmail, work))

    assert result == PassResult(True, SyncResult("incremental", 1))
    assert gmail.watched == [(BILLS,)]
    assert len(work.finished) == 1 and work.finished[0][0] is True
    assert work.finished[0][1] is not None


def test_failed_sync_keeps_the_renewal_and_leaves_the_sync_due() -> None:
    gmail = FakeGmail()
    work = MemoryWork(None, due=WorkClaim(1, True, True, 0, (BILLS,)))

    with pytest.raises(Exception, match="no connected mailbox"):
        asyncio.run(run_once(gmail, work))
    assert len(work.finished) == 1 and work.finished[0][0] is False
    assert work.finished[0][1] is not None
