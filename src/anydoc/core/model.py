"""Unified document model shared by all format handlers."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from pathlib import Path
from typing import Any, Protocol, runtime_checkable


class Capability(StrEnum):
    """Operations a format handler may support."""

    READ = "read"
    WRITE = "write"
    RENDER = "render"
    EXTRACT_TEXT = "extract_text"
    EDIT = "edit"
    CONVERT = "convert"
    THUMBNAIL = "thumbnail"


@dataclass
class Metadata:
    """Format-independent document metadata."""

    format: str
    mime: str
    title: str | None = None
    author: str | None = None
    created: datetime | None = None
    modified: datetime | None = None
    page_count: int | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-compatible dict (datetimes as ISO 8601 strings)."""
        return {
            "format": self.format,
            "mime": self.mime,
            "title": self.title,
            "author": self.author,
            "created": self.created.isoformat() if self.created else None,
            "modified": self.modified.isoformat() if self.modified else None,
            "page_count": self.page_count,
            "extra": dict(self.extra),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Metadata:
        """Rebuild a ``Metadata`` from :meth:`to_dict` output."""
        created = data.get("created")
        modified = data.get("modified")
        return cls(
            format=data["format"],
            mime=data["mime"],
            title=data.get("title"),
            author=data.get("author"),
            created=datetime.fromisoformat(created) if created else None,
            modified=datetime.fromisoformat(modified) if modified else None,
            page_count=data.get("page_count"),
            extra=dict(data.get("extra") or {}),
        )

    def to_json(self, **kwargs: Any) -> str:
        """Serialize to a JSON string (extra keyword arguments go to ``json.dumps``)."""
        kwargs.setdefault("ensure_ascii", False)
        return json.dumps(self.to_dict(), **kwargs)


@dataclass
class Part:
    """A page, sheet, slide, layer or layout of a document."""

    kind: str
    index: int
    name: str | None = None
    size: tuple[float, float] | None = None

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-compatible dict."""
        return {
            "kind": self.kind,
            "index": self.index,
            "name": self.name,
            "size": list(self.size) if self.size else None,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Part:
        """Rebuild a ``Part`` from :meth:`to_dict` output."""
        size = data.get("size")
        return cls(
            kind=data["kind"],
            index=data["index"],
            name=data.get("name"),
            size=(float(size[0]), float(size[1])) if size else None,
        )


@runtime_checkable
class Document(Protocol):
    """Minimal interface every opened document implements."""

    path: Path | None
    format: str
    metadata: Metadata

    def parts(self) -> list[Part]:
        """Return the document's parts (pages, sheets, slides, ...)."""
        ...

    def text(self) -> str:
        """Return the document's text in reading order."""
        ...

    def save(self, path: Path | None = None) -> Path:
        """Save the document (to ``path`` or in place) and return the written path."""
        ...

    def close(self) -> None:
        """Release any resources held by the document."""
        ...
