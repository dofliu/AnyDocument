"""Read-oriented DOCX document and handler built on python-docx."""

from __future__ import annotations

import io
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, ClassVar

from defusedxml import DefusedXmlException
from defusedxml import ElementTree as SafeET
from docx import Document as load_docx
from docx.opc.constants import RELATIONSHIP_TYPE as RT
from docx.table import Table
from docx.text.paragraph import Paragraph

from anydoc.core.errors import CorruptFileError
from anydoc.core.model import Capability, Metadata, Part
from anydoc.core.registry import FormatHandler

_W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
_HEADING_RE = re.compile(r"^heading\s*(\d+)$", re.IGNORECASE)
_DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
_FOOTNOTES_PART = "/word/footnotes.xml"
_SKIP_FOOTNOTE_TYPES = {"separator", "continuationSeparator", "continuationNotice"}


@dataclass(frozen=True)
class ParagraphInfo:
    """A body paragraph; ``heading_level`` is 0 for Title, 1-9 for headings, else ``None``."""

    index: int
    text: str
    style: str | None
    heading_level: int | None = None


@dataclass(frozen=True)
class TableInfo:
    """A body table as rows of cell text."""

    index: int
    rows: list[list[str]] = field(default_factory=list)


@dataclass(frozen=True)
class ImageInfo:
    """An image embedded in the document package."""

    name: str
    content_type: str
    size: int


def _heading_level(style: str | None) -> int | None:
    if not style:
        return None
    if style.lower() == "title":
        return 0
    if m := _HEADING_RE.match(style):
        return int(m.group(1))
    return None


def _table_rows(table: Table) -> list[list[str]]:
    return [[cell.text for cell in row.cells] for row in table.rows]


def _footnote_text(footnote: Any) -> str:
    paragraphs = []
    for p in footnote.iter(f"{{{_W_NS}}}p"):
        paragraphs.append("".join(t.text or "" for t in p.iter(f"{{{_W_NS}}}t")))
    return "\n".join(p for p in paragraphs if p)


