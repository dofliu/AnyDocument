"""Read-oriented PDF document and handler built on pypdf and pdfplumber."""

from __future__ import annotations

import io
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, ClassVar

import pdfplumber
import pypdfium2
from pypdf import PdfReader

from anydoc.core.errors import CorruptFileError, OperationNotSupportedError
from anydoc.core.model import Capability, Metadata, Part
from anydoc.core.registry import FormatHandler

_PDF_MIME = "application/pdf"


@dataclass(frozen=True)
class Word:
    """A word with its bounding box in PDF points (origin at the page's top-left)."""

    text: str
    x0: float
    top: float
    x1: float
    bottom: float


@dataclass(frozen=True)
class ImageInfo:
    """An image placed on a page; coordinates are PDF points (top-left origin)."""

    name: str
    x0: float
    top: float
    x1: float
    bottom: float
    src_width: int | None = None
    src_height: int | None = None


@dataclass(frozen=True)
class PageInfo:
    """Page geometry: size is the rotated MediaBox in points, ``rotation`` in degrees."""

    index: int
    width: float
    height: float
    rotation: int


def _opt_date(getter: Any) -> datetime | None:
    try:
        value = getter()
    except Exception:  # malformed date strings
        return None
    return value if isinstance(value, datetime) else None


class PdfDocument:
    """An opened PDF; page content is read lazily through pdfplumber."""

    format = "pdf"

    def __init__(self, data: bytes, *, path: Path | None = None, password: str = "") -> None:
        self.path = path
        self._data = data
        self._reader = PdfReader(io.BytesIO(data))
        self.is_encrypted: bool = self._reader.is_encrypted
        self.is_locked = False
        if self.is_encrypted and not self._reader.decrypt(password):
            self.is_locked = True
        self._plumber: Any = None
        self._password = password
        self.metadata = self._build_metadata()

    # -- helpers -----------------------------------------------------------

    def _require_unlocked(self) -> None:
        if self.is_locked:
            raise OperationNotSupportedError("PDF is encrypted; a valid password is required")

    def _plumber_pdf(self) -> Any:
        self._require_unlocked()
        if self._plumber is None:
            try:
                self._plumber = pdfplumber.open(io.BytesIO(self._data), password=self._password)
            except Exception as exc:
                raise CorruptFileError(f"Cannot parse PDF content: {exc}") from exc
        return self._plumber

    def _plumber_page(self, index: int) -> Any:
        pages = self._plumber_pdf().pages
        if not 0 <= index < len(pages):
            raise IndexError(f"No such page: {index}")
        return pages[index]

    # -- structure ---------------------------------------------------------

    @property
    def page_count(self) -> int | None:
        """Number of pages, or ``None`` while the document is locked."""
        return None if self.is_locked else len(self._reader.pages)

    def pages(self) -> list[PageInfo]:
        """Return geometry for every page."""
        self._require_unlocked()
        out: list[PageInfo] = []
        for i, page in enumerate(self._reader.pages):
            rotation = int(page.rotation) % 360
            width, height = float(page.mediabox.width), float(page.mediabox.height)
            if rotation in (90, 270):
                width, height = height, width
            out.append(PageInfo(index=i, width=width, height=height, rotation=rotation))
        return out

    def page_text(self, index: int) -> str:
        """Return the text of one page (0-based)."""
        return str(self._plumber_page(index).extract_text() or "")

    def words(self, index: int) -> list[Word]:
        """Return the words of one page with bounding boxes."""
        return [
            Word(w["text"], float(w["x0"]), float(w["top"]), float(w["x1"]), float(w["bottom"]))
            for w in self._plumber_page(index).extract_words()
        ]

    def images(self, index: int) -> list[ImageInfo]:
        """Return the images placed on one page."""
        out: list[ImageInfo] = []
        for im in self._plumber_page(index).images:
            src = im.get("srcsize") or (None, None)
            out.append(
                ImageInfo(
                    name=str(im.get("name", "")),
                    x0=float(im["x0"]),
                    top=float(im["top"]),
                    x1=float(im["x1"]),
                    bottom=float(im["bottom"]),
                    src_width=src[0],
                    src_height=src[1],
                )
            )
        return out

    def render_page(
        self,
        index: int,
        *,
        dpi: float = 72.0,
        scale: float | None = None,
        path: Path | None = None,
    ) -> bytes:
        """Render one page (0-based) to PNG with pypdfium2 and return the bytes.

        The page's rotation is applied. ``scale`` (1.0 = 72 DPI) overrides ``dpi`` when given.
        If ``path`` is given the PNG is also written there.
        """
        factor = scale if scale is not None else dpi / 72.0
        if factor <= 0:
            raise ValueError("dpi/scale must be positive")
        self._require_unlocked()
        try:
            pdf = pypdfium2.PdfDocument(self._data, password=self._password or None)
        except Exception as exc:
            raise CorruptFileError(f"Cannot render PDF: {exc}") from exc
        try:
            if not 0 <= index < len(pdf):
                raise IndexError(f"No such page: {index}")
            page = pdf[index]
            try:
                image = page.render(scale=factor).to_pil()
            finally:
                page.close()
        finally:
            pdf.close()
        buf = io.BytesIO()
        image.save(buf, format="PNG", dpi=(factor * 72.0, factor * 72.0))
        data = buf.getvalue()
        if path is not None:
            path.write_bytes(data)
        return data

    # -- Document protocol -------------------------------------------------

    def _build_metadata(self) -> Metadata:
        extra: dict[str, Any] = {
            "encrypted": self.is_encrypted,
            "locked": self.is_locked,
            "pdf_version": self._reader.pdf_header.lstrip("%PDF-") or None,
        }
        if self.is_locked:
            return Metadata(format="pdf", mime=_PDF_MIME, extra=extra)
        info = self._reader.metadata
        extra.update(
            {
                "subject": info.subject if info else None,
                "keywords": info.get("/Keywords") if info else None,
                "creator": info.creator if info else None,
                "producer": info.producer if info else None,
            }
        )
        return Metadata(
            format="pdf",
            mime=_PDF_MIME,
            title=(info.title if info else None) or None,
            author=(info.author if info else None) or None,
            created=_opt_date(lambda: info.creation_date) if info else None,
            modified=_opt_date(lambda: info.modification_date) if info else None,
            page_count=len(self._reader.pages),
            extra=extra,
        )

    def parts(self) -> list[Part]:
        """Return one part per page with its size in points."""
        return [
            Part(kind="page", index=p.index, name=f"Page {p.index + 1}", size=(p.width, p.height))
            for p in self.pages()
        ]

    def text(self) -> str:
        """Return the text of all pages separated by blank lines."""
        return "\n\n".join(self.page_text(i) for i in range(len(self._plumber_pdf().pages)))

    def save(self, path: Path | None = None) -> Path:
        """Write the original bytes to ``path`` (default: the source path) and return it."""
        target = path or self.path
        if target is None:
            raise ValueError("No path given and document has no source path")
        target.write_bytes(self._data)
        return target

    def close(self) -> None:
        """Release the pdfplumber handle if it was opened."""
        if self._plumber is not None:
            self._plumber.close()
            self._plumber = None


