"""Core domain objects that are independent of storage and user interfaces."""

from pathlib import PurePath
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator


class KnowledgeDocument(BaseModel):
    """A Markdown knowledge document as understood by MarkOS.

    ``path`` is always relative to a Markdown root. Keeping the Markdown root out of the
    model makes documents portable between storage adapters and editors.
    """

    model_config = ConfigDict(frozen=True)

    path: PurePath
    title: str = Field(min_length=1)
    body: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)
    links: tuple[str, ...] = ()
    source_hash: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")

    @field_validator("path")
    @classmethod
    def validate_path(cls, path: PurePath) -> PurePath:
        if path.is_absolute() or ".." in path.parts:
            raise ValueError("document path must be relative to the Markdown root")
        if path.suffix.casefold() not in {".md", ".markdown"}:
            raise ValueError("document path must identify a Markdown file")
        return path

    @field_validator("title")
    @classmethod
    def validate_title(cls, title: str) -> str:
        title = title.strip()
        if not title:
            raise ValueError("document title must not be blank")
        return title

    @property
    def identifier(self) -> str:
        """Return the stable, root-relative identifier for the document."""

        return self.path.as_posix()
