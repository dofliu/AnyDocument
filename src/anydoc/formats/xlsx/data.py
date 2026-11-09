"""XLSX sorting, filtering and duplicate removal on an ``openpyxl.Workbook`` (T063)."""

from __future__ import annotations

import copy
from collections.abc import Callable, Sequence
from datetime import date, datetime, time
from typing import Any

from openpyxl.utils import get_column_letter
from openpyxl.utils.cell import column_index_from_string, range_boundaries
from openpyxl.worksheet.worksheet import Worksheet

SortKey = str | int | tuple[str | int, bool]


def _bounds(ws: Worksheet, ref: str) -> tuple[int, int, int, int]:
    """Return ``(min_col, min_row, max_col, max_row)`` of an ``A1:C3`` style range."""
    text = ref.strip().upper().replace("$", "")
    if ":" not in text:
        text = f"{text}:{text}"
    try:
        bounds = range_boundaries(text)
    except (ValueError, TypeError):
        raise ValueError(f"Invalid range: {ref!r}") from None
    if any(b is None or b < 1 for b in bounds):
        raise ValueError(f"Range must be bounded (A1:C9): {ref!r}")
    c1, r1, c2, r2 = (int(b) for b in bounds if b is not None)
    return c1, r1, c2, r2


def _check_no_merges(ws: Worksheet, c1: int, r1: int, c2: int, r2: int) -> None:
    for m in ws.merged_cells.ranges:
        if not (m.max_col < c1 or m.min_col > c2 or m.max_row < r1 or m.min_row > r2):
            raise ValueError("Range contains merged cells; unmerge them first")


def _col_offset(key: str | int, c1: int, c2: int) -> int:
    """Resolve a key (column letter, or 0-based offset in the range) to a 0-based offset."""
    off = key if isinstance(key, int) else column_index_from_string(key.upper()) - c1
    if not 0 <= off <= c2 - c1:
        raise ValueError(f"Sort/filter column {key!r} is outside the range")
    return off


def _norm(key: SortKey, c1: int, c2: int) -> tuple[int, bool]:
    if isinstance(key, tuple):
        return _col_offset(key[0], c1, c2), key[1]
    return _col_offset(key, c1, c2), True


def _rank(value: Any, case_sensitive: bool = False) -> tuple[int, Any]:
    """Excel ordering: numbers < text < logicals; blanks are handled by the caller."""
    if isinstance(value, bool):
        return (2, value)
    if isinstance(value, int | float):
        return (0, float(value))
    if isinstance(value, datetime):
        return (0, value.timestamp())
    if isinstance(value, date):
        return (0, datetime(value.year, value.month, value.day).timestamp())
    if isinstance(value, time):
        return (0, value.hour * 3600.0 + value.minute * 60 + value.second)
    text = str(value)
    return (1, text if case_sensitive else text.casefold())


def _is_blank(value: Any) -> bool:
    return value is None or value == ""


def _cell(ws: Worksheet, row: int, col: int) -> Any:
    return ws.cell(row=row, column=col)


def _read_rows(ws: Worksheet, c1: int, r1: int, c2: int, r2: int) -> list[list[tuple[Any, Any]]]:
    return [
        [(_cell(ws, r, c).value, copy.copy(_cell(ws, r, c)._style)) for c in range(c1, c2 + 1)]
        for r in range(r1, r2 + 1)
    ]


def _write_rows(
    ws: Worksheet, rows: list[list[tuple[Any, Any]]], c1: int, r1: int, c2: int, r2: int
) -> None:
    for i, row in enumerate(rows):
        for j, (value, style) in enumerate(row):
            cell = _cell(ws, r1 + i, c1 + j)
            cell.value = value
            cell._style = copy.copy(style)
    for r in range(r1 + len(rows), r2 + 1):
        for c in range(c1, c2 + 1):
            _cell(ws, r, c).value = None


def sort_range(
    ws: Worksheet,
    ref: str,
    keys: Sequence[SortKey],
    *,
    has_header: bool = False,
    case_sensitive: bool = False,
) -> int:
    """Sort the rows of ``ref`` by one or more keys; return the number of rows sorted.

    A key is a column letter (``"B"``), a 0-based offset within the range, or a
    ``(key, ascending)`` tuple.  Numbers sort before text, text before booleans, and blank
    cells always come last.  Cell values and styles move together; formulas are moved as
    text (references are not rewritten).
    """
    if not keys:
        raise ValueError("At least one sort key is required")
    c1, r1, c2, r2 = _bounds(ws, ref)
    _check_no_merges(ws, c1, r1, c2, r2)
    first = r1 + 1 if has_header else r1
    if first > r2:
        return 0
    norm = [_norm(k, c1, c2) for k in keys]
    rows = _read_rows(ws, c1, first, c2, r2)
    # Stable multi-pass sort, least significant key first.
    for off, asc in reversed(norm):
        present = [row for row in rows if not _is_blank(row[off][0])]
        blank = [row for row in rows if _is_blank(row[off][0])]
        present.sort(key=lambda row, o=off: _rank(row[o][0], case_sensitive), reverse=not asc)  # type: ignore[misc]
        rows = present + blank
    _write_rows(ws, rows, c1, first, c2, r2)
    return len(rows)


