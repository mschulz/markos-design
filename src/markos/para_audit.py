"""Read-only assessment of existing Markdown root folders for PARA migration."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path, PurePath
from typing import Literal

from markos._compat import legacy_keywords, warn_renamed
from markos.markdown import MARKDOWN_SUFFIXES
from markos.query import GraphSnapshot, graph_snapshot

MigrationRisk = Literal["low", "medium", "high"]


class ParaAuditError(ValueError):
    """Raised when a PARA migration audit cannot be completed safely."""


@dataclass(frozen=True)
class FolderAudit:
    """Read-only inventory and migration guidance for one Markdown root folder."""

    source: str
    suggested_destination: str
    action: str
    risk: MigrationRisk
    file_count: int
    markdown_count: int
    raw_file_count: int
    byte_count: int
    nested_repository_count: int
    outgoing_link_count: int
    unresolved_outgoing_count: int
    internal_link_count: int
    external_inbound_count: int
    external_inbound_sources: tuple[str, ...]
    existing_managed_folders: tuple[str, ...]
    reasons: tuple[str, ...]


@dataclass(frozen=True, init=False)
class ParaAuditReport:
    """A migration assessment that never changes the Markdown root."""

    markdown_root: Path
    folders: tuple[FolderAudit, ...]

    def __init__(
        self,
        markdown_root: Path | None = None,
        folders: tuple[FolderAudit, ...] | None = None,
        *,
        vault_path: Path | None = None,
    ) -> None:
        if vault_path is not None:
            if markdown_root is not None:
                raise TypeError("provide markdown_root only, not both root names")
            warn_renamed("vault_path", "markdown_root")
            markdown_root = vault_path
        if markdown_root is None or folders is None:
            raise TypeError("markdown_root and folders are required")
        object.__setattr__(self, "markdown_root", markdown_root)
        object.__setattr__(self, "folders", folders)

    @property
    def vault_path(self) -> Path:
        """Deprecated read-only field spelling for existing Python clients."""
        warn_renamed("vault_path", "markdown_root")
        return self.markdown_root


@legacy_keywords
def audit_para_migration(
    markdown_root: Path,
    index_path: Path,
    sources: tuple[str, ...],
) -> ParaAuditReport:
    """Assess selected Markdown root folders using filesystem and indexed link facts."""

    if not sources:
        raise ParaAuditError("at least one source folder is required")
    root = _resolved_directory(markdown_root, label="markdown_root")
    snapshot = graph_snapshot(index_path)
    audits = tuple(_audit_folder(root, source, snapshot) for source in sources)
    return ParaAuditReport(markdown_root=root, folders=audits)


def render_para_audit(report: ParaAuditReport) -> str:
    """Render a human-readable Markdown migration report."""

    lines = [
        "# MarkOS PARA Migration Audit",
        "",
        f"Markdown root: `{report.markdown_root}`",
        "",
        "> Read-only assessment. No Markdown files or folders were changed.",
        "",
        "## Summary",
        "",
        "| Source | Suggested destination | Action | Risk | Markdown | "
        "Non-Markdown | Inbound links |",
        "|---|---|---|:---:|---:|---:|---:|",
    ]
    for folder in report.folders:
        lines.append(
            f"| `{folder.source}` | `{folder.suggested_destination}` | "
            f"{folder.action} | {folder.risk} | {folder.markdown_count} | "
            f"{folder.raw_file_count} | {folder.external_inbound_count} |"
        )

    lines.extend(["", "## Folder Assessments", ""])
    for folder in report.folders:
        lines.extend(
            [
                f"### {folder.source}",
                "",
                f"- Suggested destination: `{folder.suggested_destination}`",
                f"- Recommended action: **{folder.action}**",
                f"- Migration risk: **{folder.risk}**",
                f"- Files: {folder.file_count} ({_format_bytes(folder.byte_count)})",
                f"- Markdown notes: {folder.markdown_count}",
                f"- Non-Markdown/raw files: {folder.raw_file_count}",
                f"- Nested Git repositories: {folder.nested_repository_count}",
                f"- Outgoing links: {folder.outgoing_link_count}",
                f"- Unresolved outgoing links: {folder.unresolved_outgoing_count}",
                f"- Internal resolved links: {folder.internal_link_count}",
                f"- Resolved inbound links from outside folder: {folder.external_inbound_count}",
            ]
        )
        if folder.existing_managed_folders:
            managed = ", ".join(f"`{name}/`" for name in folder.existing_managed_folders)
            lines.append(f"- Existing concept subfolders: {managed}")
        lines.extend(["", "Reasons:", ""])
        lines.extend(f"- {reason}" for reason in folder.reasons)
        if folder.external_inbound_sources:
            lines.extend(["", "Inbound-link source preview:", ""])
            lines.extend(f"- `{source}`" for source in folder.external_inbound_sources)
        lines.append("")

    lines.extend(
        [
            "## Safe Migration Sequence",
            "",
            "1. Keep every source folder in place while reviewing this report.",
            "2. Resolve ambiguous destinations and concept names.",
            "3. Generate a complete file-move and managed-link proposal.",
            "4. Review the multi-file diff and backlink impact.",
            "5. Apply only an explicitly approved batch, then rebuild and run health checks.",
            "",
            "The inbox is intended for new or genuinely unclassified items. It is not a staging "
            "area for wholesale relocation of a large, linked legacy collection.",
            "",
        ]
    )
    return "\n".join(lines)


def write_para_audit(report: ParaAuditReport, output_path: Path) -> Path:
    """Write a report outside the Markdown root, never into the source-of-truth tree."""

    destination = output_path.expanduser().resolve(strict=False)
    if _is_within(destination, report.markdown_root):
        raise ParaAuditError("audit report path must be outside the Markdown root")
    try:
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(render_para_audit(report), encoding="utf-8")
    except OSError as error:
        raise ParaAuditError(f"could not write audit report: {destination}") from error
    return destination


def _audit_folder(root: Path, source: str, snapshot: GraphSnapshot) -> FolderAudit:
    relative = PurePath(source)
    if relative.is_absolute() or ".." in relative.parts or not relative.parts:
        raise ParaAuditError(f"source folder must be root-relative: {source}")
    source_path = root.joinpath(*relative.parts)
    resolved = _resolved_directory(source_path, label="source folder")
    if not _is_within(resolved, root) or source_path.absolute() != resolved:
        raise ParaAuditError(f"source folder must not be a symlink: {source}")

    file_count = 0
    markdown_count = 0
    byte_count = 0
    nested_repository_count = 0
    managed_folders: set[str] = set()
    try:
        for directory, directory_names, file_names in os.walk(
            resolved,
            followlinks=False,
            onerror=_raise_walk_error,
        ):
            directory_path = Path(directory)
            directory_names[:] = sorted(
                name for name in directory_names if not (directory_path / name).is_symlink()
            )
            if ".git" in directory_names:
                nested_repository_count += 1
                directory_names.remove(".git")
            for name in file_names:
                candidate = directory_path / name
                if candidate.is_symlink():
                    continue
                stat = candidate.stat(follow_symlinks=False)
                file_count += 1
                byte_count += stat.st_size
                suffix = candidate.suffix.casefold()
                if suffix in MARKDOWN_SUFFIXES:
                    markdown_count += 1
            if directory_path == resolved:
                managed_folders.update(
                    name
                    for name in directory_names
                    if name.casefold() in {"raw", "sources", "knowledge"}
                )
    except OSError as error:
        raise ParaAuditError(f"could not search source folder: {resolved}") from error

    prefix = relative.as_posix().rstrip("/") + "/"
    link_stats = _folder_link_stats(snapshot, prefix)
    raw_file_count = file_count - markdown_count
    suggested_destination, action, reasons = _recommendation(
        relative,
        markdown_count=markdown_count,
        raw_file_count=raw_file_count,
        inbound_count=link_stats.external_inbound_count,
        nested_repository_count=nested_repository_count,
        managed_folders=managed_folders,
    )
    risk = _migration_risk(
        markdown_count=markdown_count,
        inbound_count=link_stats.external_inbound_count,
        nested_repository_count=nested_repository_count,
    )
    return FolderAudit(
        source=relative.as_posix(),
        suggested_destination=suggested_destination,
        action=action,
        risk=risk,
        file_count=file_count,
        markdown_count=markdown_count,
        raw_file_count=raw_file_count,
        byte_count=byte_count,
        nested_repository_count=nested_repository_count,
        outgoing_link_count=link_stats.outgoing_count,
        unresolved_outgoing_count=link_stats.unresolved_outgoing_count,
        internal_link_count=link_stats.internal_count,
        external_inbound_count=link_stats.external_inbound_count,
        external_inbound_sources=link_stats.external_inbound_sources[:20],
        existing_managed_folders=tuple(sorted(managed_folders, key=str.casefold)),
        reasons=reasons,
    )


@dataclass(frozen=True)
class _FolderLinkStats:
    outgoing_count: int
    unresolved_outgoing_count: int
    internal_count: int
    external_inbound_count: int
    external_inbound_sources: tuple[str, ...]


def _folder_link_stats(snapshot: GraphSnapshot, prefix: str) -> _FolderLinkStats:
    outgoing = tuple(link for link in snapshot.links if link.source_path.startswith(prefix))
    inbound = tuple(
        link
        for link in snapshot.links
        if link.resolved_path is not None
        and link.resolved_path.startswith(prefix)
        and not link.source_path.startswith(prefix)
    )
    inbound_sources = tuple(
        sorted({link.source_path for link in inbound}, key=lambda value: (value.casefold(), value))
    )
    return _FolderLinkStats(
        outgoing_count=len(outgoing),
        unresolved_outgoing_count=sum(link.resolved_path is None for link in outgoing),
        internal_count=sum(
            link.resolved_path is not None and link.resolved_path.startswith(prefix)
            for link in outgoing
        ),
        external_inbound_count=len(inbound),
        external_inbound_sources=inbound_sources,
    )


def _recommendation(
    relative: PurePath,
    *,
    markdown_count: int,
    raw_file_count: int,
    inbound_count: int,
    nested_repository_count: int,
    managed_folders: set[str],
) -> tuple[str, str, tuple[str, ...]]:
    name = relative.name
    destination = f"Resources/{name}"
    reasons: list[str] = []
    if markdown_count >= 500 or inbound_count >= 100:
        reasons.append("Large or heavily linked collection; migrate in reviewed batches in place.")
        reasons.append(
            "Moving the collection to inbox would create unnecessary path and link churn."
        )
        return "Multiple PARA destinations", "batch migration in place", tuple(reasons)
    if managed_folders:
        reasons.append("The folder already contains part of the raw/sources/knowledge structure.")
        reasons.append(
            "Adopt and normalize it as a concept folder instead of rebuilding via inbox."
        )
        if nested_repository_count:
            reasons.append(
                "A nested Git repository requires an explicit keep, relocate or exclude decision."
            )
        return destination, "adopt existing concept folder", tuple(reasons)
    if raw_file_count and markdown_count <= 1:
        reasons.append(
            "The collection is predominantly raw source material for a Resource concept."
        )
        reasons.append("Pilot raw/source/knowledge creation through a preview-only migration plan.")
        return destination, "pilot resource migration", tuple(reasons)
    reasons.append("The folder needs human review before choosing a final PARA category.")
    reasons.append("Resources is a candidate, not an automatic classification.")
    return destination, "review classification", tuple(reasons)


def _migration_risk(
    *,
    markdown_count: int,
    inbound_count: int,
    nested_repository_count: int,
) -> MigrationRisk:
    if markdown_count >= 500 or inbound_count >= 100 or nested_repository_count:
        return "high"
    if markdown_count >= 25 or inbound_count > 0:
        return "medium"
    return "low"


def _resolved_directory(path: Path, *, label: str) -> Path:
    try:
        resolved = path.expanduser().resolve(strict=True)
    except OSError as error:
        raise ParaAuditError(f"{label} cannot be resolved: {path}") from error
    if not resolved.is_dir():
        raise ParaAuditError(f"{label} is not a directory: {path}")
    return resolved


def _is_within(path: Path, directory: Path) -> bool:
    try:
        path.relative_to(directory)
    except ValueError:
        return False
    return True


def _format_bytes(value: int) -> str:
    amount = float(value)
    for unit in ("B", "KiB", "MiB", "GiB"):
        if amount < 1024 or unit == "GiB":
            return f"{amount:.1f} {unit}"
        amount /= 1024
    raise AssertionError("unreachable")


def _raise_walk_error(error: OSError) -> None:
    raise error
