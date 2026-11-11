"""Table creation and editing for PPTX slides (T072).

A table is addressed by ``(slide index, shape id)`` like any other shape; rows and
columns are 0-based. Lengths follow ``shapes.to_emu`` (EMU numbers or ``"2cm"``).
"""

from __future__ import annotations

import copy
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Any

from pptx.oxml.ns import qn
from pptx.text.text import Font
from pptx.util import Emu

from anydoc.core.errors import AnyDocError
from anydoc.formats.pptx.shapes import (
    _ALIGN,
    _ANCHOR,
    Length,
    ParagraphFormat,
    _align,
    _apply_font,
    _color_of,
    _rgb,
    _run_format,
    _size,
    _slide,
    find_shape,
    to_emu,
)

_MERGE_ATTRS = ("gridSpan", "rowSpan", "hMerge", "vMerge")
_DEFAULT_ROW_HEIGHT = 370840  # PowerPoint's default table row height (EMU)


@dataclass(frozen=True)
class TableInfo:
    """Structure and content of a table as read back from the presentation.

    ``merges`` lists merged regions as ``(first_row, first_col, last_row, last_col)``;
    the text of a merged region lives in its top-left cell.
    """

    shape_id: int
    rows: int
    cols: int
    data: list[list[str]]
    col_widths: list[int]
    row_heights: list[int]
    merges: list[tuple[int, int, int, int]]
    header: bool
    banded: bool
    first_col: bool


@dataclass(frozen=True)
class TableCellFormat:
    """Appearance of one table cell; ``None`` means inherited from the table style."""

    text: str
    fill: str | None
    anchor: str | None
    paragraphs: list[ParagraphFormat]


# -- helpers -----------------------------------------------------------------


def _cell_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def _frame(prs: Any, slide: int, shape_id: int) -> Any:
    shape = find_shape(prs, slide, shape_id)
    if not getattr(shape, "has_table", False):
        raise AnyDocError(f"Shape {shape_id} ({shape.name!r}) is not a table")
    return shape


def _table(prs: Any, slide: int, shape_id: int) -> Any:
    return _frame(prs, slide, shape_id).table


def _rows(table: Any) -> list[Any]:
    return list(table._tbl.findall(qn("a:tr")))


def _grid_cols(table: Any) -> list[Any]:
    return list(table._tbl.find(qn("a:tblGrid")).findall(qn("a:gridCol")))


def _cells(row: Any) -> list[Any]:
    return list(row.findall(qn("a:tc")))


def _check_cell(table: Any, row: int, col: int) -> None:
    if not 0 <= row < len(table.rows) or not 0 <= col < len(table.columns):
        raise IndexError(f"No such cell: ({row}, {col})")


def _write_cell(cell: Any, value: Any) -> None:
    """Set a cell's text, keeping the first paragraph's alignment and font settings."""
    first_p = cell._tc.find(f".//{qn('a:p')}")
    p_pr = r_pr = None
    if first_p is not None:
        p_pr = first_p.find(qn("a:pPr"))
        r_pr = first_p.find(f"{qn('a:r')}/{qn('a:rPr')}")
        if r_pr is None:  # empty cell: formatting waits on the paragraph end mark
            r_pr = first_p.find(qn("a:endParaRPr"))
    kept_p = copy.deepcopy(p_pr) if p_pr is not None else None
    kept_r = copy.deepcopy(r_pr) if r_pr is not None else None
    if kept_r is not None:
        kept_r.tag = qn("a:rPr")
    cell.text = _cell_text(value)
    for para in cell._tc.iter(qn("a:p")):
        if kept_p is not None and para.find(qn("a:pPr")) is None:
            para.insert(0, copy.deepcopy(kept_p))
        if kept_r is None:
            continue
        for run in para.findall(qn("a:r")):
            old = run.find(qn("a:rPr"))
            if old is not None:
                run.remove(old)
            run.insert(0, copy.deepcopy(kept_r))


def _blank_cell(tc: Any) -> None:
    """Empty a copied ``a:tc``: no text, no merge attributes, no extension ids."""
    for attr in _MERGE_ATTRS:
        tc.attrib.pop(attr, None)
    body = tc.find(qn("a:txBody"))
    if body is not None:
        paragraphs = body.findall(qn("a:p"))
        for extra in paragraphs[1:]:
            body.remove(extra)
        for child in list(paragraphs[0]) if paragraphs else []:
            if child.tag in (qn("a:r"), qn("a:br"), qn("a:fld")):
                paragraphs[0].remove(child)


