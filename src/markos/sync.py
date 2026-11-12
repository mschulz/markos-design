"""Conditional synchronization of a Markdown root into its external index."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from markos._compat import legacy_keywords, renamed_alias
from markos.freshness import FreshnessReport, check_freshness
from markos.index import IndexSummary, rebuild_markdown_index

SyncReason = Literal["current", "missing", "stale", "forced"]


class SyncError(ValueError):
    """Raised when synchronization cannot produce a current index."""


@dataclass(frozen=True)
class SyncResult:
    """Outcome of one conditional index synchronization."""

    rebuilt: bool
    reason: SyncReason
    summary: IndexSummary | None = None
    changes: FreshnessReport | None = None


@legacy_keywords
def sync_markdown_index(
    markdown_root: Path,
    index_path: Path,
    *,
    force: bool = False,
) -> SyncResult:
    """Rebuild only when the external index is missing, stale, or forced."""

    try:
        root = markdown_root.expanduser().resolve(strict=True)
    except OSError as error:
        raise SyncError(f"markdown_root cannot be resolved: {markdown_root}") from error
    if not root.is_dir():
        raise SyncError(f"markdown_root is not a directory: {root}")
    destination = index_path.expanduser().resolve(strict=False)
    if _is_within(destination, root):
        raise SyncError("index path must be outside the Markdown root")

    if force:
        return _rebuild(root, destination, reason="forced")
    if not destination.exists():
        return _rebuild(root, destination, reason="missing")

    changes = check_freshness(root, destination)
    if changes.is_current:
        return SyncResult(rebuilt=False, reason="current", changes=changes)
    return _rebuild(root, destination, reason="stale", changes=changes)


def _rebuild(
    markdown_root: Path,
    index_path: Path,
    *,
    reason: SyncReason,
    changes: FreshnessReport | None = None,
) -> SyncResult:
    summary = rebuild_markdown_index(markdown_root, index_path)
    verification = check_freshness(markdown_root, index_path)
    if not verification.is_current:
        raise SyncError("markdown_root changed during synchronization; run sync again")
    return SyncResult(rebuilt=True, reason=reason, summary=summary, changes=changes)


def _is_within(path: Path, directory: Path) -> bool:
    try:
        path.relative_to(directory)
    except ValueError:
        return False
    return True


# Deprecated Python name; application internals use the canonical function.
sync_vault_index = renamed_alias(sync_markdown_index, "sync_vault_index")
