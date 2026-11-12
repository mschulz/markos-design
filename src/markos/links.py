"""Deterministic resolution of document link targets within a Markdown root."""

from __future__ import annotations

import posixpath
import re
from collections.abc import Iterable
from pathlib import PurePosixPath
from urllib.parse import unquote

_URI_SCHEME = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*:")
_MARKDOWN_SUFFIXES = (".md", ".markdown")


def resolve_internal_link(
    source_path: str,
    target: str,
    known_paths: Iterable[str],
) -> str | None:
    """Resolve ``target`` to one known Markdown root path, or return ``None``.

    Ambiguous basename matches are deliberately left unresolved. Paths are
    compared case-insensitively while the canonical stored casing is returned.
    """

    cleaned = unquote(target.strip()).replace("\\", "/")
    if not cleaned or cleaned.startswith(("#", "//")) or _URI_SCHEME.match(cleaned):
        return None

    target_path = cleaned.split("#", maxsplit=1)[0].split("?", maxsplit=1)[0].strip()
    if not target_path:
        return None

    paths = tuple(known_paths)
    by_casefold: dict[str, list[str]] = {}
    for path in paths:
        by_casefold.setdefault(path.casefold(), []).append(path)

    source_parent = PurePosixPath(source_path).parent
    rooted = target_path.startswith("/")
    raw = target_path.lstrip("/")
    suffix = PurePosixPath(raw).suffix.casefold()
    path_forms = (
        (raw,)
        if suffix in _MARKDOWN_SUFFIXES
        else tuple(f"{raw}{extension}" for extension in _MARKDOWN_SUFFIXES)
    )

    candidates: list[str] = []
    for form in path_forms:
        if not rooted:
            candidates.append(_normalise(source_parent / form))
        candidates.append(_normalise(PurePosixPath(form)))

    for candidate in dict.fromkeys(candidates):
        if candidate.startswith("../"):
            continue
        matches = by_casefold.get(candidate.casefold(), [])
        if len(matches) == 1:
            return matches[0]
        if len(matches) > 1:
            return None

    if "/" in raw or suffix in _MARKDOWN_SUFFIXES:
        return None

    basename_matches = [
        path for path in paths if PurePosixPath(path).stem.casefold() == raw.casefold()
    ]
    if len(basename_matches) == 1:
        return basename_matches[0]
    return None


def _normalise(path: PurePosixPath) -> str:
    return posixpath.normpath(path.as_posix())
