from pathlib import PurePath

import pytest
from pydantic import ValidationError

from markos.domain import KnowledgeDocument


def test_document_uses_markdown_root_relative_path_as_identifier() -> None:
    document = KnowledgeDocument(path=PurePath("Projects/MarkOS.md"), title=" MarkOS ")

    assert document.identifier == "Projects/MarkOS.md"
    assert document.title == "MarkOS"


@pytest.mark.parametrize("path", [PurePath("/outside.md"), PurePath("../outside.md")])
def test_document_rejects_path_outside_markdown_root(path: PurePath) -> None:
    with pytest.raises(ValidationError, match="relative to the Markdown root"):
        KnowledgeDocument(path=path, title="Outside")


def test_document_requires_markdown_path() -> None:
    with pytest.raises(ValidationError, match="Markdown file"):
        KnowledgeDocument(path=PurePath("note.txt"), title="Note")


def test_document_validates_optional_source_hash() -> None:
    document = KnowledgeDocument(
        path=PurePath("note.md"),
        title="Note",
        source_hash="a" * 64,
    )

    assert document.source_hash == "a" * 64
    with pytest.raises(ValidationError):
        KnowledgeDocument(path=PurePath("note.md"), title="Note", source_hash="invalid")
