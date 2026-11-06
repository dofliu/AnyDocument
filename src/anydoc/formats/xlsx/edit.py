"""XLSX editing primitives operating on an ``openpyxl.Workbook`` (T060)."""

from __future__ import annotations

import re
from collections.abc import Sequence
from typing import Any, cast

from openpyxl import Workbook
from openpyxl.utils.cell import column_index_from_string, coordinate_from_string
from openpyxl.worksheet.cell_range import CellRange
from openpyxl.worksheet.worksheet import Worksheet

_BAD_TITLE = re.compile(r"[\[\]:*?/\\]")
MAX_TITLE = 31


def get_sheet(wb: Workbook, sheet: int | str) -> Worksheet:
    """Return a worksheet by index or name; raise ``KeyError`` if missing."""
    try:
        ws: Any = wb.worksheets[sheet] if isinstance(sheet, int) else wb[sheet]
    except (KeyError, IndexError):
        raise KeyError(f"No such sheet: {sheet!r}") from None
    return cast(Worksheet, ws)


def _pos(wb: Workbook, ws: Worksheet) -> int:
    return next(i for i, w in enumerate(wb.worksheets) if w is ws)


def check_title(wb: Workbook, title: str, *, ignore: Worksheet | None = None) -> None:
    """Validate a sheet name (length, forbidden characters, uniqueness, case-insensitive)."""
    if not title or title != title.strip("'") or len(title) > MAX_TITLE:
        raise ValueError(f"Invalid sheet name (1-{MAX_TITLE} chars, no edge quotes): {title!r}")
    if _BAD_TITLE.search(title):
        raise ValueError(f"Sheet name contains forbidden characters []:*?/\\ : {title!r}")
    for ws in wb.worksheets:
        if ws is not ignore and ws.title.lower() == title.lower():
            raise ValueError(f"Sheet already exists: {title!r}")


def add_sheet(wb: Workbook, title: str, index: int | None = None) -> Worksheet:
    """Add an empty sheet (appended, or at ``index``)."""
    check_title(wb, title)
    return cast(Worksheet, wb.create_sheet(title, index))


def delete_sheet(wb: Workbook, sheet: int | str) -> None:
    """Delete a sheet; a workbook must keep at least one sheet."""
    ws = get_sheet(wb, sheet)
    if len(wb.worksheets) == 1:
        raise ValueError("Cannot delete the only sheet in a workbook")
    wb.remove(ws)


def rename_sheet(wb: Workbook, sheet: int | str, title: str) -> None:
    """Rename a sheet and rewrite formula references to it in the workbook."""
    ws = get_sheet(wb, sheet)
    check_title(wb, title, ignore=ws)
    old = ws.title
    if old == title:
        return
    ws.title = title
    new_ref = f"'{title}'!" if re.search(r"[^\w]", title) else f"{title}!"
    pattern = re.compile(rf"(?:'{re.escape(old)}'|(?<![\w'.]){re.escape(old)})!")
    for other in wb.worksheets:
        for row in other.iter_rows():
            for cell in row:
                if cell.data_type == "f" and isinstance(cell.value, str):
                    cell.value = pattern.sub(lambda _m: new_ref, cell.value)


def copy_sheet(wb: Workbook, sheet: int | str, title: str) -> Worksheet:
    """Duplicate a sheet (values, styles, dimensions, merges) placed after the source."""
    src = get_sheet(wb, sheet)
    check_title(wb, title)
    dup = wb.copy_worksheet(src)
    dup.title = "_anydoc_tmp_"  # openpyxl's auto name may clash case-insensitively
    dup.title = title
    wb.move_sheet(dup, offset=_pos(wb, src) + 1 - _pos(wb, dup))
    return dup


def move_sheet(wb: Workbook, sheet: int | str, index: int) -> None:
    """Move a sheet to position ``index`` (0-based)."""
    ws = get_sheet(wb, sheet)
    if not 0 <= index < len(wb.worksheets):
        raise IndexError(f"Sheet position out of range: {index}")
    wb.move_sheet(ws, offset=index - _pos(wb, ws))


