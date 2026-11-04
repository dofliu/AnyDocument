"""Table and picture helpers for DOCX documents (python-docx based)."""

from __future__ import annotations

import copy
import io
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Emu, Length, Mm, Pt
from docx.table import Table, _Cell

from anydoc.core.errors import OperationNotSupportedError
from anydoc.formats.docx.edit import format_run, parse_color

BORDER_STYLES = frozenset(
    {"single", "double", "dotted", "dashed", "thick", "dashDotStroked", "wave", "none"}
)
_EDGES = ("top", "left", "bottom", "right", "insideH", "insideV")
_ALIGN = {
    "left": WD_ALIGN_PARAGRAPH.LEFT,
    "center": WD_ALIGN_PARAGRAPH.CENTER,
    "right": WD_ALIGN_PARAGRAPH.RIGHT,
}


def _length(value: float, unit: str) -> Length:
    if unit == "mm":
        return Mm(value)
    if unit == "pt":
        return Pt(value)
    if unit == "in":
        return Emu(int(value * 914400))
    raise ValueError(f"Unknown unit: {unit!r} (use mm, pt or in)")


def table_at(doc: Any, index: int) -> Table:
    """Return body table ``index`` (0-based, negative allowed)."""
    tables = doc.tables
    try:
        return tables[index]  # type: ignore[no-any-return]
    except IndexError:
        raise OperationNotSupportedError(
            f"Table index {index} out of range (0..{len(tables) - 1})"
        ) from None


def cell_at(table: Table, row: int, col: int) -> _Cell:
    """Return the cell at ``row``/``col`` (0-based), validating bounds."""
    n_rows, n_cols = len(table.rows), len(table.columns)
    if not (0 <= row < n_rows and 0 <= col < n_cols):
        raise OperationNotSupportedError(
            f"Cell ({row}, {col}) out of range for a {n_rows}x{n_cols} table"
        )
    return table.cell(row, col)


def set_cell_text(cell: _Cell, text: str, **fmt: Any) -> None:
    """Replace a cell's content with ``text`` (first paragraph kept, others dropped)."""
    if fmt.get("color") is not None:
        parse_color(fmt["color"])
    paragraphs = cell.paragraphs
    for extra in paragraphs[1:]:
        extra._p.getparent().remove(extra._p)
    first = paragraphs[0]
    for run in list(first.runs):
        run._r.getparent().remove(run._r)
    format_run(first.add_run(text), **fmt)


def add_table(
    doc: Any,
    rows: Sequence[Sequence[Any]] | None = None,
    *,
    n_rows: int | None = None,
    n_cols: int | None = None,
    header: bool = False,
    style: str | None = "Table Grid",
    alignment: str | None = None,
) -> Table:
    """Append a table filled from ``rows`` (or an empty ``n_rows`` x ``n_cols`` grid)."""
    if rows:
        n_rows = len(rows)
        n_cols = max(len(r) for r in rows)
    if not n_rows or not n_cols or n_rows < 1 or n_cols < 1:
        raise ValueError("Table needs rows data or positive n_rows and n_cols")
    if alignment is not None and alignment.lower() not in _ALIGN:
        raise ValueError(f"Unknown table alignment: {alignment!r}")
    table: Table = doc.add_table(rows=n_rows, cols=n_cols)
    if style is not None:
        try:
            table.style = style
        except KeyError:
            table._tbl.getparent().remove(table._tbl)
            raise ValueError(f"Unknown table style: {style!r}") from None
    if alignment is not None:
        table.alignment = _ALIGN[alignment.lower()]  # type: ignore[assignment]
    for r, row in enumerate(rows or ()):
        for c, value in enumerate(row):
            set_cell_text(table.cell(r, c), "" if value is None else str(value))
    if header:
        for cell in table.rows[0].cells:
            for run in cell.paragraphs[0].runs:
                run.bold = True
        _mark_header_row(table.rows[0])
    return table


def _mark_header_row(row: Any) -> None:
    trpr = row._tr.get_or_add_trPr()
    if trpr.find(qn("w:tblHeader")) is None:
        trpr.append(OxmlElement("w:tblHeader"))


def merge_cells(table: Table, top: int, left: int, bottom: int, right: int) -> _Cell:
    """Merge the rectangle ``(top, left)``..``(bottom, right)`` (inclusive) into one cell."""
    if top > bottom or left > right:
        raise ValueError("Merge range must run from top-left to bottom-right")
    start = cell_at(table, top, left)
    end = cell_at(table, bottom, right)
    if start._tc is end._tc:
        return start
    merged: _Cell = start.merge(end)
    return merged


def set_borders(
    table: Table,
    *,
    style: str = "single",
    size: float = 0.5,
    color: str = "000000",
    edges: Sequence[str] | None = None,
) -> None:
    """Set table borders (``size`` in points; ``edges`` default to all six)."""
    if style not in BORDER_STYLES:
        raise ValueError(f"Unknown border style: {style!r}")
    parse_color(color)
    chosen = tuple(edges) if edges is not None else _EDGES
    for edge in chosen:
        if edge not in _EDGES:
            raise ValueError(f"Unknown border edge: {edge!r}")
    tbl_pr = table._tbl.tblPr
    borders = tbl_pr.find(qn("w:tblBorders"))
    if borders is None:
        borders = OxmlElement("w:tblBorders")
        tbl_pr.append(borders)
    for edge in chosen:
        el = borders.find(qn(f"w:{edge}"))
        if el is None:
            el = OxmlElement(f"w:{edge}")
            borders.append(el)
        el.set(qn("w:val"), style)
        el.set(qn("w:sz"), str(max(2, round(size * 8))))  # eighths of a point
        el.set(qn("w:space"), "0")
        el.set(qn("w:color"), color.lstrip("#").upper())
    # schema order of tblBorders children
    ordered = sorted(borders, key=lambda e: _EDGES.index(e.tag.split("}")[1]))
    for child in ordered:
        borders.remove(child)
        borders.append(child)


