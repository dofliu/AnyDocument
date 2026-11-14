"""Read-only views of a stored document: metadata, parts, text and rendered images.

These functions back the viewing endpoints of the API (``/docs/{id}/info``, ``/parts``,
``/text`` and ``/render``). They work on plain paths so they can be used and tested
without the web framework.

Rendering addresses *render units*: pages of a PDF, pages of the PDF rendition of an
Office/ODF/RTF document (made with LibreOffice and cached next to the document),
layouts of a drawing, frames of an image and a single preview for text-like formats.
"""

from __future__ import annotations

import contextlib
import hashlib
import os
import threading
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import anydoc
from anydoc.converters.office_pdf import OFFICE_PDF_SOURCES, make_office_to_pdf
from anydoc.converters.thumbnail import THUMBNAIL_FORMATS, make_thumbnail
from anydoc.core.errors import AnyDocError, ExternalToolMissingError, OperationNotSupportedError
from anydoc.core.model import Document
from anydoc.core.registry import registry
from anydoc.formats.dwg import DwgDocument
from anydoc.formats.dxf import DxfDocument

__all__ = [
    "CACHE_DIR_NAME",
    "IMAGE_MEDIA_TYPES",
    "PartNotFoundError",
    "RenderInfo",
    "RenderedPart",
    "TextSlice",
    "document_info",
    "document_parts",
    "document_text",
    "render_etag",
    "render_formats",
    "render_info",
    "render_part",
    "render_unit",
]

CACHE_DIR_NAME = ".cache"
"""Sub-directory of a session folder that holds derived files (PDF renditions)."""

IMAGE_MEDIA_TYPES: dict[str, str] = {
    "png": "image/png",
    "jpeg": "image/jpeg",
    "webp": "image/webp",
    "svg": "image/svg+xml",
}
"""Media type of every output format of :func:`render_part`."""

_ALIASES = {"jpg": "jpeg"}
_RASTER_SOURCES = ("png", "jpeg", "gif", "bmp", "webp", "tiff")
_SVG_SOURCES = ("svg", "dxf", "dwg")
_UNITS = {
    "pdf": "page",
    "dxf": "layout",
    "dwg": "layout",
    "svg": "image",
    **dict.fromkeys(OFFICE_PDF_SOURCES, "page"),
    **dict.fromkeys(_RASTER_SOURCES, "frame"),
}
_rendition_lock = threading.Lock()


class PartNotFoundError(AnyDocError):
    """Raised when a document has no render unit with the requested index."""

    def __init__(self, part: int, count: int | None = None) -> None:
        self.part = part
        self.count = count
        hint = "" if count is None else f" (the document has {count})"
        super().__init__(f"No such part: {part}{hint}")


@dataclass(frozen=True)
class TextSlice:
    """A window of a document's text, measured in characters."""

    text: str
    offset: int
    length: int
    total: int

    @property
    def truncated(self) -> bool:
        """Whether text remains after this window."""
        return self.offset + self.length < self.total


@dataclass(frozen=True)
class RenderInfo:
    """What can be rendered of a document: unit kind, count and output formats."""

    unit: str
    count: int
    formats: tuple[str, ...]


@dataclass(frozen=True)
class RenderedPart:
    """An encoded image of one render unit."""

    data: bytes
    media_type: str
    format: str


def _normalize_format(fmt: str) -> str:
    key = fmt.lower().lstrip(".")
    return _ALIASES.get(key, key)


def render_unit(format_id: str) -> str:
    """Return what ``/render/{part}`` indexes for a format: page, layout, frame, ..."""
    return _UNITS.get(format_id, "preview")


def render_formats(format_id: str) -> tuple[str, ...]:
    """Return the image formats a document of ``format_id`` can be rendered to."""
    extra = ("svg",) if format_id in _SVG_SOURCES else ()
    return (*THUMBNAIL_FORMATS, *extra)


def document_info(path: Path, format_id: str | None = None) -> dict[str, Any]:
    """Return metadata, part count, handler capabilities and render options of ``path``.

    Raises:
        UnsupportedFormatError: if no handler exists for the format.
        CorruptFileError: if the document cannot be parsed.
    """
    doc = anydoc.open(path)
    try:
        fmt = format_id or doc.format
        handler = registry.get(doc.format)
        return {
            "metadata": doc.metadata.to_dict(),
            "part_count": len(doc.parts()),
            "capabilities": sorted(str(c) for c in handler.capabilities) if handler else [],
            "render": {"unit": render_unit(fmt), "formats": list(render_formats(fmt))},
        }
    finally:
        doc.close()


def document_parts(path: Path) -> list[dict[str, Any]]:
    """Return the structural parts (pages, sheets, slides, layouts, ...) of ``path``."""
    doc = anydoc.open(path)
    try:
        return [part.to_dict() for part in doc.parts()]
    finally:
        doc.close()


def document_text(path: Path, *, offset: int = 0, limit: int | None = None) -> TextSlice:
    """Return the text of ``path`` in reading order, optionally a character window.

    Args:
        path: The document.
        offset: First character to return (clamped to the text length).
        limit: Largest number of characters to return; ``None`` returns the rest.

    Raises:
        ValueError: if ``offset`` is negative or ``limit`` is not positive.
    """
    if offset < 0:
        raise ValueError("offset must not be negative")
    if limit is not None and limit <= 0:
        raise ValueError("limit must be positive")
    text = anydoc.extract_text(path)
    total = len(text)
    start = min(offset, total)
    window = text[start:] if limit is None else text[start : start + limit]
    return TextSlice(text=window, offset=start, length=len(window), total=total)


