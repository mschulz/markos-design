"""Read-only discovery and parsing of Markdown knowledge documents."""

from __future__ import annotations

import os
import re
from collections.abc import Iterable
from pathlib import Path, PurePath

import frontmatter
from markdown_it import MarkdownIt
from markdown_it.token import Token

from markos.domain import KnowledgeDocument

MARKDOWN_SUFFIXES = frozenset({".md", ".markdown"})
_WIKILINK = re.compile(r"(?<!!)\[\[([^\[\]\n]+?)\]\]")


class MarkdownDiscoveryError(ValueError):
    """Raised when a vault cannot be safely searched for Markdown files."""


class MarkdownParseError(ValueError):
    """Raised when a Markdown document cannot be safely read or parsed."""


def discover_markdown(vault_path: Path) -> tuple[Path, ...]:
    """Return Markdown paths relative to ``vault_path`` in stable order.

    Directory symlinks and file symlinks are deliberately excluded so discovery
    cannot escape the configured vault. The operation never creates or modifies
    files.
    """

    try:
        root = vault_path.expanduser().resolve(strict=True)
    except OSError as error:
        raise MarkdownDiscoveryError(f"vault cannot be resolved: {vault_path}") from error

    if not root.is_dir():
        raise MarkdownDiscoveryError(f"vault is not a directory: {vault_path}")

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
        raise MarkdownDiscoveryError(f"could not search vault {vault_path}: {error}") from error

    return tuple(sorted(discovered, key=lambda path: (path.as_posix().casefold(), path.as_posix())))


def parse_markdown(vault_path: Path, relative_path: PurePath) -> KnowledgeDocument:
    """Parse one vault-relative Markdown file without modifying the vault."""

    root = _resolved_vault(vault_path)
    path = Path(relative_path)
    if path.is_absolute() or ".." in path.parts:
        raise MarkdownParseError("document path must be relative to the vault")
    if path.suffix.casefold() not in MARKDOWN_SUFFIXES:
        raise MarkdownParseError("document path must identify a Markdown file")

    source_path = root / path
    try:
        resolved_source = source_path.resolve(strict=True)
        resolved_source.relative_to(root)
    except (OSError, ValueError) as error:
        message = f"document is not a readable vault file: {path.as_posix()}"
        raise MarkdownParseError(message) from error

    if source_path.absolute() != resolved_source or not resolved_source.is_file():
        raise MarkdownParseError(f"document is not a regular vault file: {path.as_posix()}")

    try:
        source = resolved_source.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as error:
        raise MarkdownParseError(f"could not read Markdown document: {path.as_posix()}") from error

    try:
        post = frontmatter.loads(source)
    except Exception as error:
        raise MarkdownParseError(f"invalid frontmatter in: {path.as_posix()}") from error

    body = post.content
    tokens = MarkdownIt("commonmark").parse(body)
    metadata = dict(post.metadata)

    return KnowledgeDocument(
        path=PurePath(path.as_posix()),
        title=_document_title(metadata.get("title"), tokens, path),
        body=body,
        metadata=metadata,
        links=_document_links(tokens),
    )


def load_markdown(vault_path: Path) -> tuple[KnowledgeDocument, ...]:
    """Discover and parse every Markdown document in a vault."""

    return tuple(parse_markdown(vault_path, path) for path in discover_markdown(vault_path))


def _resolved_vault(vault_path: Path) -> Path:
    try:
        root = vault_path.expanduser().resolve(strict=True)
    except OSError as error:
        raise MarkdownParseError(f"vault cannot be resolved: {vault_path}") from error
    if not root.is_dir():
        raise MarkdownParseError(f"vault is not a directory: {vault_path}")
    return root


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