def remove_duplicates(
    ws: Worksheet,
    ref: str,
    columns: Sequence[str | int] | None = None,
    *,
    has_header: bool = True,
    case_sensitive: bool = False,
) -> int:
    """Remove rows whose ``columns`` repeat an earlier row; return the number removed.

    ``columns`` defaults to every column of the range.  The first occurrence is kept, the
    remaining rows move up and the freed rows at the bottom of the range are cleared.
    """
    c1, r1, c2, r2 = _bounds(ws, ref)
    _check_no_merges(ws, c1, r1, c2, r2)
    first = r1 + 1 if has_header else r1
    if first > r2:
        return 0
    offsets = (
        list(range(c2 - c1 + 1)) if columns is None else [_col_offset(c, c1, c2) for c in columns]
    )
    rows = _read_rows(ws, c1, first, c2, r2)
    seen: set[tuple[Any, ...]] = set()
    kept: list[list[tuple[Any, Any]]] = []
    for row in rows:
        ident = tuple(
            (None if _is_blank(row[o][0]) else _rank(row[o][0], case_sensitive)) for o in offsets
        )
        if ident in seen:
            continue
        seen.add(ident)
        kept.append(row)
    removed = len(rows) - len(kept)
    if removed:
        _write_rows(ws, kept, c1, first, c2, r2)
    return removed


Criterion = Sequence[Any] | Callable[[Any], bool]


def _matcher(criterion: Criterion) -> Callable[[Any], bool]:
    if callable(criterion):
        return criterion
    allowed = {_rank(v) for v in criterion if not _is_blank(v)}
    allow_blank = any(_is_blank(v) for v in criterion)
    return lambda v: allow_blank if _is_blank(v) else _rank(v) in allowed


def set_auto_filter(
    ws: Worksheet,
    ref: str,
    criteria: dict[str | int, Criterion] | None = None,
) -> list[int]:
    """Install an AutoFilter on ``ref`` (first row is the header) and optionally apply it.

    ``criteria`` maps a column (letter, or 0-based offset in the range) to the list of
    accepted values or to a predicate.  Non-matching data rows are hidden and the accepted
    values are recorded in the AutoFilter so that Excel shows the filter state.  Returns
    the 1-based numbers of the hidden rows.
    """
    c1, r1, c2, r2 = _bounds(ws, ref)
    ws.auto_filter.ref = f"{get_column_letter(c1)}{r1}:{get_column_letter(c2)}{r2}"
    ws.auto_filter.filterColumn = []
    ws.auto_filter.sortState = None
    # Reset any rows hidden by an earlier filter on this range.
    for r in range(r1 + 1, r2 + 1):
        if r in ws.row_dimensions:
            ws.row_dimensions[r].hidden = False
    hidden: list[int] = []
    if not criteria:
        return hidden
    tests = [(_col_offset(k, c1, c2), _matcher(v), v) for k, v in criteria.items()]
    for off, _, spec in tests:
        if not callable(spec):
            values = [str(v) for v in spec if not _is_blank(v)]
            ws.auto_filter.add_filter_column(off, values, blank=any(_is_blank(v) for v in spec))
    for r in range(r1 + 1, r2 + 1):
        if not all(test(_cell(ws, r, c1 + off).value) for off, test, _ in tests):
            ws.row_dimensions[r].hidden = True
            hidden.append(r)
    return hidden


def clear_auto_filter(ws: Worksheet) -> int:
    """Remove the AutoFilter and unhide the rows it hid; return the number of rows shown."""
    ref = ws.auto_filter.ref
    shown = 0
    if ref:
        _, r1, _, r2 = _bounds(ws, str(ref))
        for r in range(r1 + 1, r2 + 1):
            if r in ws.row_dimensions and ws.row_dimensions[r].hidden:
                ws.row_dimensions[r].hidden = False
                shown += 1
    ws.auto_filter.ref = None
    ws.auto_filter.filterColumn = []
    return shown


def get_auto_filter(ws: Worksheet) -> dict[str, Any] | None:
    """Describe the AutoFilter (``ref``, per-column ``values``, ``hidden_rows``) or ``None``."""
    ref = ws.auto_filter.ref
    if not ref:
        return None
    c1, r1, _, r2 = _bounds(ws, str(ref))
    columns: dict[str, list[str]] = {}
    for fc in ws.auto_filter.filterColumn:
        values = list(fc.filters.filter) if fc.filters is not None else []
        columns[get_column_letter(c1 + int(fc.colId))] = values
    hidden = [
        r for r in range(r1 + 1, r2 + 1) if r in ws.row_dimensions and ws.row_dimensions[r].hidden
    ]
    return {"ref": str(ref), "columns": columns, "hidden_rows": hidden}
