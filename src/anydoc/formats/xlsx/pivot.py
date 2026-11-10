"""XLSX pivot tables computed in Python and written to a new worksheet (T066)."""

from __future__ import annotations

import statistics
from collections.abc import Callable, Sequence
from typing import Any

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font
from openpyxl.worksheet.worksheet import Worksheet

from anydoc.formats.xlsx import data as _data
from anydoc.formats.xlsx import edit as _edit

ValueSpec = str | tuple[str, str]
GRAND_TOTAL = "Grand Total"


def _nums(vals: list[Any]) -> list[float]:
    return [float(v) for v in vals if isinstance(v, int | float) and not isinstance(v, bool)]


def _sum(vals: list[Any]) -> Any:
    n = _nums(vals)
    return sum(n) if n else 0


def _mean(vals: list[Any]) -> Any:
    n = _nums(vals)
    return sum(n) / len(n) if n else None


def _min(vals: list[Any]) -> Any:
    n = _nums(vals)
    return min(n) if n else None


def _max(vals: list[Any]) -> Any:
    n = _nums(vals)
    return max(n) if n else None


def _median(vals: list[Any]) -> Any:
    n = _nums(vals)
    return statistics.median(n) if n else None


def _count(vals: list[Any]) -> Any:
    return sum(1 for v in vals if v is not None and v != "")


AGGREGATES: dict[str, Callable[[list[Any]], Any]] = {
    "sum": _sum,
    "count": _count,
    "average": _mean,
    "min": _min,
    "max": _max,
    "median": _median,
}


def _tidy(v: Any) -> Any:
    if isinstance(v, float) and v.is_integer():
        return int(v)
    return v


def _label(v: Any) -> Any:
    return "(blank)" if v is None or v == "" else v


def _sorted_keys(keys: set[tuple[Any, ...]]) -> list[tuple[Any, ...]]:
    return sorted(keys, key=lambda k: tuple(_data._rank(x) for x in k))


def read_records(
    ws: Worksheet, source_range: str, computed: dict[str, Any] | None = None
) -> tuple[list[str], list[list[Any]]]:
    """Read a header row plus data rows from ``source_range`` (formula results via ``computed``)."""
    c1, r1, c2, r2 = _data._bounds(ws, source_range)
    if r2 <= r1:
        raise ValueError("Pivot source needs a header row and at least one data row")
    computed = computed or {}

    def val(r: int, c: int) -> Any:
        cell = ws.cell(row=r, column=c)
        if isinstance(cell.value, str) and cell.value.startswith("="):
            return computed.get(cell.coordinate)
        return cell.value

    header = [val(r1, c) for c in range(c1, c2 + 1)]
    names = [str(h) if h not in (None, "") else "" for h in header]
    if "" in names or len(set(names)) != len(names):
        raise ValueError("Pivot header cells must be non-empty and unique")
    recs = [[val(r, c) for c in range(c1, c2 + 1)] for r in range(r1 + 1, r2 + 1)]
    return names, [r for r in recs if any(not _data._is_blank(v) for v in r)]


def _specs(values: Sequence[ValueSpec]) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    for v in values:
        field, agg = (v, "sum") if isinstance(v, str) else (v[0], v[1])
        agg = agg.lower()
        if agg not in AGGREGATES:
            raise ValueError(f"Unknown aggregate {agg!r}; use one of {sorted(AGGREGATES)}")
        out.append((field, agg))
    if not out:
        raise ValueError("At least one value field is required")
    return out


