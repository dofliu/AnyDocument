"""PDF bookmarks (outline): read, replace, add and remove.

Functions take and return PDF ``bytes`` like :mod:`anydoc.formats.pdf.pages`. Page indexes are
0-based. Bookmarks are exposed as a flat, depth-first list where ``level`` (0 = top level)
encodes the hierarchy.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass
from typing import Any

from pypdf import PdfReader, PdfWriter
from pypdf.generic import IndirectObject, NameObject

from anydoc.core.errors import CorruptFileError
from anydoc.formats.pdf.pages import _check, _dump, _reader

__all__ = [
    "Bookmark",
    "add_bookmark",
    "list_bookmarks",
    "remove_bookmarks",
    "set_bookmarks",
]


@dataclass(frozen=True)
class Bookmark:
    """One outline entry. ``page`` is a 0-based page index (``None`` if it has no valid target)."""

    title: str
    page: int | None
    level: int = 0

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serialisable dict."""
        return asdict(self)


def _walk(reader: PdfReader, items: Sequence[Any], level: int, out: list[Bookmark]) -> None:
    for item in items:
        if isinstance(item, list):
            _walk(reader, item, level + 1, out)
            continue
        try:
            page: int | None = reader.get_destination_page_number(item)
        except Exception:
            page = None
        if page is not None and page < 0:
            page = None
        out.append(Bookmark(title=str(item.title), page=page, level=level))


def list_bookmarks(data: bytes, password: str = "") -> list[Bookmark]:
    """Return every bookmark in depth-first order (empty if the PDF has no outline)."""
    reader = _reader(data, password)
    try:
        outline = reader.outline
    except Exception as exc:
        raise CorruptFileError(f"Cannot read PDF outline: {exc}") from exc
    out: list[Bookmark] = []
    _walk(reader, outline, 0, out)
    return out


def _clear_outline(writer: PdfWriter) -> None:
    root = writer._root_object
    if "/Outlines" in root:
        del root["/Outlines"]
    if str(root.get("/PageMode", "")) == "/UseOutlines":
        del root["/PageMode"]


def _coerce(items: Iterable[Bookmark | Mapping[str, Any]]) -> list[Bookmark]:
    out: list[Bookmark] = []
    for it in items:
        if isinstance(it, Bookmark):
            out.append(it)
        else:
            page = it.get("page")
            out.append(
                Bookmark(
                    str(it["title"]), None if page is None else int(page), int(it.get("level", 0))
                )
            )
    return out


def _build(writer: PdfWriter, marks: Sequence[Bookmark], *, base_level: int = 0) -> None:
    count = len(writer.pages)
    parents: list[IndirectObject | None] = []  # parents[level] = outline item at that level
    prev_level = -1
    for mark in marks:
        level = mark.level
        if level < 0:
            raise ValueError(f"Bookmark level must be >= 0: {mark.title!r}")
        if level > prev_level + 1:
            raise ValueError(
                f"Bookmark {mark.title!r} level {level} skips a level (previous was {prev_level})"
            )
        if mark.page is None:
            raise ValueError(f"Bookmark {mark.title!r} has no page")
        (page,) = _check([mark.page], count)
        parent = parents[level - 1] if level > 0 else None
        ref = writer.add_outline_item(title=mark.title, page_number=page, parent=parent)
        del parents[level:]
        parents.append(ref)
        prev_level = level
    if marks:
        writer._root_object[NameObject("/PageMode")] = NameObject("/UseOutlines")


def set_bookmarks(
    data: bytes,
    bookmarks: Iterable[Bookmark | Mapping[str, Any]],
    password: str = "",
) -> bytes:
    """Replace the whole outline with ``bookmarks`` (flat list, hierarchy via ``level``).

    Items may be :class:`Bookmark` objects or dicts with ``title``, ``page`` (0-based) and an
    optional ``level``. A level may be at most one deeper than the previous entry.

    Raises:
        ValueError: On invalid levels or missing pages.
        IndexError: When a page index is outside the document.
    """
    marks = _coerce(bookmarks)
    writer = PdfWriter(clone_from=_reader(data, password))
    _clear_outline(writer)
    _build(writer, marks)
    return _dump(writer)


def add_bookmark(
    data: bytes,
    title: str,
    page: int,
    *,
    level: int = 0,
    password: str = "",
) -> bytes:
    """Append one bookmark at the end of the outline at the given ``level``."""
    existing = list_bookmarks(data, password)
    return set_bookmarks(data, [*existing, Bookmark(title, page, level)], password)


def remove_bookmarks(data: bytes, password: str = "") -> bytes:
    """Remove the entire outline."""
    writer = PdfWriter(clone_from=_reader(data, password))
    _clear_outline(writer)
    return _dump(writer)
