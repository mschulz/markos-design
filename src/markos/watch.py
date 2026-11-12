"""Foreground monitoring for automatic read-only Markdown root synchronization."""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from threading import Event
from typing import Protocol

from markos._compat import legacy_keywords, renamed_alias
from markos.freshness import FreshnessError
from markos.index import KnowledgeIndexError
from markos.markdown import MarkdownDiscoveryError, discover_markdown
from markos.query import KnowledgeQueryError
from markos.sync import SyncError, SyncResult, sync_markdown_index

logger = logging.getLogger(__name__)

MarkdownSnapshot = tuple[tuple[str, int, int, int, int], ...]
SyncCallback = Callable[[SyncResult], None]
ErrorCallback = Callable[[Exception], None]


class StopSignal(Protocol):
    """Minimal event interface used by the watch loop."""

    def wait(self, timeout: float) -> bool:
        """Return true when monitoring should stop."""


class WatchError(ValueError):
    """Raised when a Markdown root cannot be monitored safely."""


@dataclass(frozen=True)
class WatchSummary:
    """Operational counts returned when a watcher stops."""

    sync_attempts: int
    rebuilds: int
    errors: int


@legacy_keywords
def snapshot_markdown_root(markdown_root: Path) -> MarkdownSnapshot:
    """Capture stable, read-only change markers for discoverable Markdown."""

    try:
        root = markdown_root.expanduser().resolve(strict=True)
    except OSError as error:
        raise WatchError(f"markdown_root cannot be resolved: {markdown_root}") from error
    if not root.is_dir():
        raise WatchError(f"markdown_root is not a directory: {root}")

    entries: list[tuple[str, int, int, int, int]] = []
    try:
        for relative_path in discover_markdown(root):
            stat = (root / relative_path).stat(follow_symlinks=False)
            entries.append(
                (
                    relative_path.as_posix(),
                    stat.st_size,
                    stat.st_mtime_ns,
                    stat.st_ctime_ns,
                    stat.st_ino,
                )
            )
    except (MarkdownDiscoveryError, OSError) as error:
        raise WatchError(f"could not snapshot markdown_root {root}: {error}") from error
    return tuple(entries)


@legacy_keywords
def watch_markdown_index(
    markdown_root: Path,
    index_path: Path,
    *,
    poll_interval: float = 1.0,
    debounce: float = 0.5,
    stop_signal: StopSignal | None = None,
    scanner: Callable[[Path], MarkdownSnapshot] = snapshot_markdown_root,
    synchronizer: Callable[[Path, Path], SyncResult] = sync_markdown_index,
    clock: Callable[[], float] = time.monotonic,
    on_sync: SyncCallback | None = None,
    on_error: ErrorCallback | None = None,
) -> WatchSummary:
    """Synchronize initially, then after each debounced Markdown change burst."""

    if poll_interval <= 0:
        raise WatchError("poll interval must be greater than zero")
    if debounce < 0:
        raise WatchError("debounce must not be negative")

    root = markdown_root.expanduser().resolve(strict=False)
    destination = index_path.expanduser().resolve(strict=False)
    if _is_within(destination, root):
        raise WatchError("index path must be outside the Markdown root")

    stopper = Event() if stop_signal is None else stop_signal
    baseline = scanner(root)
    sync_attempts = 0
    rebuilds = 0
    errors = 0
    pending_at: float | None = None

    def synchronize() -> bool:
        nonlocal sync_attempts, rebuilds, errors
        sync_attempts += 1
        try:
            result = synchronizer(root, destination)
        except _RECOVERABLE_ERRORS as error:
            errors += 1
            logger.error("watch synchronization failed: %s", error)
            if on_error is not None:
                on_error(error)
            return False
        if result.rebuilt:
            rebuilds += 1
        logger.info("watch synchronized: reason=%s rebuilt=%s", result.reason, result.rebuilt)
        if on_sync is not None:
            on_sync(result)
        return True

    if not synchronize():
        pending_at = clock()

    logger.info("watching Markdown root: %s", root)
    while not stopper.wait(poll_interval):
        try:
            current = scanner(root)
        except WatchError as error:
            errors += 1
            logger.error("watch scan failed: %s", error)
            if on_error is not None:
                on_error(error)
            continue

        now = clock()
        if current != baseline:
            baseline = current
            pending_at = now
            logger.info("Markdown change detected; synchronization pending")

        if pending_at is None or now - pending_at < debounce:
            continue
        pending_at = None if synchronize() else now

    logger.info("Markdown watcher stopped")
    return WatchSummary(sync_attempts=sync_attempts, rebuilds=rebuilds, errors=errors)


def _is_within(path: Path, directory: Path) -> bool:
    try:
        path.relative_to(directory)
    except ValueError:
        return False
    return True


_RECOVERABLE_ERRORS = (
    FreshnessError,
    KnowledgeIndexError,
    KnowledgeQueryError,
    MarkdownDiscoveryError,
    SyncError,
)


# Deprecated Python name; application internals use the canonical function.
snapshot_vault = renamed_alias(snapshot_markdown_root, "snapshot_vault")


# Deprecated Python name; application internals use the canonical function.
watch_vault_index = renamed_alias(watch_markdown_index, "watch_vault_index")

# Deprecated type spelling; new callers use MarkdownSnapshot.
VaultSnapshot = MarkdownSnapshot
