"""Read-oriented ODT/ODS/ODP documents parsed directly from the ODF package XML."""

from __future__ import annotations

import io
import zipfile
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any, ClassVar
from xml.etree.ElementTree import Element

from defusedxml import ElementTree as SafeET

from anydoc.core.errors import CorruptFileError, SecurityLimitError
from anydoc.core.model import Capability, Document, Metadata, Part
from anydoc.core.registry import FormatHandler

_NS = {
    "office": "urn:oasis:names:tc:opendocument:xmlns:office:1.0",
    "text": "urn:oasis:names:tc:opendocument:xmlns:text:1.0",
    "table": "urn:oasis:names:tc:opendocument:xmlns:table:1.0",
    "draw": "urn:oasis:names:tc:opendocument:xmlns:drawing:1.0",
    "presentation": "urn:oasis:names:tc:opendocument:xmlns:presentation:1.0",
    "meta": "urn:oasis:names:tc:opendocument:xmlns:meta:1.0",
    "dc": "http://purl.org/dc/elements/1.1/",
    "xlink": "http://www.w3.org/1999/xlink",
}

_MIME = {
    "odt": "application/vnd.oasis.opendocument.text",
    "ods": "application/vnd.oasis.opendocument.spreadsheet",
    "odp": "application/vnd.oasis.opendocument.presentation",
}

_MAX_XML_BYTES = 256 * 1024 * 1024
_MAX_REPEAT = 1000


def _q(tag: str) -> str:
    prefix, name = tag.split(":")
    return f"{{{_NS[prefix]}}}{name}"


def _attr(elem: Element, name: str) -> str | None:
    return elem.get(_q(name))


def _inline_text(elem: Element) -> str:
    """Return the text of a ``text:p``/``text:h``/``text:span`` with ODF whitespace elements."""
    parts: list[str] = [elem.text or ""]
    for child in elem:
        tag = child.tag
        if tag == _q("text:s"):
            parts.append(" " * int(_attr(child, "text:c") or 1))
        elif tag == _q("text:tab"):
            parts.append("\t")
        elif tag == _q("text:line-break"):
            parts.append("\n")
        elif tag in (_q("office:annotation"), _q("text:note")):
            pass  # comments and footnote bodies are not part of the running text
        else:
            parts.append(_inline_text(child))
        parts.append(child.tail or "")
    return "".join(parts)


def _col_letters(n: int) -> str:
    out = ""
    while n:
        n, rem = divmod(n - 1, 26)
        out = chr(65 + rem) + out
    return out


def _parse_date(value: str) -> date | datetime:
    return datetime.fromisoformat(value) if "T" in value else date.fromisoformat(value)


@dataclass(frozen=True)
class BlockInfo:
    """A top-level ODT block: ``paragraph``, ``heading`` or ``table``.

    ``level`` is the outline level for headings (else ``None``); ``table`` holds the cell
    texts of a table block; ``list_depth`` is the nesting depth inside ``text:list`` (0 = none).
    """

    kind: str
    text: str = ""
    style: str | None = None
    level: int | None = None
    list_depth: int = 0
    table: list[list[str]] | None = None


@dataclass(frozen=True)
class SheetInfo:
    """An ODS table; ``dimensions`` is the used range (e.g. ``A1:C4``) or ``None`` if empty."""

    index: int
    name: str
    dimensions: str | None
    max_row: int
    max_column: int


@dataclass(frozen=True)
class CellInfo:
    """A non-empty ODS cell.

    ``type`` is one of ``string``, ``number``, ``boolean``, ``date``, ``formula``. For formulas
    ``formula`` holds the ODF expression and ``value`` the cached result (``None`` if absent).
    """

    coordinate: str
    row: int
    column: int
    value: Any
    type: str
    formula: str | None = None


@dataclass(frozen=True)
class ShapeInfo:
    """A frame on an ODP slide: ``text``, ``picture`` or ``table``.

    ``placeholder`` is the ``presentation:class`` (``title``, ``outline``, ...) if any.
    """

    name: str | None
    kind: str
    text: str = ""
    placeholder: str | None = None
    table: list[list[str]] | None = None


@dataclass(frozen=True)
class SlideInfo:
    """An ODP slide with its master page (layout) name, title, shapes and speaker notes."""

    index: int
    name: str | None
    layout: str | None
    title: str | None
    shapes: list[ShapeInfo]
    notes: str = ""