class DocxDocument:
    """An opened DOCX file exposing its structure; ``save`` rewrites via python-docx."""

    format = "docx"

    def __init__(self, doc: Any, *, path: Path | None = None) -> None:
        self._doc = doc
        self.path = path
        self.metadata = self._build_metadata()

    @property
    def native(self) -> Any:
        """The underlying ``docx.document.Document`` (python-docx object)."""
        return self._doc

    # -- structure ---------------------------------------------------------

    def _iter_body(self) -> list[Paragraph | Table]:
        items: list[Paragraph | Table] = []
        for child in self._doc.element.body.iterchildren():
            if child.tag == f"{{{_W_NS}}}p":
                items.append(Paragraph(child, self._doc))
            elif child.tag == f"{{{_W_NS}}}tbl":
                items.append(Table(child, self._doc))
        return items

    def paragraphs(self) -> list[ParagraphInfo]:
        """Return body paragraphs (excluding tables) with style and heading level."""
        result: list[ParagraphInfo] = []
        for i, p in enumerate(self._doc.paragraphs):
            style = p.style.name if p.style is not None else None
            result.append(ParagraphInfo(i, p.text, style, _heading_level(style)))
        return result

    def headings(self) -> list[ParagraphInfo]:
        """Return paragraphs that are headings (Title counts as level 0)."""
        return [p for p in self.paragraphs() if p.heading_level is not None]

    def tables(self) -> list[TableInfo]:
        """Return body tables as rows of cell text."""
        return [TableInfo(i, _table_rows(t)) for i, t in enumerate(self._doc.tables)]

    def images(self) -> list[ImageInfo]:
        """Return images referenced from the main document part."""
        found: list[ImageInfo] = []
        seen: set[str] = set()
        for rel in self._doc.part.rels.values():
            if rel.reltype != RT.IMAGE or rel.is_external:
                continue
            part = rel.target_part
            name = str(part.partname)
            if name in seen:
                continue
            seen.add(name)
            found.append(ImageInfo(name.rsplit("/", 1)[-1], part.content_type, len(part.blob)))
        return found

    def headers_footers(self) -> list[dict[str, str]]:
        """Return per-section header/footer text as ``{"header": ..., "footer": ...}``."""
        out: list[dict[str, str]] = []
        for section in self._doc.sections:
            out.append(
                {
                    "header": "\n".join(p.text for p in section.header.paragraphs if p.text),
                    "footer": "\n".join(p.text for p in section.footer.paragraphs if p.text),
                }
            )
        return out

    def footnotes(self) -> list[str]:
        """Return footnote texts (separators excluded), in id order."""
        part = next(
            (p for p in self._doc.part.package.iter_parts() if str(p.partname) == _FOOTNOTES_PART),
            None,
        )
        if part is None:
            return []
        try:
            root = SafeET.fromstring(part.blob)
        except (SafeET.ParseError, DefusedXmlException) as exc:
            raise CorruptFileError(f"Invalid footnotes part: {exc}") from exc
        notes: list[str] = []
        for fn in root.iter(f"{{{_W_NS}}}footnote"):
            if fn.get(f"{{{_W_NS}}}type") in _SKIP_FOOTNOTE_TYPES:
                continue
            if text := _footnote_text(fn):
                notes.append(text)
        return notes

    # -- Document protocol -------------------------------------------------

    def _build_metadata(self) -> Metadata:
        cp = self._doc.core_properties
        headings = [{"level": h.heading_level, "title": h.text} for h in self.headings() if h.text]
        return Metadata(
            format="docx",
            mime=_DOCX_MIME,
            title=cp.title or None,
            author=cp.author or None,
            created=cp.created,
            modified=cp.modified,
            page_count=None,
            extra={
                "subject": cp.subject or None,
                "keywords": cp.keywords or None,
                "last_modified_by": cp.last_modified_by or None,
                "paragraph_count": len(self._doc.paragraphs),
                "table_count": len(self._doc.tables),
                "image_count": len(self.images()),
                "section_count": len(self._doc.sections),
                "footnote_count": len(self.footnotes()),
                "headings": headings,
            },
        )

    def parts(self) -> list[Part]:
        """Return one part per section (page size in points)."""
        result: list[Part] = []
        for i, s in enumerate(self._doc.sections):
            size = None
            if s.page_width is not None and s.page_height is not None:
                size = (s.page_width.pt, s.page_height.pt)
            result.append(Part(kind="section", index=i, size=size))
        return result

    def text(self) -> str:
        """Return body text in reading order; table rows are tab-separated lines."""
        lines: list[str] = []
        for item in self._iter_body():
            if isinstance(item, Paragraph):
                lines.append(item.text)
            else:
                lines.extend("\t".join(row) for row in _table_rows(item))
        return "\n".join(lines)

    def save(self, path: Path | None = None) -> Path:
        """Write the document to ``path`` (default: the source path) and return it."""
        target = path or self.path
        if target is None:
            raise ValueError("No path given and document has no source path")
        self._doc.save(str(target))
        return target

    def close(self) -> None:
        """No resources to release."""


class DocxHandler(FormatHandler):
    """Handler for Word ``.docx`` documents."""

    format_id: ClassVar[str] = "docx"
    extensions: ClassVar[tuple[str, ...]] = ("docx",)
    mime_types: ClassVar[tuple[str, ...]] = (_DOCX_MIME,)
    capabilities: ClassVar[frozenset[Capability]] = frozenset(
        {Capability.READ, Capability.EXTRACT_TEXT}
    )

    @classmethod
    def sniff(cls, head: bytes, path: Path | None) -> float:
        """Return confidence from the extension (content detection lives in ``detect``)."""
        if path is not None and path.suffix.lower() == ".docx":
            return 0.9
        return 0.0

    def open(self, source: Path | bytes) -> DocxDocument:
        """Open a DOCX from a path or bytes; invalid packages raise ``CorruptFileError``."""
        path = source if isinstance(source, Path) else None
        if isinstance(source, Path):
            try:
                data = source.read_bytes()
            except OSError as exc:
                raise CorruptFileError(f"Cannot read {source}: {exc}") from exc
        else:
            data = source
        try:
            return DocxDocument(load_docx(io.BytesIO(data)), path=path)
        except CorruptFileError:
            raise
        except Exception as exc:  # python-docx raises many types for bad packages
            raise CorruptFileError(f"Invalid DOCX: {exc}") from exc