def _strip_ext(element: Any) -> None:
    for ext in element.findall(qn("a:extLst")):
        element.remove(ext)


def _sync_frame(frame: Any) -> None:
    """Make the graphic frame as large as the sum of its rows and columns."""
    table = frame.table
    frame.width = Emu(sum(int(c.width) for c in table.columns))
    frame.height = Emu(sum(int(r.height) for r in table.rows))


def _end_font(paragraph: Any) -> Any:
    """Font of an empty paragraph's end mark: what text typed there later will use."""
    make: Any = Font  # python-pptx ships no type hints for this constructor
    return make(paragraph._p.get_or_add_endParaRPr())


def _indexes(spec: int | Iterable[int] | None, count: int, what: str) -> list[int]:
    if spec is None:
        return list(range(count))
    items = [spec] if isinstance(spec, int) else list(spec)
    for i in items:
        if isinstance(i, bool) or not 0 <= i < count:
            raise IndexError(f"No such {what}: {i}")
    return items


# -- creation ----------------------------------------------------------------


def add_table(
    prs: Any,
    slide: int,
    data: Sequence[Sequence[Any]] | None = None,
    *,
    rows: int | None = None,
    cols: int | None = None,
    left: Length,
    top: Length,
    width: Length,
    height: Length | None = None,
    header: bool = True,
    banded: bool = True,
    first_col: bool = False,
    col_widths: Sequence[Length] | None = None,
    size: float | None = None,
    font: str | None = None,
    align: str | None = None,
) -> int:
    """Add a table and return its shape id.

    ``data`` is a list of rows (the first one is the header when ``header`` is true);
    short rows are padded with empty cells. Without ``data`` an empty ``rows`` x ``cols``
    table is created. ``col_widths`` overrides the even split of ``width``; ``height``
    defaults to PowerPoint's standard row height times the row count. ``size``, ``font``
    and ``align`` apply to every cell.
    """
    matrix = [list(r) for r in data] if data is not None else []
    n_rows = rows if rows is not None else len(matrix)
    n_cols = cols if cols is not None else max((len(r) for r in matrix), default=0)
    if n_rows < 1 or n_cols < 1:
        raise ValueError("A table needs at least one row and one column")
    if len(matrix) > n_rows or any(len(r) > n_cols for r in matrix):
        raise ValueError("data does not fit in the requested rows/cols")
    if col_widths is not None and len(col_widths) != n_cols:
        raise ValueError(f"col_widths needs {n_cols} values")
    if align is not None:
        _align(align)
    if size is not None and size <= 0:
        raise ValueError("size must be positive")
    total_height = _size(height, "height") if height is not None else _DEFAULT_ROW_HEIGHT * n_rows
    frame = _slide(prs, slide).shapes.add_table(
        n_rows, n_cols, to_emu(left), to_emu(top), _size(width, "width"), total_height
    )
    table = frame.table
    table.first_row = header
    table.horz_banding = banded
    table.first_col = first_col
    for r, values in enumerate(matrix):
        for c, value in enumerate(values):
            table.cell(r, c).text = _cell_text(value)
    if col_widths is not None:
        for column, value in zip(table.columns, col_widths, strict=True):
            column.width = Emu(_size(value, "column width"))
        _sync_frame(frame)
    if size is not None or font is not None or align is not None:
        format_cells(prs, slide, frame.shape_id, size=size, font=font, align=align)
    return int(frame.shape_id)


# -- content -----------------------------------------------------------------


def set_cell(prs: Any, slide: int, shape_id: int, row: int, col: int, value: Any) -> None:
    """Set the text of one cell (alignment and font settings of the cell are kept)."""
    table = _table(prs, slide, shape_id)
    _check_cell(table, row, col)
    cell = table.cell(row, col)
    if cell.is_spanned:
        raise AnyDocError(f"Cell ({row}, {col}) is covered by a merged cell")
    _write_cell(cell, value)