def compute_pivot(
    names: list[str],
    records: list[list[Any]],
    *,
    rows: Sequence[str],
    columns: Sequence[str] = (),
    values: Sequence[ValueSpec],
    totals: bool = True,
    filters: dict[str, Any] | None = None,
) -> list[list[Any]]:
    """Compute the pivot grid (header row first) from records."""
    specs = _specs(values)
    for f in [*rows, *columns, *(s[0] for s in specs), *(filters or {})]:
        if f not in names:
            raise ValueError(f"Unknown field {f!r}; fields are {names}")
    if not rows and not columns:
        raise ValueError("Specify at least one row or column field")
    idx = {n: i for i, n in enumerate(names)}
    if filters:
        keep = {
            k: (set(v) if isinstance(v, list | tuple | set) else {v}) for k, v in filters.items()
        }
        records = [r for r in records if all(r[idx[k]] in allowed for k, allowed in keep.items())]
    ri = [idx[f] for f in rows]
    ci = [idx[f] for f in columns]

    cells: dict[tuple[Any, ...], dict[tuple[Any, ...], list[list[Any]]]] = {}
    col_keys: set[tuple[Any, ...]] = set()
    row_keys: set[tuple[Any, ...]] = set()
    for rec in records:
        rk = tuple(_label(rec[i]) for i in ri)
        ck = tuple(_label(rec[i]) for i in ci)
        row_keys.add(rk)
        col_keys.add(ck)
        bucket = cells.setdefault(rk, {}).setdefault(ck, [[] for _ in specs])
        for j, (field, _) in enumerate(specs):
            bucket[j].append(rec[idx[field]])

    def agg(buckets: list[list[Any]], j: int) -> Any:
        return _tidy(AGGREGATES[specs[j][1]](buckets[j]))

    def merge(parts: list[list[list[Any]]]) -> list[list[Any]]:
        return [[v for p in parts for v in p[j]] for j in range(len(specs))]

    def caption(j: int) -> str:
        return f"{specs[j][1].capitalize()} of {specs[j][0]}"

    ordered_cols = _sorted_keys(col_keys)
    header: list[Any] = list(rows)
    multi = len(specs) > 1
    for ck in ordered_cols:
        for j in range(len(specs)):
            base = " / ".join(str(x) for x in ck)
            header.append(f"{base} / {caption(j)}" if base and multi else base or caption(j))
    if totals and columns:
        for j in range(len(specs)):
            header.append(f"{GRAND_TOTAL} / {caption(j)}" if multi else GRAND_TOTAL)

    grid: list[list[Any]] = [header]
    for rk in _sorted_keys(row_keys):
        line: list[Any] = list(rk)
        by_col = cells.get(rk, {})
        for ck in ordered_cols:
            b = by_col.get(ck)
            line.extend(agg(b, j) if b else None for j in range(len(specs)))
        if totals and columns:
            allb = merge(list(by_col.values()))
            line.extend(agg(allb, j) for j in range(len(specs)))
        grid.append(line)
    if totals and rows:
        line = [GRAND_TOTAL] + [None] * (len(rows) - 1)
        every = [b for by in cells.values() for b in by.values()]
        for ck in ordered_cols:
            parts = [by[ck] for by in cells.values() if ck in by]
            line.extend(agg(merge(parts), j) if parts else None for j in range(len(specs)))
        if columns:
            line.extend(agg(merge(every), j) for j in range(len(specs)))
        grid.append(line)
    return grid


def pivot_table(
    wb: Workbook,
    source: Worksheet,
    source_range: str,
    target_title: str,
    *,
    rows: Sequence[str],
    columns: Sequence[str] = (),
    values: Sequence[ValueSpec],
    totals: bool = True,
    filters: dict[str, Any] | None = None,
    computed: dict[str, Any] | None = None,
) -> Worksheet:
    """Compute a pivot table from ``source_range`` and write it to a new sheet."""
    names, records = read_records(source, source_range, computed)
    grid = compute_pivot(
        names, records, rows=rows, columns=columns, values=values, totals=totals, filters=filters
    )
    ws = _edit.add_sheet(wb, target_title)
    bold = Font(bold=True)
    for r, line in enumerate(grid, start=1):
        for c, v in enumerate(line, start=1):
            cell = ws.cell(row=r, column=c, value=v)
            if r == 1 or (totals and r == len(grid) and rows) or c <= len(rows):
                cell.font = bold
            if r == 1 and c > len(rows):
                cell.alignment = Alignment(horizontal="center")
    ws.freeze_panes = ws.cell(row=2, column=len(rows) + 1).coordinate if rows else "A2"
    return ws
