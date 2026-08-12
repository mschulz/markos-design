from pathlib import Path, PurePath

import pytest

from markos.markdown import (
    MarkdownDiscoveryError,
    MarkdownParseError,
    discover_markdown,
    load_markdown,
    load_markdown_report,
    parse_markdown,
)


def test_discovery_is_recursive_deterministic_and_markdown_only(tmp_path: Path) -> None:
    (tmp_path / "z-note.md").write_text("# Z", encoding="utf-8")
    (tmp_path / "A-note.MARKDOWN").write_text("# A", encoding="utf-8")
    nested = tmp_path / "Projects"
    nested.mkdir()
    (nested / "Beta.md").write_text("# Beta", encoding="utf-8")
    (nested / "ignore.txt").write_text("not Markdown", encoding="utf-8")

    assert discover_markdown(tmp_path) == (
        Path("A-note.MARKDOWN"),
        Path("Projects/Beta.md"),
        Path("z-note.md"),
    )


def test_discovery_does_not_follow_symlinks(tmp_path: Path) -> None:
    outside = tmp_path.parent / "outside-markos-test"
    outside.mkdir()
    (outside / "outside.md").write_text("# Outside", encoding="utf-8")
    (tmp_path / "linked-directory").symlink_to(outside, target_is_directory=True)
    (tmp_path / "linked-file.md").symlink_to(outside / "outside.md")

    assert discover_markdown(tmp_path) == ()


def test_discovery_requires_a_directory(tmp_path: Path) -> None:
    file_path = tmp_path / "note.md"
    file_path.write_text("# Note", encoding="utf-8")

    with pytest.raises(MarkdownDiscoveryError, match="not a directory"):
        discover_markdown(file_path)


def test_parse_frontmatter_title_body_and_links(tmp_path: Path) -> None:
    note = tmp_path / "note.md"
    note.write_text(
        """---
title: Frontmatter title
status: active
tags:
  - markos
---
# Ignored heading

See [[Architecture]], [[Roadmap|the roadmap]], and [the vision](Vision.md).
Repeat [[Architecture]]. Do not parse `[[inline code]]`.
""",
        encoding="utf-8",
    )

    document = parse_markdown(tmp_path, PurePath("note.md"))

    assert document.identifier == "note.md"
    assert document.title == "Frontmatter title"
    assert document.metadata == {
        "title": "Frontmatter title",
        "status": "active",
        "tags": ["markos"],
    }
    assert document.body.startswith("# Ignored heading")
    assert document.links == ("Architecture", "Roadmap", "Vision.md")


@pytest.mark.parametrize(
    ("contents", "expected_title"),
    [
        ("## Introduction\n\n# Heading title\n", "Heading title"),
        ("No heading here.\n", "fallback"),
        ("---\ntitle: '   '\n---\n# Heading title\n", "Heading title"),
    ],
)
def test_parse_title_fallbacks(tmp_path: Path, contents: str, expected_title: str) -> None:
    (tmp_path / "fallback.md").write_text(contents, encoding="utf-8")

    assert parse_markdown(tmp_path, PurePath("fallback.md")).title == expected_title


@pytest.mark.parametrize("path", [PurePath("../outside.md"), PurePath("note.txt")])
def test_parse_rejects_invalid_document_path(tmp_path: Path, path: PurePath) -> None:
    with pytest.raises(MarkdownParseError):
        parse_markdown(tmp_path, path)


def test_parse_rejects_symlink_even_when_target_is_in_vault(tmp_path: Path) -> None:
    (tmp_path / "target.md").write_text("# Target", encoding="utf-8")
    (tmp_path / "link.md").symlink_to(tmp_path / "target.md")

    with pytest.raises(MarkdownParseError, match="regular vault file"):
        parse_markdown(tmp_path, PurePath("link.md"))


def test_parse_rejects_symlinked_parent_directory(tmp_path: Path) -> None:
    target = tmp_path / "target"
    target.mkdir()
    (target / "note.md").write_text("# Target", encoding="utf-8")
    (tmp_path / "linked-directory").symlink_to(target, target_is_directory=True)

    with pytest.raises(MarkdownParseError, match="regular vault file"):
        parse_markdown(tmp_path, PurePath("linked-directory/note.md"))


def test_parse_reports_invalid_utf8(tmp_path: Path) -> None:
    (tmp_path / "broken.md").write_bytes(b"\xff")

    with pytest.raises(MarkdownParseError, match="could not read"):
        parse_markdown(tmp_path, PurePath("broken.md"))


def test_load_discovers_and_parses_without_modifying_files(tmp_path: Path) -> None:
    first = tmp_path / "one.md"
    second = tmp_path / "two.markdown"
    first.write_text("# One\n", encoding="utf-8")
    second.write_text("# Two\n", encoding="utf-8")
    before = {path: path.read_bytes() for path in (first, second)}

    documents = load_markdown(tmp_path)

    assert [document.title for document in documents] == ["One", "Two"]
    assert {path: path.read_bytes() for path in (first, second)} == before


def test_load_report_records_parse_failures_and_continues(tmp_path: Path) -> None:
    (tmp_path / "valid.md").write_text("# Valid", encoding="utf-8")
    (tmp_path / "broken.md").write_text(
        "---\npublication: Make:\n---\n# Broken",
        encoding="utf-8",
    )

    report = load_markdown_report(tmp_path)

    assert [document.identifier for document in report.documents] == ["valid.md"]
    assert [issue.path for issue in report.issues] == ["broken.md"]
    assert "invalid frontmatter" in report.issues[0].error