def set_data(
    prs: Any,
    slide: int,
    shape_id: int,
    data: Sequence[Sequence[Any]],
    *,
    start_row: int = 0,
    start_col: int = 0,
    resize: bool = False,
) -> None:
    """Write a block of values starting at ``(start_row, start_col)``.

    With ``resize`` the table grows (rows/columns appended) to hold the block;
    otherwise data that does not fit raises ``ValueError``. Cells covered by a merge
    are skipped.
    """
    frame = _frame(prs, slide, shape_id)
    table = frame.table
    if start_row < 0 or start_col < 0:
        raise IndexError("start_row/start_col must not be negative")
    matrix = [list(r) for r in data]
    need_rows = start_row + len(matrix)
    need_cols = start_col + max((len(r) for r in matrix), default=0)
    if need_rows > len(table.rows) or need_cols > len(table.columns):
        if not resize:
            raise ValueError("data does not fit in the table (use resize=True)")
        for _ in range(need_rows - len(table.rows)):
            insert_row(prs, slide, shape_id)
        for _ in range(need_cols - len(table.columns)):
            insert_column(prs, slide, shape_id)
    for r, values in enumerate(matrix, start=start_row):
        for c, value in enumerate(values, start=start_col):
            cell = table.cell(r, c)
            if not cell.is_spanned:
                _write_cell(cell, value)


def table_data(prs: Any, slide: int, shape_id: int) -> list[list[str]]:
    """Return the table's cell texts as a list of rows."""
    table = _table(prs, slide, shape_id)
    return [[cell.text for cell in row.cells] for row in table.rows]


# -- rows and columns --------------------------------------------------------


def insert_row(
    prs: Any,
    slide: int,
    shape_id: int,
    index: int | None = None,
    values: Sequence[Any] | None = None,
) -> int:
    """Insert a row before ``index`` (default: append); return the new row's index.

    The new row copies the height and cell formatting of its neighbour (the row
    above, or the first row when inserting at the top) and is filled with ``values``.
    """
    frame = _frame(prs, slide, shape_id)
    table = frame.table
    rows = _rows(table)
    position = len(rows) if index is None else index
    if not 0 <= position <= len(rows):
        raise IndexError(f"No such row position: {position}")
    if values is not None and len(values) > len(table.columns):
        raise ValueError("Too many values for the table's columns")
    if position < len(rows) and any(tc.get("vMerge") for tc in _cells(rows[position])):
        raise AnyDocError("Cannot insert a row inside a vertically merged region")
    source = rows[position - 1] if position > 0 else rows[0]
    new_row = copy.deepcopy(source)
    _strip_ext(new_row)
    for tc in _cells(new_row):
        _blank_cell(tc)
    if position == len(rows):
        rows[-1].addnext(new_row)
    else:
        rows[position].addprevious(new_row)
    for c, value in enumerate(values or []):
        _write_cell(table.cell(position, c), value)
    _sync_frame(frame)
    return position


def delete_row(prs: Any, slide: int, shape_id: int, index: int) -> None:
    """Delete a row; rows that take part in a vertical merge are refused."""
    frame = _frame(prs, slide, shape_id)
    rows = _rows(frame.table)
    if not 0 <= index < len(rows):
        raise IndexError(f"No such row: {index}")
    if len(rows) == 1:
        raise AnyDocError("Cannot delete the only row of a table")
    if any(tc.get("vMerge") or int(tc.get("rowSpan", "1")) > 1 for tc in _cells(rows[index])):
        raise AnyDocError("Cannot delete a row that is part of a merged region; unmerge first")
    rows[index].getparent().remove(rows[index])
    _sync_frame(frame)