def _stamp(path: Path) -> str:
    stat = path.stat()
    return f"{stat.st_mtime_ns:x}-{stat.st_size:x}"


def render_etag(path: Path, part: int, fmt: str, size: int) -> str:
    """Return a strong ETag for one rendering; it changes whenever the file changes."""
    raw = f"{path.name}|{_stamp(path)}|{part}|{_normalize_format(fmt)}|{size}"
    return f'"{hashlib.sha256(raw.encode()).hexdigest()[:32]}"'


def _pdf_rendition(path: Path, format_id: str, cache_dir: Path | None) -> Path:
    """Return the (cached) PDF rendition of an Office document."""
    folder = cache_dir if cache_dir is not None else path.parent / CACHE_DIR_NAME
    target = folder / f"rendition-{_stamp(path)}.pdf"
    with _rendition_lock:
        if target.is_file():
            return target
        folder.mkdir(parents=True, exist_ok=True)
        for stale in folder.glob("rendition-*.pdf"):
            with contextlib.suppress(OSError):
                stale.unlink()
        tmp = folder / f"rendition-{uuid.uuid4().hex}.tmp"
        try:
            make_office_to_pdf(format_id).func(path, tmp)
            os.replace(tmp, target)
        finally:
            with contextlib.suppress(OSError):
                tmp.unlink()
    return target


def _open_drawing(doc: Document) -> DxfDocument:
    if isinstance(doc, DwgDocument):
        if doc.dxf is None:
            raise ExternalToolMissingError(
                "dwg-converter",
                "Rendering DWG needs ODA File Converter or LibreDWG to convert to DXF",
            )
        return doc.dxf
    if isinstance(doc, DxfDocument):
        return doc
    raise OperationNotSupportedError(f"{doc.format} is not a drawing")


def _unit_count(path: Path, format_id: str, cache_dir: Path | None) -> tuple[Path, int]:
    """Return the file to render from and the number of render units."""
    if format_id in OFFICE_PDF_SOURCES:
        path = _pdf_rendition(path, format_id, cache_dir)
    elif format_id not in ("pdf", "dxf", "dwg", *_RASTER_SOURCES):
        return path, 1
    doc = anydoc.open(path)
    try:
        if format_id == "dwg":
            return path, len(_open_drawing(doc).parts())
        return path, len(doc.parts())
    finally:
        doc.close()


def render_info(path: Path, format_id: str, *, cache_dir: Path | None = None) -> RenderInfo:
    """Return the render unit, the number of units and the output formats of ``path``.

    Office/ODF/RTF documents are converted to PDF (cached) to count their pages.

    Raises:
        ExternalToolMissingError: if the format needs LibreOffice or a DWG converter
            that is not installed.
    """
    _, count = _unit_count(path, format_id, cache_dir)
    return RenderInfo(render_unit(format_id), count, render_formats(format_id))


def _render_svg(path: Path, format_id: str, part: int) -> bytes:
    if format_id == "svg":
        anydoc.open(path).close()  # validates the document
        if part != 0:
            raise PartNotFoundError(part, 1)
        return path.read_bytes()
    doc = anydoc.open(path)
    try:
        drawing = _open_drawing(doc)
        names = drawing.layout_names()
        if not 0 <= part < len(names):
            raise PartNotFoundError(part, len(names))
        return drawing.render("svg", layout=names[part])
    finally:
        doc.close()


def render_part(
    path: Path,
    format_id: str,
    part: int = 0,
    *,
    fmt: str = "png",
    size: int = 1024,
    cache_dir: Path | None = None,
) -> RenderedPart:
    """Render one unit of a document to an image.

    Args:
        path: The document.
        format_id: Its format id (as detected at upload).
        part: 0-based render unit: page, layout or frame (see :func:`render_unit`).
        fmt: ``png``, ``jpeg`` (``jpg``), ``webp`` or, for SVG/DXF/DWG, ``svg``.
        size: Longest side of a raster image in pixels (images are never enlarged).
        cache_dir: Where PDF renditions are cached (default: ``.cache`` beside ``path``).

    Raises:
        PartNotFoundError: if ``part`` is out of range.
        OperationNotSupportedError: if the format cannot be rendered to ``fmt``.
        ExternalToolMissingError: if a required external tool is not installed.
        ValueError: if ``fmt`` is unknown or ``size`` is not positive.
    """
    out_format = _normalize_format(fmt)
    if out_format not in IMAGE_MEDIA_TYPES:
        raise ValueError(f"Unsupported render format: {fmt} (expected {sorted(IMAGE_MEDIA_TYPES)})")
    if size <= 0:
        raise ValueError("size must be positive")
    if out_format not in render_formats(format_id):
        raise OperationNotSupportedError(
            f"{format_id} cannot be rendered to {out_format} "
            f"(available: {', '.join(render_formats(format_id))})"
        )
    if part < 0:
        raise PartNotFoundError(part)
    if out_format == "svg":
        data = _render_svg(path, format_id, part)
    else:
        source, count = _unit_count(path, format_id, cache_dir)
        if part >= count:
            raise PartNotFoundError(part, count)
        data = make_thumbnail(source, size=size, fmt=out_format, page=part)
    return RenderedPart(data=data, media_type=IMAGE_MEDIA_TYPES[out_format], format=out_format)
