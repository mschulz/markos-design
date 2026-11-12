"""Read-only discovery and parsing of Markdown knowledge documents."""

from __future__ import annotations

import os
import re
from collections.abc import Iterable
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path, PurePath

import frontmatter
from markdown_it import MarkdownIt
from markdown_it.token import Token

from markos._compat import legacy_keywords
from markos.domain import KnowledgeDocument

MARKDOWN_SUFFIXES = frozenset({".md", ".markdown"})
_WIKILINK = re.compile(r"(?<!!)\[\[([^\[\]\n]+?)\]\]")


class MarkdownDiscoveryError(ValueError):
    """Raised when a Markdown root cannot be safely searched for Markdown files."""


class MarkdownParseError(ValueError):
    """Raised when a Markdown document cannot be safely read or parsed."""


@dataclass(frozen=True)
class IngestionIssue:
    """One document rejected during a resilient Markdown root load."""

    path: str
    error: str
    source_hash: str | None = None


@dataclass(frozen=True)
class MarkdownLoadReport:
    """Successfully parsed documents and explicit rejection diagnostics."""

    documents: tuple[KnowledgeDocument, ...]
    issues: tuple[IngestionIssue, ...]


@legacy_keywords
def discover_markdown(markdown_root: Path) -> tuple[Path, ...]:
    """Return Markdown paths relative to ``markdown_root`` in stable order.

    Directory symlinks and file symlinks are deliberately excluded so discovery
    cannot escape the configured Markdown root. The operation never creates or modifies
    files.
    """

    try:
        root = markdown_root.expanduser().resolve(strict=True)
    except OSError as error:
        raise MarkdownDiscoveryError(
            f"markdown_root cannot be resolved: {markdown_root}"
        ) from error

    if not root.is_dir():
        raise MarkdownDiscoveryError(f"markdown_root is not a directory: {markdown_root}")

    discovered: list[Path] = []
    try:
        for directory, directory_names, file_names in os.walk(
            root,
            followlinks=False,
            onerror=_raise_walk_error,
        ):
            directory_path = Path(directory)
            directory_names[:] = sorted(
                (name for name in directory_names if not (directory_path / name).is_symlink()),
                key=str.casefold,
            )
            for name in file_names:
                candidate = directory_path / name
                if candidate.is_symlink() or candidate.suffix.casefold() not in MARKDOWN_SUFFIXES:
                    continue
                discovered.append(candidate.relative_to(root))
    except OSError as error:
        raise MarkdownDiscoveryError(
            f"could not search markdown_root {markdown_root}: {error}"
        ) from error

    return tuple(sorted(discovered, key=lambda path: (path.as_posix().casefold(), path.as_posix())))


@legacy_keywords
def parse_markdown(markdown_root: Path, relative_path: PurePath) -> KnowledgeDocument:
    """Parse one root-relative Markdown file without modifying the Markdown root."""

    root = _resolved_markdown_root(markdown_root)
    path = Path(relative_path)
    if path.is_absolute() or ".." in path.parts:
        raise MarkdownParseError("document path must be relative to the Markdown root")
    if path.suffix.casefold() not in MARKDOWN_SUFFIXES:
        raise MarkdownParseError("document path must identify a Markdown file")

    source_path = root / path
    try:
        resolved_source = source_path.resolve(strict=True)
        resolved_source.relative_to(root)
    except (OSError, ValueError) as error:
        message = f"document is not a readable Markdown file: {path.as_posix()}"
        raise MarkdownParseError(message) from error

    if source_path.absolute() != resolved_source or not resolved_source.is_file():
        raise MarkdownParseError(f"document is not a regular Markdown file: {path.as_posix()}")

    try:
        source_bytes = resolved_source.read_bytes()
        source = source_bytes.decode("utf-8")
    except (OSError, UnicodeError) as error:
        raise MarkdownParseError(f"could not read Markdown document: {path.as_posix()}") from error

    try:
        post = frontmatter.loads(source)
    except Exception as error:
        message = f"invalid frontmatter in {path.as_posix()}: {error}"
        raise MarkdownParseError(message) from error

    body = post.content
    tokens = MarkdownIt("commonmark").parse(body)
    metadata = dict(post.metadata)

    return KnowledgeDocument(
        path=PurePath(path.as_posix()),
        title=_document_title(metadata.get("title"), tokens, path),
        body=body,
        metadata=metadata,
        links=_document_links(tokens),
        source_hash=sha256(source_bytes).hexdigest(),
    )


@legacy_keywords
def load_markdown(markdown_root: Path) -> tuple[KnowledgeDocument, ...]:
    """Discover and parse every Markdown document in a Markdown root."""

    return tuple(parse_markdown(markdown_root, path) for path in discover_markdown(markdown_root))


@legacy_keywords
def load_markdown_report(markdown_root: Path) -> MarkdownLoadReport:
    """Parse all discoverable documents while recording per-file failures."""

    documents: list[KnowledgeDocument] = []
    issues: list[IngestionIssue] = []
    for path in discover_markdown(markdown_root):
        try:
            documents.append(parse_markdown(markdown_root, path))
        except MarkdownParseError as error:
            issues.append(
                IngestionIssue(
                    path=path.as_posix(),
                    error=str(error),
                    source_hash=_source_hash(markdown_root, path),
                )
            )
    return MarkdownLoadReport(documents=tuple(documents), issues=tuple(issues))


def _resolved_markdown_root(markdown_root: Path) -> Path:
    try:
        root = markdown_root.expanduser().resolve(strict=True)
    except OSError as error:
        raise MarkdownParseError(f"markdown_root cannot be resolved: {markdown_root}") from error
    if not root.is_dir():
        raise MarkdownParseError(f"markdown_root is not a directory: {markdown_root}")
    return root


def _source_hash(markdown_root: Path, relative_path: Path) -> str | None:
    try:
        content = (markdown_root.expanduser().resolve(strict=True) / relative_path).read_bytes()
    except OSError:
        return None
    return sha256(content).hexdigest()


def _raise_walk_error(error: OSError) -> None:
    raise error


def _document_title(metadata_title: object, tokens: Iterable[Token], path: Path) -> str:
    if isinstance(metadata_title, str) and metadata_title.strip():
        return metadata_title.strip()

    token_list = list(tokens)
    for index, token in enumerate(token_list[:-1]):
        if token.type != "heading_open" or token.tag != "h1":
            continue
        inline = token_list[index + 1]
        if inline.type == "inline" and inline.content.strip():
            return inline.content.strip()

    return path.stem


def _document_links(tokens: Iterable[Token]) -> tuple[str, ...]:
    links: list[str] = []
    seen: set[str] = set()

    def add(target: str) -> None:
        cleaned = target.strip()
        if cleaned and cleaned not in seen:
            seen.add(cleaned)
            links.append(cleaned)

    for token in tokens:
        if token.type != "inline":
            continue
        for child in token.children or ():
            if child.type == "link_open":
                href = child.attrGet("href")
                if href is not None:
                    add(str(href))
            elif child.type == "text":
                for match in _WIKILINK.finditer(child.content):
                    add(match.group(1).split("|", maxsplit=1)[0])

    return tuple(links)