@dataclass
class _Package:
    parts: dict[str, bytes] = field(default_factory=dict)
    raw: bytes = b""


def _load_package(data: bytes) -> _Package:
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            infos = {i.filename: i for i in zf.infolist()}
            if "mimetype" not in infos or "content.xml" not in infos:
                raise CorruptFileError("Not an ODF package: missing mimetype or content.xml")
            pkg = _Package(raw=data)
            for name in ("mimetype", "content.xml", "meta.xml"):
                info = infos.get(name)
                if info is None:
                    continue
                if info.file_size > _MAX_XML_BYTES:
                    raise SecurityLimitError(f"{name} too large: {info.file_size} bytes")
                pkg.parts[name] = zf.read(name)
            pkg.parts["__names__"] = "\n".join(infos).encode("utf-8")
            return pkg
    except zipfile.BadZipFile as exc:
        raise CorruptFileError(f"Invalid ODF package: {exc}") from exc


class OdfDocument:
    """Common base of opened ODF documents; ``save`` writes the original package bytes."""

    format: str

    def __init__(self, pkg: _Package, *, path: Path | None = None) -> None:
        self._pkg = pkg
        self.path = path
        try:
            self._content = SafeET.fromstring(pkg.parts["content.xml"])
            self._meta = (
                SafeET.fromstring(pkg.parts["meta.xml"]) if "meta.xml" in pkg.parts else None
            )
        except Exception as exc:  # defusedxml raises ParseError and its own forbidden-* errors
            raise CorruptFileError(f"Invalid ODF XML: {exc}") from exc
        mimetype = pkg.parts["mimetype"].decode("ascii", "ignore").strip()
        if mimetype != _MIME[self.format]:
            raise CorruptFileError(f"Not an {self.format.upper()} document: {mimetype!r}")
        body = self._content.find("office:body", _NS)
        if body is None:
            raise CorruptFileError("ODF content.xml has no office:body")
        self._body = body
        self.metadata = self._build_metadata()

    @property
    def native(self) -> Element:
        """The parsed ``content.xml`` root element."""
        return self._content

    # -- metadata ----------------------------------------------------------

    def _meta_text(self, path: str) -> str | None:
        if self._meta is None:
            return None
        node = self._meta.find(f"office:meta/{path}", _NS)
        return (node.text or "").strip() or None if node is not None else None

    def _meta_date(self, path: str) -> datetime | None:
        value = self._meta_text(path)
        if value is None:
            return None
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            return None

    def _page_count(self) -> int | None:
        return None

    def _build_metadata(self) -> Metadata:
        keywords = [
            (n.text or "").strip()
            for n in (
                self._meta.findall("office:meta/meta:keyword", _NS)
                if self._meta is not None
                else []
            )
            if (n.text or "").strip()
        ]
        return Metadata(
            format=self.format,
            mime=_MIME[self.format],
            title=self._meta_text("dc:title"),
            author=self._meta_text("meta:initial-creator") or self._meta_text("dc:creator"),
            created=self._meta_date("meta:creation-date"),
            modified=self._meta_date("dc:date"),
            page_count=self._page_count(),
            extra={
                "subject": self._meta_text("dc:subject"),
                "keywords": keywords or None,
                "generator": self._meta_text("meta:generator"),
                "language": self._meta_text("dc:language"),
            },
        )

    # -- Document protocol -------------------------------------------------

    def parts(self) -> list[Part]:
        """Return the structural parts (sheets/slides; none for text documents)."""
        return []

    def text(self) -> str:
        """Return the document text in reading order."""
        raise NotImplementedError

    def save(self, path: Path | None = None) -> Path:
        """Write the original package bytes to ``path`` (default: the source path)."""
        target = path or self.path
        if target is None:
            raise ValueError("No path given and document has no source path")
        target.write_bytes(self._pkg.raw)
        return target

    def close(self) -> None:
        """Release resources (nothing to close)."""


def _table_rows(table: Element) -> list[list[str]]:
    rows: list[list[str]] = []
    for row in table.iter(_q("table:table-row")):
        cells: list[str] = []
        for cell in row:
            if cell.tag not in (_q("table:table-cell"), _q("table:covered-table-cell")):
                continue
            text = "\n".join(
                _inline_text(p) for p in cell.iter() if p.tag in (_q("text:p"), _q("text:h"))
            )
            cells.append(text)
        rows.append(cells)
    return rows