class PdfHandler(FormatHandler):
    """Handler for ``.pdf`` documents (read and render; editing arrives in T040+)."""

    format_id: ClassVar[str] = "pdf"
    extensions: ClassVar[tuple[str, ...]] = ("pdf",)
    mime_types: ClassVar[tuple[str, ...]] = (_PDF_MIME,)
    capabilities: ClassVar[frozenset[Capability]] = frozenset(
        {Capability.READ, Capability.EXTRACT_TEXT, Capability.RENDER}
    )

    @classmethod
    def sniff(cls, head: bytes, path: Path | None) -> float:
        """Return confidence from the ``%PDF-`` header, else from the extension."""
        if head.lstrip(b"\x00 \t\r\n")[:5] == b"%PDF-":
            return 1.0
        if path is not None and path.suffix.lower() == ".pdf":
            return 0.5
        return 0.0

    def open(self, source: Path | bytes, *, password: str = "") -> PdfDocument:
        """Open a PDF from a path or bytes; unparsable input raises ``CorruptFileError``.

        An encrypted file that cannot be unlocked with ``password`` opens in a locked state
        (``is_locked``) exposing only encryption status; content access raises
        ``OperationNotSupportedError``.
        """
        path = source if isinstance(source, Path) else None
        if isinstance(source, Path):
            try:
                data = source.read_bytes()
            except OSError as exc:
                raise CorruptFileError(f"Cannot read {source}: {exc}") from exc
        else:
            data = source
        try:
            return PdfDocument(data, path=path, password=password)
        except Exception as exc:  # pypdf raises many types for bad files
            raise CorruptFileError(f"Invalid PDF: {exc}") from exc
