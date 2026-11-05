"""DOCX document and handler (read, create, basic edit) built on python-docx."""

from __future__ import annotations

import io
import re
from collections.abc import Sequence
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
from anydoc.formats.docx import edit as _edit
from anydoc.formats.docx import layout as _layout
from anydoc.formats.docx import replace as _replace
from anydoc.formats.docx import tables as _tables

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

    def sections(self) -> list[_layout.SectionInfo]:
        """Return the page layout of every section."""
        return [_layout.section_info(self._doc, i) for i in range(len(self._doc.sections))]

    def add_section(self, start_type: str = "new_page") -> int:
        """Append a section (``new_page``, ``continuous``, ``even_page``, ``odd_page``…).

        Returns the new section index.
        """
        _layout.add_section(self._doc, start_type)
        return len(self._doc.sections) - 1

    def set_page_size(
        self,
        paper: str | None = None,
        *,
        width: float | None = None,
        height: float | None = None,
        orientation: str | None = None,
        section: int = -1,
    ) -> None:
        """Set paper size (``A4``, ``Letter``… or ``width``/``height`` mm) and orientation."""
        _layout.set_page_size(
            _layout.section_at(self._doc, section),
            paper,
            width=width,
            height=height,
            orientation=orientation,
        )

    def set_orientation(self, orientation: str, *, section: int = -1) -> None:
        """Set a section to ``portrait`` or ``landscape`` (swaps page dimensions)."""
        _layout.set_orientation(_layout.section_at(self._doc, section), orientation)

    def set_margins(
        self,
        *,
        top: float | None = None,
        bottom: float | None = None,
        left: float | None = None,
        right: float | None = None,
        header: float | None = None,
        footer: float | None = None,
        gutter: float | None = None,
        section: int = -1,
    ) -> None:
        """Set margins in millimetres for a section."""
        _layout.set_margins(
            _layout.section_at(self._doc, section),
            top=top,
            bottom=bottom,
            left=left,
            right=right,
            header=header,
            footer=footer,
            gutter=gutter,
        )

    def set_header(
        self, text: str = "", *, section: int = 0, which: str = "primary", **opts: Any
    ) -> None:
        """Set header text (``{page}``/``{pages}`` become fields); see ``set_header_footer``."""
        _layout.set_header_footer(
            _layout.section_at(self._doc, section), "header", text, which=which, **opts
        )

    def set_footer(
        self, text: str = "", *, section: int = 0, which: str = "primary", **opts: Any
    ) -> None:
        """Set footer text (``{page}``/``{pages}`` become fields); see ``set_header_footer``."""
        _layout.set_header_footer(
            _layout.section_at(self._doc, section), "footer", text, which=which, **opts
        )

    def add_page_number(self, *, section: int = 0, **opts: Any) -> None:
        """Insert a PAGE field (default: centered in the footer)."""
        _layout.add_page_number(_layout.section_at(self._doc, section), **opts)

    def header_footer_fields(
        self, kind: str = "footer", *, section: int = 0, which: str = "primary"
    ) -> list[str]:
        """List field instructions (``PAGE``, ``NUMPAGES``) in a header/footer."""
        return _layout.header_footer_fields(_layout.section_at(self._doc, section), kind, which)

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

    # -- editing -----------------------------------------------------------

    def add_paragraph(
        self,
        text: str = "",
        *,
        style: str | None = None,
        alignment: str | None = None,
        bold: bool | None = None,
        italic: bool | None = None,
        underline: bool | None = None,
        strike: bool | None = None,
        font: str | None = None,
        size: float | None = None,
        color: str | None = None,
    ) -> Paragraph:
        """Append a paragraph; formatting options apply to its text run."""
        return _edit.add_paragraph(
            self._doc,
            text,
            style=style,
            alignment=alignment,
            bold=bold,
            italic=italic,
            underline=underline,
            strike=strike,
            font=font,
            size=size,
            color=color,
        )

    def add_heading(self, text: str, level: int = 1) -> Paragraph:
        """Append a heading (level 0 = Title, 1-9 = Heading n)."""
        return _edit.add_heading(self._doc, text, level)

    def add_list(
        self, items: list[str], *, ordered: bool = False, level: int = 0
    ) -> list[Paragraph]:
        """Append a bulleted (default) or numbered list at nesting ``level`` 0-2."""
        return _edit.add_list(self._doc, items, ordered=ordered, level=level)

    def add_page_break(self) -> None:
        """Append a page break."""
        _edit.add_page_break(self._doc)

    def set_paragraph_text(self, index: int, text: str, **fmt: Any) -> None:
        """Replace the text of body paragraph ``index``, keeping its style."""
        _edit.set_paragraph_text(_edit.paragraph_at(self._doc, index), text, **fmt)

    def insert_paragraph(
        self, index: int, text: str = "", *, style: str | None = None
    ) -> Paragraph:
        """Insert a new paragraph after body paragraph ``index``."""
        return _edit.insert_paragraph_after(_edit.paragraph_at(self._doc, index), text, style)

    def delete_paragraph(self, index: int) -> None:
        """Delete body paragraph ``index``."""
        _edit.delete_paragraph(_edit.paragraph_at(self._doc, index))

    # -- tables and pictures ------------------------------------------------

    def add_table(
        self,
        rows: Sequence[Sequence[Any]] | None = None,
        *,
        n_rows: int | None = None,
        n_cols: int | None = None,
        header: bool = False,
        style: str | None = "Table Grid",
        alignment: str | None = None,
    ) -> int:
        """Append a table from ``rows`` (or an empty grid); returns its table index."""
        _tables.add_table(
            self._doc,
            rows,
            n_rows=n_rows,
            n_cols=n_cols,
            header=header,
            style=style,
            alignment=alignment,
        )
        return len(self._doc.tables) - 1

    def set_cell(self, table: int, row: int, col: int, text: str, **fmt: Any) -> None:
        """Set the text of a table cell (0-based indexes); ``fmt`` formats the run."""
        t = _tables.table_at(self._doc, table)
        _tables.set_cell_text(_tables.cell_at(t, row, col), text, **fmt)

    def merge_cells(self, table: int, top: int, left: int, bottom: int, right: int) -> None:
        """Merge a rectangular cell range (inclusive) of a table."""
        _tables.merge_cells(_tables.table_at(self._doc, table), top, left, bottom, right)

    def set_table_borders(
        self,
        table: int,
        *,
        style: str = "single",
        size: float = 0.5,
        color: str = "000000",
        edges: Sequence[str] | None = None,
    ) -> None:
        """Set borders of a table (``size`` in points)."""
        _tables.set_borders(
            _tables.table_at(self._doc, table), style=style, size=size, color=color, edges=edges
        )

    def shade_cell(self, table: int, row: int, col: int, color: str) -> None:
        """Fill a table cell with a solid ``color``."""
        t = _tables.table_at(self._doc, table)
        _tables.shade_cell(_tables.cell_at(t, row, col), color)

    def add_table_row(
        self, table: int, values: Sequence[Any] | None = None, *, index: int | None = None
    ) -> int:
        """Insert a table row (appended by default); returns its index."""
        return _tables.add_row(_tables.table_at(self._doc, table), values, index=index)

    def delete_table_row(self, table: int, index: int) -> None:
        """Delete a table row."""
        _tables.delete_row(_tables.table_at(self._doc, table), index)

    def add_table_column(
        self, table: int, values: Sequence[Any] | None = None, *, width_mm: float | None = None
    ) -> int:
        """Append a table column; returns its index."""
        return _tables.add_column(_tables.table_at(self._doc, table), values, width_mm=width_mm)

    def delete_table_column(self, table: int, index: int) -> None:
        """Delete a table column."""
        _tables.delete_column(_tables.table_at(self._doc, table), index)

    def set_column_width(self, table: int, index: int, width: float, unit: str = "mm") -> None:
        """Set a table column width (``unit``: mm, pt or in)."""
        _tables.set_column_width(_tables.table_at(self._doc, table), index, width, unit)

    def set_row_height(self, table: int, index: int, height: float, unit: str = "mm") -> None:
        """Set a table row's minimum height (``unit``: mm, pt or in)."""
        _tables.set_row_height(_tables.table_at(self._doc, table), index, height, unit)

    def add_image(
        self,
        source: Path | bytes,
        *,
        width: float | None = None,
        height: float | None = None,
        unit: str = "mm",
        alignment: str | None = None,
    ) -> tuple[int, int]:
        """Append a picture; returns its size ``(width, height)`` in EMU."""
        shape = _tables.add_image(
            self._doc, source, width=width, height=height, unit=unit, alignment=alignment
        )
        return int(shape.width), int(shape.height)

    def replace_text(
        self,
        find: str,
        replace: str,
        *,
        regex: bool = False,
        ignore_case: bool = False,
        whole_word: bool = False,
        count: int = 0,
        body: bool = True,
        headers_footers: bool = True,
    ) -> int:
        """Replace text (across runs, keeping formatting); return the replacement count."""
        return _replace.replace_text(
            self._doc,
            find,
            replace,
            regex=regex,
            ignore_case=ignore_case,
            whole_word=whole_word,
            count=count,
            body=body,
            headers_footers=headers_footers,
        )

    def find_text(
        self,
        find: str,
        *,
        regex: bool = False,
        ignore_case: bool = False,
        whole_word: bool = False,
        body: bool = True,
        headers_footers: bool = True,
    ) -> list[_replace.TextMatch]:
        """Return all matches of ``find`` (body, headers and footers) without editing."""
        return _replace.find_text(
            self._doc,
            find,
            regex=regex,
            ignore_case=ignore_case,
            whole_word=whole_word,
            body=body,
            headers_footers=headers_footers,
        )

    def set_properties(
        self,
        *,
        title: str | None = None,
        author: str | None = None,
        subject: str | None = None,
        keywords: str | None = None,
    ) -> None:
        """Update core properties and refresh ``metadata``."""
        cp = self._doc.core_properties
        if title is not None:
            cp.title = title
        if author is not None:
            cp.author = author
        if subject is not None:
            cp.subject = subject
        if keywords is not None:
            cp.keywords = keywords
        self.metadata = self._build_metadata()

    def to_bytes(self) -> bytes:
        """Serialize the document to DOCX bytes."""
        buf = io.BytesIO()
        self._doc.save(buf)
        return buf.getvalue()

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
        {Capability.READ, Capability.WRITE, Capability.EDIT, Capability.EXTRACT_TEXT}
    )

    @classmethod
    def sniff(cls, head: bytes, path: Path | None) -> float:
        """Return confidence from the extension (content detection lives in ``detect``)."""
        if path is not None and path.suffix.lower() == ".docx":
            return 0.9
        return 0.0

    def create(self) -> DocxDocument:
        """Create a new empty DOCX document (use ``save(path)`` to write it)."""
        return DocxDocument(load_docx())

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