def shade_cell(cell: _Cell, color: str) -> None:
    """Fill a cell with a solid background ``color`` (``RRGGBB``)."""
    parse_color(color)
    tc_pr = cell._tc.get_or_add_tcPr()
    for old in tc_pr.findall(qn("w:shd")):
        tc_pr.remove(old)
    shd = OxmlElement("w:shd")
    shd.set(qn("w:val"), "clear")
    shd.set(qn("w:color"), "auto")
    shd.set(qn("w:fill"), color.lstrip("#").upper())
    tc_pr.append(shd)


def add_row(table: Table, values: Sequence[Any] | None = None, *, index: int | None = None) -> int:
    """Insert a row (appended by default, else before ``index``); returns its index."""
    n_rows = len(table.rows)
    if index is None or index == n_rows:
        row = table.add_row()  # type: ignore[no-untyped-call]
        new_index = n_rows
    else:
        if not 0 <= index < n_rows:
            raise OperationNotSupportedError(f"Row index {index} out of range (0..{n_rows})")
        source = table.rows[index]._tr
        new_tr = copy.deepcopy(source)
        for tc in new_tr.findall(qn("w:tc")):
            for p in tc.findall(qn("w:p"))[1:]:
                tc.remove(p)
            for p in tc.findall(qn("w:p")):
                for child in list(p):
                    if child.tag != qn("w:pPr"):
                        p.remove(child)
        source.addprevious(new_tr)
        row = table.rows[index]
        new_index = index
    if values is not None:
        for c, value in enumerate(values[: len(row.cells)]):
            set_cell_text(row.cells[c], "" if value is None else str(value))
    return new_index


def delete_row(table: Table, index: int) -> None:
    """Delete row ``index``."""
    rows = table.rows
    if not 0 <= index < len(rows):
        raise OperationNotSupportedError(f"Row index {index} out of range (0..{len(rows) - 1})")
    tr = rows[index]._tr
    tr.getparent().remove(tr)


def add_column(
    table: Table, values: Sequence[Any] | None = None, *, width_mm: float | None = None
) -> int:
    """Append a column (optionally filled top-down); returns its index."""
    n_cols = len(table.columns)
    width = Mm(width_mm) if width_mm is not None else table.columns[-1].width
    table.add_column(width or Mm(30))
    if values is not None:
        for r, value in enumerate(values[: len(table.rows)]):
            set_cell_text(table.cell(r, n_cols), "" if value is None else str(value))
    return n_cols


def delete_column(table: Table, index: int) -> None:
    """Delete column ``index`` (every row's cell at that grid position)."""
    n_cols = len(table.columns)
    if not 0 <= index < n_cols:
        raise OperationNotSupportedError(f"Column index {index} out of range (0..{n_cols - 1})")
    grid_cols = table._tbl.tblGrid.findall(qn("w:gridCol"))
    for tr in table._tbl.findall(qn("w:tr")):
        tcs = tr.findall(qn("w:tc"))
        pos = 0
        for tc in tcs:
            span_el = tc.tcPr.find(qn("w:gridSpan")) if tc.tcPr is not None else None
            span = int(span_el.get(qn("w:val"))) if span_el is not None else 1
            if pos <= index < pos + span:
                if span > 1:
                    span_el.set(qn("w:val"), str(span - 1))  # type: ignore[union-attr]
                else:
                    tr.remove(tc)
                break
            pos += span
    grid_cols[index].getparent().remove(grid_cols[index])


def set_column_width(table: Table, index: int, width: float, unit: str = "mm") -> None:
    """Set the width of column ``index`` (grid and every cell)."""
    if width <= 0:
        raise ValueError("Width must be positive")
    length = _length(width, unit)
    if not 0 <= index < len(table.columns):
        raise OperationNotSupportedError(f"Column index {index} out of range")
    table.autofit = False
    table.columns[index].width = length
    for row in table.rows:
        row.cells[index].width = length


def set_row_height(table: Table, index: int, height: float, unit: str = "mm") -> None:
    """Set the minimum height of row ``index``."""
    if height <= 0:
        raise ValueError("Height must be positive")
    rows = table.rows
    if not 0 <= index < len(rows):
        raise OperationNotSupportedError(f"Row index {index} out of range")
    rows[index].height = _length(height, unit)


def add_image(
    doc: Any,
    source: Path | bytes,
    *,
    width: float | None = None,
    height: float | None = None,
    unit: str = "mm",
    alignment: str | None = None,
) -> Any:
    """Append a picture paragraph. A single of ``width``/``height`` keeps the aspect ratio."""
    if (width is not None and width <= 0) or (height is not None and height <= 0):
        raise ValueError("Image width and height must be positive")
    if alignment is not None and alignment.lower() not in _ALIGN:
        raise ValueError(f"Unknown image alignment: {alignment!r}")
    stream: Any = io.BytesIO(source) if isinstance(source, bytes) else str(source)
    paragraph = doc.add_paragraph()
    run = paragraph.add_run()
    try:
        run.add_picture(
            stream,
            width=_length(width, unit) if width is not None else None,
            height=_length(height, unit) if height is not None else None,
        )
    except Exception as exc:
        paragraph._p.getparent().remove(paragraph._p)
        if isinstance(exc, FileNotFoundError):
            raise
        raise ValueError(f"Cannot insert image: {exc}") from exc
    if alignment is not None:
        paragraph.alignment = _ALIGN[alignment.lower()]
    return doc.inline_shapes[-1]