def insert_column(
    prs: Any,
    slide: int,
    shape_id: int,
    index: int | None = None,
    values: Sequence[Any] | None = None,
    *,
    width: Length | None = None,
) -> int:
    """Insert a column before ``index`` (default: append); return the new column's index.

    The column copies the width and cell formatting of its neighbour (the column to
    the left, or the first column when inserting at the start) unless ``width`` is given.
    """
    frame = _frame(prs, slide, shape_id)
    table = frame.table
    grid = _grid_cols(table)
    rows = _rows(table)
    position = len(grid) if index is None else index
    if not 0 <= position <= len(grid):
        raise IndexError(f"No such column position: {position}")
    if values is not None and len(values) > len(rows):
        raise ValueError("Too many values for the table's rows")
    if position < len(grid) and any(_cells(row)[position].get("hMerge") for row in rows):
        raise AnyDocError("Cannot insert a column inside a horizontally merged region")
    source = position - 1 if position > 0 else 0
    new_col = copy.deepcopy(grid[source])
    _strip_ext(new_col)
    if width is not None:
        new_col.set("w", str(_size(width, "width")))
    if position == len(grid):
        grid[-1].addnext(new_col)
    else:
        grid[position].addprevious(new_col)
    for row in rows:
        cells = _cells(row)
        new_tc = copy.deepcopy(cells[source])
        _blank_cell(new_tc)
        if position == len(cells):
            cells[-1].addnext(new_tc)
        else:
            cells[position].addprevious(new_tc)
    for r, value in enumerate(values or []):
        _write_cell(table.cell(r, position), value)
    _sync_frame(frame)
    return position


def delete_column(prs: Any, slide: int, shape_id: int, index: int) -> None:
    """Delete a column; columns that take part in a horizontal merge are refused."""
    frame = _frame(prs, slide, shape_id)
    table = frame.table
    grid = _grid_cols(table)
    if not 0 <= index < len(grid):
        raise IndexError(f"No such column: {index}")
    if len(grid) == 1:
        raise AnyDocError("Cannot delete the only column of a table")
    rows = _rows(table)
    for row in rows:
        tc = _cells(row)[index]
        if tc.get("hMerge") or int(tc.get("gridSpan", "1")) > 1:
            raise AnyDocError(
                "Cannot delete a column that is part of a merged region; unmerge first"
            )
    for row in rows:
        row.remove(_cells(row)[index])
    grid[index].getparent().remove(grid[index])
    _sync_frame(frame)


def set_column_width(prs: Any, slide: int, shape_id: int, col: int, width: Length) -> None:
    """Set the width of one column (the table frame is resized to match)."""
    frame = _frame(prs, slide, shape_id)
    columns = frame.table.columns
    if not 0 <= col < len(columns):
        raise IndexError(f"No such column: {col}")
    columns[col].width = Emu(_size(width, "width"))
    _sync_frame(frame)


def set_row_height(prs: Any, slide: int, shape_id: int, row: int, height: Length) -> None:
    """Set the (minimum) height of one row (the table frame is resized to match)."""
    frame = _frame(prs, slide, shape_id)
    rows = frame.table.rows
    if not 0 <= row < len(rows):
        raise IndexError(f"No such row: {row}")
    rows[row].height = Emu(_size(height, "height"))
    _sync_frame(frame)


# -- merging -----------------------------------------------------------------


def merge_cells(
    prs: Any, slide: int, shape_id: int, row1: int, col1: int, row2: int, col2: int
) -> None:
    """Merge the rectangle between two corner cells; texts are joined in the origin cell."""
    table = _table(prs, slide, shape_id)
    _check_cell(table, row1, col1)
    _check_cell(table, row2, col2)
    top, bottom = sorted((row1, row2))
    left, right = sorted((col1, col2))
    if (top, left) == (bottom, right):
        raise ValueError("A merge needs at least two cells")
    for r in range(top, bottom + 1):
        for c in range(left, right + 1):
            cell = table.cell(r, c)
            if cell.is_merge_origin or cell.is_spanned:
                raise AnyDocError(f"Cell ({r}, {c}) is already merged")
    table.cell(top, left).merge(table.cell(bottom, right))


def unmerge_cells(prs: Any, slide: int, shape_id: int, row: int, col: int) -> None:
    """Split the merged region whose top-left cell is ``(row, col)``."""
    table = _table(prs, slide, shape_id)
    _check_cell(table, row, col)
    cell = table.cell(row, col)
    if not cell.is_merge_origin:
        raise AnyDocError(f"Cell ({row}, {col}) is not the origin of a merged region")
    cell.split()


# -- formatting --------------------------------------------------------------