def _walk_blocks(parent: Element, depth: int, out: list[BlockInfo]) -> None:
    for child in parent:
        tag = child.tag
        if tag == _q("text:p"):
            out.append(
                BlockInfo(
                    "paragraph", _inline_text(child), _attr(child, "text:style-name"), None, depth
                )
            )
        elif tag == _q("text:h"):
            level = int(_attr(child, "text:outline-level") or 1)
            out.append(
                BlockInfo(
                    "heading", _inline_text(child), _attr(child, "text:style-name"), level, depth
                )
            )
        elif tag == _q("table:table"):
            rows = _table_rows(child)
            out.append(BlockInfo("table", "\n".join("\t".join(r) for r in rows), table=rows))
        elif tag in (_q("text:list"), _q("text:list-item"), _q("text:list-header")):
            _walk_blocks(child, depth + (1 if tag == _q("text:list") else 0), out)
        elif tag in (_q("text:section"), _q("text:table-of-content"), _q("text:index-body")):
            _walk_blocks(child, depth, out)


class OdtDocument(OdfDocument):
    """An opened ODT text document."""

    format = "odt"

    def __init__(self, pkg: _Package, *, path: Path | None = None) -> None:
        super().__init__(pkg, path=path)
        if self._body.find("office:text", _NS) is None:
            raise CorruptFileError("ODT content.xml has no office:text")

    def blocks(self) -> list[BlockInfo]:
        """Return paragraphs, headings and tables in reading order."""
        out: list[BlockInfo] = []
        _walk_blocks(self._body.find("office:text", _NS), 0, out)  # type: ignore[arg-type]
        return out

    def paragraphs(self) -> list[BlockInfo]:
        """Return paragraphs and headings (excluding table contents)."""
        return [b for b in self.blocks() if b.kind != "table"]

    def headings(self) -> list[BlockInfo]:
        """Return headings with their outline levels."""
        return [b for b in self.blocks() if b.kind == "heading"]

    def tables(self) -> list[list[list[str]]]:
        """Return every top-level table as rows of cell texts."""
        return [b.table for b in self.blocks() if b.table is not None]

    def images(self) -> list[str]:
        """Return the package paths/links of images referenced by the document."""
        return [
            href
            for frame in self._content.iter(_q("draw:image"))
            if (href := frame.get(_q("xlink:href")))
        ]

    def text(self) -> str:
        """Return paragraphs, headings and tables (tab-joined rows) in reading order."""
        return "\n".join(b.text for b in self.blocks())


