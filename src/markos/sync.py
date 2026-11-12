"""Conditional synchronization of a Markdown vault into its external index."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from markos.freshness import FreshnessReport, check_freshness
from markos.index import IndexSummary, rebuild_vault_index

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


def sync_vault_index(
    vault_path: Path,
    index_path: Path,
    *,
    force: bool = False,
) -> SyncResult:
    """Rebuild only when the external index is missing, stale, or forced."""

    try:
        root = vault_path.expanduser().resolve(strict=True)
    except OSError as error:
        raise SyncError(f"vault cannot be resolved: {vault_path}") from error
    if not root.is_dir():
        raise SyncError(f"vault is not a directory: {root}")
    destination = index_path.expanduser().resolve(strict=False)
    if _is_within(destination, root):
        raise SyncError("index path must be outside the Markdown vault")

    if force:
        return _rebuild(root, destination, reason="forced")
    if not destination.exists():
        return _rebuild(root, destination, reason="missing")

    changes = check_freshness(root, destination)
    if changes.is_current:
        return SyncResult(rebuilt=False, reason="current", changes=changes)
    return _rebuild(root, destination, reason="stale", changes=changes)


def _rebuild(
    vault_path: Path,
    index_path: Path,
    *,
    reason: SyncReason,
    changes: FreshnessReport | None = None,
) -> SyncResult:
    summary = rebuild_vault_index(vault_path, index_path)
    verification = check_freshness(vault_path, index_path)
    if not verification.is_current:
        raise SyncError("vault changed during synchronization; run sync again")
    return SyncResult(rebuilt=True, reason=reason, summary=summary, changes=changes)


def _is_within(path: Path, directory: Path) -> bool:
    try:
        path.relative_to(directory)
    except ValueError:
        return False
    return True