def set_cell(ws: Worksheet, coordinate: str, value: Any) -> None:
    """Write one cell; strings starting with ``=`` become formulas, ``None`` clears it."""
    ws[_norm(coordinate)].value = value


def set_range(ws: Worksheet, top_left: str, rows: Sequence[Sequence[Any]]) -> str:
    """Write a 2-D block of values starting at ``top_left``; return the range written."""
    col_letter, row0 = coordinate_from_string(_norm(top_left))
    col0 = column_index_from_string(col_letter)
    width = 0
    for r, values in enumerate(rows):
        width = max(width, len(values))
        for c, value in enumerate(values):
            ws.cell(row=row0 + r, column=col0 + c).value = value
    if not rows or width == 0:
        return f"{top_left.upper()}:{top_left.upper()}"
    end = ws.cell(row=row0 + len(rows) - 1, column=col0 + width - 1).coordinate
    return f"{ws.cell(row=row0, column=col0).coordinate}:{end}"


def clear_range(ws: Worksheet, cell_range: str) -> int:
    """Clear values in a range (styles kept); return the number of cells cleared."""
    n = 0
    for row in ws[_norm(cell_range)] if ":" in cell_range else [[ws[_norm(cell_range)]]]:
        for cell in row:
            if cell.value is not None:
                n += 1
            cell.value = None
    return n


def _norm(ref: str) -> str:
    ref = ref.strip().upper().replace("$", "")
    if not re.fullmatch(r"[A-Z]{1,3}\d+(:[A-Z]{1,3}\d+)?", ref):
        raise ValueError(f"Invalid cell reference: {ref!r}")
    return ref


def _shift_merges(ws: Worksheet, *, axis: str, at: int, amount: int) -> None:
    """Shift merged ranges after an insert (amount>0) or delete (amount<0) at ``at``."""
    ranges = [CellRange(str(r)) for r in ws.merged_cells.ranges]
    for r in list(ws.merged_cells.ranges):
        ws.merged_cells.remove(r)
    for r in ranges:
        lo, hi = (r.min_row, r.max_row) if axis == "row" else (r.min_col, r.max_col)
        if amount > 0:
            lo, hi = (lo + amount if lo >= at else lo), (hi + amount if hi >= at else hi)
        else:
            gone = -amount
            end = at + gone - 1
            if lo >= at and hi <= end:
                continue  # merge entirely deleted
            new_lo = lo if lo < at else max(at, lo - gone)
            new_hi = hi if hi < at else (hi - gone if hi > end else at - 1)
            lo, hi = new_lo, new_hi
        if axis == "row":
            r.min_row, r.max_row = lo, hi
        else:
            r.min_col, r.max_col = lo, hi
        if r.min_row == r.max_row and r.min_col == r.max_col:
            continue
        ws.merge_cells(r.coord)


def insert_rows(ws: Worksheet, at: int, amount: int = 1) -> None:
    """Insert ``amount`` blank rows before 1-based row ``at`` (merges shift; formulas do not)."""
    _check_pos(at, amount)
    ws.insert_rows(at, amount)
    _shift_merges(ws, axis="row", at=at, amount=amount)


def delete_rows(ws: Worksheet, at: int, amount: int = 1) -> None:
    """Delete ``amount`` rows starting at 1-based row ``at``."""
    _check_pos(at, amount)
    ws.delete_rows(at, amount)
    _shift_merges(ws, axis="row", at=at, amount=-amount)


def insert_cols(ws: Worksheet, at: int, amount: int = 1) -> None:
    """Insert ``amount`` blank columns before 1-based column ``at``."""
    _check_pos(at, amount)
    ws.insert_cols(at, amount)
    _shift_merges(ws, axis="col", at=at, amount=amount)


def delete_cols(ws: Worksheet, at: int, amount: int = 1) -> None:
    """Delete ``amount`` columns starting at 1-based column ``at``."""
    _check_pos(at, amount)
    ws.delete_cols(at, amount)
    _shift_merges(ws, axis="col", at=at, amount=-amount)


def _check_pos(at: int, amount: int) -> None:
    if at < 1 or amount < 1:
        raise ValueError("Position and amount must be >= 1")
