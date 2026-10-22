"""Format handler base class and the handler registry."""

from __future__ import annotations

from abc import ABC, abstractmethod
from importlib.metadata import entry_points
from pathlib import Path
from typing import ClassVar

from anydoc.core.errors import OperationNotSupportedError
from anydoc.core.model import Capability, Document

ENTRY_POINT_GROUP = "anydoc.formats"


class FormatHandler(ABC):
    """Base class for all format handlers."""

    format_id: ClassVar[str]
    extensions: ClassVar[tuple[str, ...]] = ()
    mime_types: ClassVar[tuple[str, ...]] = ()
    capabilities: ClassVar[frozenset[Capability]] = frozenset()

    @classmethod
    def sniff(cls, head: bytes, path: Path | None) -> float:
        """Return a 0..1 confidence that ``head`` (leading bytes) is this format."""
        return 0.0

    @abstractmethod
    def open(self, source: Path | bytes) -> Document:
        """Open a document from a path or raw bytes."""

    def create(self) -> Document:
        """Create a new empty document (only for handlers with ``WRITE``)."""
        raise OperationNotSupportedError(f"{self.format_id} cannot create new documents")


def _norm_ext(ext: str) -> str:
    return ext.lower().lstrip(".")


class HandlerRegistry:
    """Registry of format handlers, queryable by format id, extension or MIME type."""

    def __init__(self) -> None:
        self._handlers: dict[str, type[FormatHandler]] = {}

    def register(
        self, handler: type[FormatHandler], *, replace: bool = False
    ) -> type[FormatHandler]:
        """Register a handler class; usable as a class decorator."""
        fid = handler.format_id
        if fid in self._handlers and not replace:
            raise ValueError(f"Handler already registered for format: {fid}")
        self._handlers[fid] = handler
        return handler

    def unregister(self, format_id: str) -> None:
        """Remove the handler for ``format_id`` (no error if absent)."""
        self._handlers.pop(format_id, None)

    def get(self, format_id: str) -> type[FormatHandler] | None:
        """Return the handler class for a format id, or ``None``."""
        return self._handlers.get(format_id)

    def by_extension(self, ext: str) -> list[type[FormatHandler]]:
        """Return handlers claiming ``ext`` (with or without a leading dot)."""
        key = _norm_ext(ext)
        return [h for h in self._handlers.values() if key in map(_norm_ext, h.extensions)]

    def by_mime(self, mime: str) -> list[type[FormatHandler]]:
        """Return handlers claiming the MIME type."""
        key = mime.lower()
        return [h for h in self._handlers.values() if key in (m.lower() for m in h.mime_types)]

    def all(self) -> list[type[FormatHandler]]:
        """Return all registered handlers sorted by format id."""
        return sorted(self._handlers.values(), key=lambda h: h.format_id)

    def load_entry_points(self) -> list[str]:
        """Load handlers from the ``anydoc.formats`` entry-point group.

        Returns the format ids newly registered. Already-registered formats are skipped.
        """
        loaded: list[str] = []
        for ep in entry_points(group=ENTRY_POINT_GROUP):
            obj = ep.load()
            if (
                isinstance(obj, type)
                and issubclass(obj, FormatHandler)
                and obj.format_id not in self._handlers
            ):
                self.register(obj)
                loaded.append(obj.format_id)
        return loaded


registry = HandlerRegistry()