def format_cells(
    prs: Any,
    slide: int,
    shape_id: int,
    *,
    rows: int | Iterable[int] | None = None,
    cols: int | Iterable[int] | None = None,
    bold: bool | None = None,
    italic: bool | None = None,
    underline: bool | None = None,
    size: float | None = None,
    color: str | None = None,
    font: str | None = None,
    align: str | None = None,
    anchor: str | None = None,
    fill: str | None = None,
) -> None:
    """Format the cells at the intersection of ``rows`` and ``cols`` (default: all).

    Font options apply to the cell text (empty cells remember them for text typed
    later); ``align`` is the horizontal alignment, ``anchor`` the vertical one
    (top/middle/bottom); ``fill`` is ``RRGGBB`` or ``"none"``.
    """
    table = _table(prs, slide, shape_id)
    row_ids = _indexes(rows, len(table.rows), "row")
    col_ids = _indexes(cols, len(table.columns), "column")
    alignment = _align(align) if align is not None else None
    if anchor is not None and anchor.lower() not in _ANCHOR:
        raise ValueError(f"Invalid anchor: {anchor!r}")
    fill_rgb = _rgb(fill) if fill is not None and fill.lower() != "none" else None
    has_font = any(v is not None for v in (bold, italic, underline, size, color, font))
    for r in row_ids:
        for c in col_ids:
            cell = table.cell(r, c)
            for para in cell.text_frame.paragraphs:
                if alignment is not None:
                    para.alignment = alignment
                if not has_font:
                    continue
                targets = [run.font for run in para.runs] or [_end_font(para)]
                for target in targets:
                    _apply_font(
                        target,
                        bold=bold,
                        italic=italic,
                        underline=underline,
                        size=size,
                        color=color,
                        font_name=font,
                    )
            if anchor is not None:
                cell.vertical_anchor = _ANCHOR[anchor.lower()]
            if fill is not None:
                if fill_rgb is None:
                    cell.fill.background()
                else:
                    cell.fill.solid()
                    cell.fill.fore_color.rgb = fill_rgb


def set_table_options(
    prs: Any,
    slide: int,
    shape_id: int,
    *,
    header: bool | None = None,
    banded: bool | None = None,
    first_col: bool | None = None,
) -> None:
    """Toggle the table style's header row, banded rows and emphasised first column."""
    table = _table(prs, slide, shape_id)
    if header is not None:
        table.first_row = header
    if banded is not None:
        table.horz_banding = banded
    if first_col is not None:
        table.first_col = first_col


# -- read back ---------------------------------------------------------------


def table_info(prs: Any, slide: int, shape_id: int) -> TableInfo:
    """Return size, content, column widths, row heights and merged regions of a table."""
    frame = _frame(prs, slide, shape_id)
    table = frame.table
    merges: list[tuple[int, int, int, int]] = []
    for r in range(len(table.rows)):
        for c in range(len(table.columns)):
            cell = table.cell(r, c)
            if cell.is_merge_origin:
                merges.append((r, c, r + cell.span_height - 1, c + cell.span_width - 1))
    return TableInfo(
        shape_id=int(frame.shape_id),
        rows=len(table.rows),
        cols=len(table.columns),
        data=[[cell.text for cell in row.cells] for row in table.rows],
        col_widths=[int(c.width) for c in table.columns],
        row_heights=[int(r.height) for r in table.rows],
        merges=merges,
        header=bool(table.first_row),
        banded=bool(table.horz_banding),
        first_col=bool(table.first_col),
    )


def cell_format(prs: Any, slide: int, shape_id: int, row: int, col: int) -> TableCellFormat:
    """Return text, fill colour, vertical anchor and per-run formatting of one cell."""
    table = _table(prs, slide, shape_id)
    _check_cell(table, row, col)
    cell = table.cell(row, col)
    names_align = {v: k for k, v in _ALIGN.items()}
    names_anchor = {v: k for k, v in _ANCHOR.items()}
    fill = cell.fill
    try:
        fill_color = _color_of(fill.fore_color) if fill.type is not None else None
    except TypeError:
        fill_color = None  # explicit "no fill"
    return TableCellFormat(
        text=cell.text,
        fill=fill_color,
        anchor=names_anchor.get(cell.vertical_anchor),
        paragraphs=[
            ParagraphFormat(
                text=p.text,
                align=names_align.get(p.alignment),
                level=p.level,
                runs=[_run_format(r) for r in p.runs],
            )
            for p in cell.text_frame.paragraphs
        ],
    )
