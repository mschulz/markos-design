from collections.abc import Callable
from pathlib import Path

from markos.freshness import check_freshness
from markos.index import rebuild_markdown_index
from markos.query import read_document
from markos.sync import SyncError, SyncResult, sync_markdown_index
from markos.watch import WatchError, snapshot_markdown_root, watch_markdown_index


class ScriptedStop:
    def __init__(self, actions: list[Callable[[], None]]) -> None:
        self.actions = actions

    def wait(self, timeout: float) -> bool:
        del timeout
        if not self.actions:
            return True
        self.actions.pop(0)()
        return False


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def advance(self, value: float) -> Callable[[], None]:
        def action() -> None:
            self.now = value

        return action


def test_snapshot_is_stable_and_read_only(tmp_path: Path) -> None:
    markdown_root = tmp_path / "markdown_root"
    markdown_root.mkdir()
    note = markdown_root / "Note.md"
    note.write_text("# Note", encoding="utf-8")
    before = note.read_bytes()

    first = snapshot_markdown_root(markdown_root)
    second = snapshot_markdown_root(markdown_root)

    assert first == second
    assert first[0][0] == "Note.md"
    assert note.read_bytes() == before


def test_watch_coalesces_a_change_burst_into_one_rebuild(tmp_path: Path) -> None:
    markdown_root = tmp_path / "markdown_root"
    markdown_root.mkdir()
    note = markdown_root / "Note.md"
    note.write_text("# Before", encoding="utf-8")
    index_path = tmp_path / "index.duckdb"
    clock = FakeClock()

    def first_change() -> None:
        clock.now = 0.1
        note.write_text("# First expansion", encoding="utf-8")

    def second_change() -> None:
        clock.now = 0.3
        note.write_text("# Second much longer expansion", encoding="utf-8")

    stop = ScriptedStop([first_change, second_change, clock.advance(0.7), clock.advance(0.9)])

    summary = watch_markdown_index(
        markdown_root,
        index_path,
        poll_interval=1.0,
        debounce=0.5,
        stop_signal=stop,
        clock=clock,
    )

    document = read_document(index_path, "Note.md")
    assert summary.sync_attempts == 2
    assert summary.rebuilds == 2
    assert summary.errors == 0
    assert document is not None
    assert document.title == "Second much longer expansion"
    assert check_freshness(markdown_root, index_path).is_current


def test_watch_retries_a_recoverable_sync_error(tmp_path: Path) -> None:
    markdown_root = tmp_path / "markdown_root"
    markdown_root.mkdir()
    (markdown_root / "Note.md").write_text("# Note", encoding="utf-8")
    index_path = tmp_path / "index.duckdb"
    clock = FakeClock()
    calls = 0
    reported: list[Exception] = []

    def flaky_sync(source: Path, destination: Path) -> SyncResult:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise SyncError("temporary failure")
        return sync_markdown_index(source, destination)

    summary = watch_markdown_index(
        markdown_root,
        index_path,
        debounce=0.0,
        stop_signal=ScriptedStop([clock.advance(1.0)]),
        synchronizer=flaky_sync,
        clock=clock,
        on_error=reported.append,
    )

    assert summary.sync_attempts == 2
    assert summary.rebuilds == 1
    assert summary.errors == 1
    assert len(reported) == 1
    assert check_freshness(markdown_root, index_path).is_current


def test_watch_preserves_current_index_and_leaves_no_lock_file(tmp_path: Path) -> None:
    markdown_root = tmp_path / "markdown_root"
    markdown_root.mkdir()
    (markdown_root / "Note.md").write_text("# Note", encoding="utf-8")
    index_path = tmp_path / "index.duckdb"
    rebuild_markdown_index(markdown_root, index_path)
    before = index_path.read_bytes()

    summary = watch_markdown_index(markdown_root, index_path, stop_signal=ScriptedStop([]))

    assert summary == type(summary)(sync_attempts=1, rebuilds=0, errors=0)
    assert index_path.read_bytes() == before
    assert sorted(path.name for path in tmp_path.iterdir()) == ["index.duckdb", "markdown_root"]


def test_watch_rejects_index_inside_markdown_root(tmp_path: Path) -> None:
    markdown_root = tmp_path / "markdown_root"
    markdown_root.mkdir()

    try:
        watch_markdown_index(
            markdown_root, markdown_root / "index.duckdb", stop_signal=ScriptedStop([])
        )
    except WatchError as error:
        assert "outside" in str(error)
    else:
        raise AssertionError("watch accepted an index inside the markdown_root")