class OdsDocument(OdfDocument):
    """An opened ODS spreadsheet."""

    format = "ods"

    def __init__(self, pkg: _Package, *, path: Path | None = None) -> None:
        super().__init__(pkg, path=path)
        if self._body.find("office:spreadsheet", _NS) is None:
            raise CorruptFileError("ODS content.xml has no office:spreadsheet")
        self._tables = self._body.findall("office:spreadsheet/table:table", _NS)
        self._cache: dict[int, tuple[list[CellInfo], int, int]] = {}

    def _page_count(self) -> int | None:
        return None

    def _scan(self, index: int) -> tuple[list[CellInfo], int, int]:
        if index in self._cache:
            return self._cache[index]
        cells: list[CellInfo] = []
        max_row = max_col = 0
        row_no = 0
        for row in self._tables[index].iter(_q("table:table-row")):
            row_rep = int(_attr(row, "table:number-rows-repeated") or 1)
            row_cells: list[tuple[int, Element, int]] = []
            col_no = 0
            for cell in row:
                if cell.tag not in (_q("table:table-cell"), _q("table:covered-table-cell")):
                    continue
                col_rep = int(_attr(cell, "table:number-columns-repeated") or 1)
                if self._cell_info(cell, 1, 1) is not None:
                    row_cells.append((col_no + 1, cell, col_rep))
                col_no += col_rep
            for r in range(min(row_rep, _MAX_REPEAT) if row_cells else 0):
                for first_col, cell, col_rep in row_cells:
                    for c in range(min(col_rep, _MAX_REPEAT)):
                        info = self._cell_info(cell, row_no + r + 1, first_col + c)
                        if info is not None:
                            cells.append(info)
                            max_row = max(max_row, info.row)
                            max_col = max(max_col, info.column)
            row_no += row_rep
        self._cache[index] = (cells, max_row, max_col)
        return self._cache[index]

    @staticmethod
    def _cell_info(cell: Element, row: int, col: int) -> CellInfo | None:
        vtype = _attr(cell, "office:value-type")
        formula = _attr(cell, "table:formula")
        text = "\n".join(_inline_text(p) for p in cell.findall("text:p", _NS))
        value: Any
        kind: str
        if vtype in ("float", "percentage", "currency"):
            num = float(_attr(cell, "office:value") or 0)
            value, kind = (int(num) if num.is_integer() else num), "number"
        elif vtype == "date":
            raw = _attr(cell, "office:date-value") or ""
            try:
                value = _parse_date(raw)
            except ValueError:
                value = raw
            kind = "date"
        elif vtype == "boolean":
            value, kind = _attr(cell, "office:boolean-value") == "true", "boolean"
        elif vtype == "string":
            value, kind = _attr(cell, "office:string-value") or text, "string"
        elif vtype == "time":
            value, kind = _attr(cell, "office:time-value") or text, "string"
        elif formula is not None or text:
            value, kind = (text or None), "string"
        else:
            return None
        if formula is not None:
            kind = "formula"
        return CellInfo(
            coordinate=f"{_col_letters(col)}{row}",
            row=row,
            column=col,
            value=value,
            type=kind,
            formula=formula,
        )

    def sheets(self) -> list[SheetInfo]:
        """Return every sheet with its used range."""
        out: list[SheetInfo] = []
        for i, table in enumerate(self._tables):
            _, max_row, max_col = self._scan(i)
            dims = f"A1:{_col_letters(max_col)}{max_row}" if max_row else None
            out.append(
                SheetInfo(i, _attr(table, "table:name") or f"Sheet{i + 1}", dims, max_row, max_col)
            )
        return out

    def _index(self, sheet: int | str) -> int:
        if isinstance(sheet, int):
            if not 0 <= sheet < len(self._tables):
                raise IndexError(f"No such sheet: {sheet}")
            return sheet
        for s in self.sheets():
            if s.name == sheet:
                return s.index
        raise KeyError(f"No such sheet: {sheet}")

    def cells(self, sheet: int | str = 0) -> list[CellInfo]:
        """Return the non-empty cells of a sheet (by index or name) in row-major order."""
        return list(self._scan(self._index(sheet))[0])

    def cell(self, sheet: int | str, coordinate: str) -> CellInfo | None:
        """Return the cell at ``coordinate`` (e.g. ``B2``) or ``None`` if empty."""
        coord = coordinate.upper()
        return next((c for c in self.cells(sheet) if c.coordinate == coord), None)

    def rows(self, sheet: int | str = 0) -> list[list[Any]]:
        """Return the used range as rows of values (``None`` for empty cells)."""
        cells, max_row, max_col = self._scan(self._index(sheet))
        grid: list[list[Any]] = [[None] * max_col for _ in range(max_row)]
        for c in cells:
            grid[c.row - 1][c.column - 1] = c.value
        return grid

    def parts(self) -> list[Part]:
        """Return one part per sheet."""
        return [Part(kind="sheet", index=s.index, name=s.name) for s in self.sheets()]

    def text(self) -> str:
        """Return each sheet's name followed by its rows (tab-joined values)."""
        blocks: list[str] = []
        for s in self.sheets():
            lines = [
                "\t".join("" if v is None else str(v) for v in row) for row in self.rows(s.index)
            ]
            blocks.append("\n".join([s.name, *lines]))
        return "\n\n".join(blocks)


def _frame_info(frame: Element) -> ShapeInfo:
    name = _attr(frame, "draw:name")
    placeholder = frame.get(_q("presentation:class"))
    if frame.find("table:table", _NS) is not None:
        rows = _table_rows(frame.find("table:table", _NS))  # type: ignore[arg-type]
        return ShapeInfo(name, "table", "\n".join("\t".join(r) for r in rows), placeholder, rows)
    if frame.find("draw:image", _NS) is not None:
        return ShapeInfo(name, "picture", "", placeholder)
    box = frame.find("draw:text-box", _NS)
    lines: list[str] = []
    if box is not None:
        blocks: list[BlockInfo] = []
        _walk_blocks(box, 0, blocks)
        lines = [b.text for b in blocks]
    return ShapeInfo(name, "text", "\n".join(lines), placeholder)


