"""Deterministic parsing of bibliographic citations from Markdown reference lists.

Targets the common "Author, I., Author, I. *Title.* Venue, Month Year." list
format found under a "References" heading. Personal notes vary widely in
citation style, so lines that don't match the expected shape are skipped
rather than raising.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from markdown_it import MarkdownIt
from markdown_it.token import Token

_REFERENCES_HEADING = re.compile(r"^references?$", re.IGNORECASE)
_AUTHOR = re.compile(r",?\s*([^,]+?,\s*(?:[^\s,.]+\.\s*)+)")
_YEAR = re.compile(r"(?P<year>1[5-9]\d{2}|20\d{2})")


@dataclass(frozen=True)
class Citation:
    """One bibliographic entry parsed from a References section."""

    raw: str
    authors: tuple[str, ...]
    title: str
    venue: str | None
    year: int | None
    start_line: int
    end_line: int

    @property
    def source_location(self) -> str:
        if self.start_line == self.end_line:
            return f"L{self.start_line}"
        return f"L{self.start_line}-L{self.end_line}"


def parse_citations(text: str) -> tuple[Citation, ...]:
    """Extract structured citations from a document's References section."""

    tokens = MarkdownIt("commonmark").parse(text)
    citations = []
    for item in _reference_list_items(tokens):
        citation = _parse_citation_line(item)
        if citation is not None:
            citations.append(citation)
    return tuple(citations)


def _reference_list_items(tokens: list[Token]) -> tuple[Token, ...]:
    for index, token in enumerate(tokens):
        if (
            token.type == "heading_open"
            and index + 1 < len(tokens)
            and tokens[index + 1].type == "inline"
            and _REFERENCES_HEADING.fullmatch(tokens[index + 1].content.strip())
        ):
            return _list_items_after(tokens, index + 2)
    return ()


def _list_items_after(tokens: list[Token], start: int) -> tuple[Token, ...]:
    index = start
    while index < len(tokens) and tokens[index].type != "bullet_list_open":
        if tokens[index].type in {"heading_open", "bullet_list_close"}:
            return ()
        index += 1
    if index >= len(tokens):
        return ()

    items: list[Token] = []
    depth = 0
    index += 1
    while index < len(tokens):
        token = tokens[index]
        if token.type == "bullet_list_open":
            depth += 1
        elif token.type == "bullet_list_close":
            if depth == 0:
                break
            depth -= 1
        elif token.type == "inline" and depth == 0:
            items.append(token)
        index += 1
    return tuple(items)


def _parse_citation_line(item: Token) -> Citation | None:
    authors_text: list[str] = []
    title_text: list[str] = []
    remainder_text: list[str] = []
    section = "authors"
    for child in item.children or ():
        if child.type == "em_open":
            section = "title"
        elif child.type == "em_close":
            section = "remainder"
        elif child.type == "text":
            {
                "authors": authors_text,
                "title": title_text,
                "remainder": remainder_text,
            }[section].append(child.content)

    title = "".join(title_text).strip().rstrip(".")
    if not title:
        return None
    authors = _split_authors("".join(authors_text))
    if not authors:
        return None

    remainder = "".join(remainder_text).strip().lstrip(". ").rstrip(".")
    year_match = _YEAR.search(remainder)
    year = int(year_match.group("year")) if year_match else None

    if item.map is None:
        return None
    start_line, end_line_exclusive = item.map

    return Citation(
        raw=item.content.strip(),
        authors=authors,
        title=title,
        venue=remainder or None,
        year=year,
        start_line=start_line + 1,
        end_line=end_line_exclusive,
    )


def _split_authors(text: str) -> tuple[str, ...]:
    return tuple(match.group(1).strip() for match in _AUTHOR.finditer(text))
