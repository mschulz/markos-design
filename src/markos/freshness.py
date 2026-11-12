"""Read-only comparison of vault sources with indexed fingerprints."""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path

from markos.markdown import discover_markdown
from markos.query import source_fingerprints


class FreshnessError(ValueError):
    """Raised when current vault content cannot be fingerprinted."""


@dataclass(frozen=True)
class FreshnessReport:
    """Vault-relative changes since the latest successful rebuild."""

    added: tuple[str, ...]
    modified: tuple[str, ...]
    deleted: tuple[str, ...]

    @property
    def is_current(self) -> bool:
        return not (self.added or self.modified or self.deleted)


def check_freshness(vault_path: Path, index_path: Path) -> FreshnessReport:
    """Compare current Markdown bytes with schema-versioned index hashes."""

    try:
        root = vault_path.expanduser().resolve(strict=True)
    except OSError as error:
        raise FreshnessError(f"vault cannot be resolved: {vault_path}") from error
    indexed = {item.path: item.source_hash for item in source_fingerprints(index_path)}
    current: dict[str, str] = {}
    for relative_path in discover_markdown(root):
        try:
            content = (root / relative_path).read_bytes()
        except OSError as error:
            raise FreshnessError(f"could not fingerprint: {relative_path.as_posix()}") from error
        current[relative_path.as_posix()] = sha256(content).hexdigest()

    def sort_key(path: str) -> tuple[str, str]:
        return path.casefold(), path

    added = tuple(sorted(current.keys() - indexed.keys(), key=sort_key))
    deleted = tuple(sorted(indexed.keys() - current.keys(), key=sort_key))
    modified = tuple(
        sorted(
            (
                path
                for path in current.keys() & indexed.keys()
                if indexed[path] is None or current[path] != indexed[path]
            ),
            key=sort_key,
        )
    )
    return FreshnessReport(added=added, modified=modified, deleted=deleted)