class OdpDocument(OdfDocument):
    """An opened ODP presentation."""

    format = "odp"

    def __init__(self, pkg: _Package, *, path: Path | None = None) -> None:
        super().__init__(pkg, path=path)
        if self._body.find("office:presentation", _NS) is None:
            raise CorruptFileError("ODP content.xml has no office:presentation")
        self._pages = self._body.findall("office:presentation/draw:page", _NS)

    def _page_count(self) -> int | None:
        return len(self._body.findall("office:presentation/draw:page", _NS))

    def _slide_info(self, index: int, page: Element) -> SlideInfo:
        shapes = [_frame_info(f) for f in page.findall("draw:frame", _NS)]
        notes_node = page.find("presentation:notes", _NS)
        notes = ""
        if notes_node is not None:
            notes = "\n".join(
                s.text
                for f in notes_node.findall("draw:frame", _NS)
                if f.get(_q("presentation:class")) == "notes" and (s := _frame_info(f)).text
            )
        title = next((s.text for s in shapes if s.placeholder == "title"), None)
        return SlideInfo(
            index=index,
            name=_attr(page, "draw:name"),
            layout=_attr(page, "draw:master-page-name"),
            title=title,
            shapes=shapes,
            notes=notes,
        )

    def slides(self) -> list[SlideInfo]:
        """Return every slide in presentation order."""
        return [self._slide_info(i, p) for i, p in enumerate(self._pages)]

    def slide(self, index: int) -> SlideInfo:
        """Return one slide by 0-based index."""
        if not 0 <= index < len(self._pages):
            raise IndexError(f"No such slide: {index}")
        return self._slide_info(index, self._pages[index])

    def slide_text(self, index: int) -> str:
        """Return a slide's text (shape order), excluding notes."""
        return "\n".join(s.text for s in self.slide(index).shapes if s.text)

    def parts(self) -> list[Part]:
        """Return one part per slide (named by its title when it has one)."""
        return [
            Part(kind="slide", index=s.index, name=s.title or s.name or f"Slide {s.index + 1}")
            for s in self.slides()
        ]

    def text(self) -> str:
        """Return all slide text in order, each slide followed by its notes."""
        blocks: list[str] = []
        for s in self.slides():
            lines = [sh.text for sh in s.shapes if sh.text]
            if s.notes:
                lines.append(s.notes)
            blocks.append("\n".join(lines))
        return "\n\n".join(blocks)


class OdfHandler(FormatHandler):
    """Base handler for ODF packages; subclasses fix the document class and extension."""

    document_class: ClassVar[type[OdfDocument]]
    capabilities: ClassVar[frozenset[Capability]] = frozenset(
        {Capability.READ, Capability.EXTRACT_TEXT}
    )

    @classmethod
    def sniff(cls, head: bytes, path: Path | None) -> float:
        """Return confidence from the extension (content detection lives in ``detect``)."""
        if path is not None and path.suffix.lower().lstrip(".") in cls.extensions:
            return 0.9
        return 0.0

    def open(self, source: Path | bytes) -> Document:
        """Open an ODF file from a path or bytes; invalid packages raise ``CorruptFileError``."""
        path = source if isinstance(source, Path) else None
        if isinstance(source, Path):
            try:
                data = source.read_bytes()
            except OSError as exc:
                raise CorruptFileError(f"Cannot read {source}: {exc}") from exc
        else:
            data = source
        return self.document_class(_load_package(data), path=path)


class OdtHandler(OdfHandler):
    """Handler for OpenDocument text (``.odt``)."""

    format_id: ClassVar[str] = "odt"
    extensions: ClassVar[tuple[str, ...]] = ("odt",)
    mime_types: ClassVar[tuple[str, ...]] = (_MIME["odt"],)
    document_class = OdtDocument


class OdsHandler(OdfHandler):
    """Handler for OpenDocument spreadsheets (``.ods``)."""

    format_id: ClassVar[str] = "ods"
    extensions: ClassVar[tuple[str, ...]] = ("ods",)
    mime_types: ClassVar[tuple[str, ...]] = (_MIME["ods"],)
    document_class = OdsDocument


class OdpHandler(OdfHandler):
    """Handler for OpenDocument presentations (``.odp``)."""

    format_id: ClassVar[str] = "odp"
    extensions: ClassVar[tuple[str, ...]] = ("odp",)
    mime_types: ClassVar[tuple[str, ...]] = (_MIME["odp"],)
    document_class = OdpDocument
