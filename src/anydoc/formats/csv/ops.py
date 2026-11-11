"""CSV / TSV data operations: filter, sort, dedupe, columns, join, group-by, statistics (T080).

All functions work on :class:`~anydoc.formats.csv.handler.CsvDocument` objects and keep
cells as text, so untouched values round-trip byte for byte.  Columns are addressed by
header name (``col1``, ``col2``, ... when the table has no header) or by 0-based index.
"""

from __future__ import annotations

import re
import statistics
from collections.abc import Callable, Mapping, Sequence
from datetime import date, datetime
from typing import Any

from anydoc.formats.csv.handler import CsvDocument
from anydoc.formats.csv.parse import column_types, convert_value, infer_type

ColumnRef = str | int
Predicate = Callable[[dict[str, str]], bool]
Condition = str | tuple[ColumnRef, str, Any] | Predicate
SortKey = str | int | tuple[ColumnRef, bool]
AggSpec = str | tuple[str, ColumnRef | None] | tuple[str, ColumnRef | None, str]

OPERATORS = ("==", "!=", ">=", "<=", "=~", "!~", "=", ">", "<", "~")
JOIN_KINDS = ("inner", "left", "right", "outer")
AGGREGATES = ("count", "sum", "mean", "min", "max", "median", "first", "last", "nunique")
_AGG_ALIASES = {"average": "mean", "avg": "mean", "unique": "nunique"}
_NUMERIC = ("int", "float")


# --------------------------------------------------------------------------- helpers


def column_names(doc: CsvDocument) -> list[str]:
    """Return one name per column: the header, with ``colN`` for missing/blank names."""
    header = doc.header if doc.has_header else []
    return [
        header[i] if i < len(header) and header[i].strip() else f"col{i + 1}"
        for i in range(doc.column_count)
    ]


def _index(names: Sequence[str], ref: ColumnRef) -> int:
    if isinstance(ref, bool) or not isinstance(ref, str | int):
        raise ValueError(f"Invalid column reference: {ref!r}")
    if isinstance(ref, int):
        if not 0 <= ref < len(names):
            raise ValueError(f"Column index out of range: {ref}")
        return ref
    matches = [i for i, n in enumerate(names) if n == ref]
    if not matches:
        matches = [i for i, n in enumerate(names) if n.strip() == ref.strip()]
    if not matches:
        raise ValueError(f"No such column: {ref!r} (available: {', '.join(names)})")
    if len(matches) > 1:
        raise ValueError(f"Ambiguous column name: {ref!r}")
    return matches[0]


def resolve_column(doc: CsvDocument, ref: ColumnRef) -> int:
    """Return the 0-based index of a column given its name or index."""
    return _index(column_names(doc), ref)


def _table(doc: CsvDocument) -> tuple[list[str], list[list[str]]]:
    """Return ``(names, rows)`` with every row padded to the table width (copies)."""
    names = column_names(doc)
    n = len(names)
    return names, [list(r[:n]) + [""] * (n - len(r)) for r in doc.rows]


def _store(doc: CsvDocument, names: Sequence[str], rows: list[list[str]]) -> None:
    doc.header = list(names) if doc.has_header else []
    doc.rows = rows
    doc.refresh()


def _new(like: CsvDocument, names: Sequence[str], rows: list[list[str]]) -> CsvDocument:
    return CsvDocument(
        [list(names), *rows],
        format=like.format,
        encoding=like.encoding,
        delimiter=like.delimiter,
        has_bom=like.has_bom,
        has_header=True,
    )


def _as_list(value: Any) -> list[Any]:
    return list(value) if isinstance(value, list) else [value]


def _typed(cell: str) -> Any:
    return convert_value(cell, infer_type(cell))


def _is_number(v: Any) -> bool:
    return isinstance(v, int | float) and not isinstance(v, bool)


def _comparable(a: Any, b: Any) -> tuple[Any, Any] | None:
    """Return the pair coerced to a common ordered type, or ``None`` to compare as text."""
    if _is_number(a) and _is_number(b):
        return float(a), float(b)
    if isinstance(a, bool) and isinstance(b, bool):
        return a, b
    if isinstance(a, date) and isinstance(b, date):
        if isinstance(a, datetime) == isinstance(b, datetime):
            return a, b
        return _as_datetime(a), _as_datetime(b)
    return None


def _as_datetime(v: date) -> datetime:
    return v if isinstance(v, datetime) else datetime(v.year, v.month, v.day)


def _fmt(v: Any) -> str:
    """Format an aggregate result as cell text (``3.0`` -> ``3``, float noise trimmed)."""
    if v is None:
        return ""
    if isinstance(v, float):
        v = round(v, 10)
        return str(int(v)) if v.is_integer() else repr(v)
    return str(v)


def _numbers(cells: Sequence[str]) -> list[int | float]:
    out: list[int | float] = []
    for c in cells:
        kind = infer_type(c)
        if kind == "int":
            out.append(int(c.strip()))
        elif kind == "float":
            out.append(float(c.strip()))
    return out


# --------------------------------------------------------------------------- filter


def parse_condition(text: str) -> tuple[str, str, str]:
    """Split ``"column OP value"`` into ``(column, op, value)``.

    Operators: ``=``/``==``, ``!=``, ``>``, ``>=``, ``<``, ``<=``, ``~`` (contains,
    case-insensitive), ``!~`` (does not contain) and ``=~`` (regular expression search).
    The value may be quoted; an empty value matches empty cells.
    """
    best: tuple[int, str] | None = None
    for op in OPERATORS:  # longer operators are listed first
        pos = text.find(op)
        if pos >= 0 and (best is None or pos < best[0]):
            best = (pos, op)
    if best is None:
        raise ValueError(f"Invalid condition (expected COLUMN OP VALUE): {text!r}")
    pos, op = best
    column = text[:pos].strip()
    value = text[pos + len(op) :].strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        value = value[1:-1]
    if not column:
        raise ValueError(f"Invalid condition (missing column): {text!r}")
    return column, op, value


def _cell_test(op: str, value: Any) -> Callable[[str], bool]:
    target = "" if value is None else str(value).strip()
    if op not in OPERATORS:
        raise ValueError(f"Unknown operator: {op!r} (use one of {' '.join(OPERATORS)})")
    if op == "~":
        needle = target.casefold()
        return lambda cell: needle in cell.casefold()
    if op == "!~":
        needle = target.casefold()
        return lambda cell: needle not in cell.casefold()
    if op == "=~":
        try:
            pattern = re.compile(target)
        except re.error as exc:
            raise ValueError(f"Invalid regular expression {target!r}: {exc}") from exc
        return lambda cell: pattern.search(cell) is not None

    typed_target = _typed(target)

    def equal(cell: str) -> bool:
        text = cell.strip()
        pair = _comparable(_typed(text), typed_target)
        return pair[0] == pair[1] if pair is not None else text == target

    def ordered(cell: str) -> bool:
        text = cell.strip()
        if not text or not target:
            return False
        a, b = _comparable(_typed(text), typed_target) or (text, target)
        if op == ">":
            return bool(a > b)
        if op == ">=":
            return bool(a >= b)
        if op == "<":
            return bool(a < b)
        return bool(a <= b)

    if op in ("=", "=="):
        return equal
    if op == "!=":
        return lambda cell: not equal(cell)
    return ordered


def _row_test(names: Sequence[str], condition: Condition) -> Callable[[list[str]], bool]:
    if callable(condition):
        predicate = condition
        return lambda row: bool(predicate(dict(zip(names, row, strict=True))))
    column: ColumnRef
    value: Any
    if isinstance(condition, str):
        column, op, value = parse_condition(condition)
    elif isinstance(condition, tuple) and len(condition) == 3:
        column, op, value = condition
    else:
        raise ValueError(f"Invalid condition: {condition!r}")
    idx = _index(names, column)
    test = _cell_test(op, value)
    return lambda row: test(row[idx])


def filter_rows(
    doc: CsvDocument,
    conditions: Condition | list[Condition],
    *,
    match: str = "all",
    invert: bool = False,
) -> int:
    """Keep the rows satisfying the conditions; return how many rows were removed.

    A condition is an expression string (see :func:`parse_condition`), a
    ``(column, op, value)`` tuple, or a callable receiving the row as a ``{name: text}``
    dict.  ``match`` is ``"all"`` (AND) or ``"any"`` (OR); ``invert`` keeps the rows that
    do *not* match instead.  Numbers and ISO dates compare by value, other cells as text.
    """
    if match not in ("all", "any"):
        raise ValueError(f"match must be 'all' or 'any', not {match!r}")
    names, rows = _table(doc)
    tests = [_row_test(names, c) for c in _as_list(conditions)]
    if not tests:
        raise ValueError("At least one condition is required")
    combine = all if match == "all" else any
    kept = [r for r in rows if combine(t(r) for t in tests) != invert]
    _store(doc, names, kept)
    return len(rows) - len(kept)


# --------------------------------------------------------------------------- sort


def _sort_key(names: Sequence[str], key: SortKey, descending: bool) -> tuple[int, bool]:
    if isinstance(key, tuple):
        return _index(names, key[0]), bool(key[1])
    if isinstance(key, str) and key not in names:
        base, sep, direction = key.rpartition(":")
        if sep and direction.lower() in ("asc", "desc"):
            return _index(names, base), direction.lower() == "desc"
    return _index(names, key), descending


def _sort_value(kind: str, ignore_case: bool) -> Callable[[str], Any]:
    if kind in _NUMERIC:
        return lambda cell: float(cell.strip())
    if kind in ("bool", "date", "datetime"):
        return lambda cell: convert_value(cell, kind)
    if ignore_case:
        return lambda cell: cell.strip().casefold()
    return lambda cell: cell.strip()


def sort_rows(
    doc: CsvDocument,
    keys: SortKey | list[SortKey],
    *,
    descending: bool = False,
    ignore_case: bool = False,
) -> None:
    """Sort the data rows in place by one or more columns (stable).

    A key is a column name/index, a ``"name:desc"`` / ``"name:asc"`` string, or a
    ``(column, descending)`` tuple.  Numeric and date columns sort by value, other
    columns as text; empty cells always go last.
    """
    names, rows = _table(doc)
    parsed = [_sort_key(names, k, descending) for k in _as_list(keys)]
    if not parsed:
        raise ValueError("At least one sort key is required")
    kinds = column_types(rows, len(names))
    for idx, desc in reversed(parsed):
        value = _sort_value(kinds[idx], ignore_case)
        filled = [r for r in rows if r[idx].strip()]
        blank = [r for r in rows if not r[idx].strip()]
        filled.sort(key=lambda r, i=idx, v=value: v(r[i]), reverse=desc)  # type: ignore[misc]
        rows = filled + blank
    _store(doc, names, rows)


# --------------------------------------------------------------------------- dedupe


def dedupe(
    doc: CsvDocument,
    columns: Sequence[ColumnRef] | None = None,
    *,
    keep: str = "first",
    ignore_case: bool = False,
) -> int:
    """Remove duplicate rows (compared on ``columns``, default all); return the count removed.

    ``keep`` selects which occurrence survives: ``"first"`` or ``"last"``.  Cells are
    compared after trimming surrounding whitespace.
    """
    if keep not in ("first", "last"):
        raise ValueError(f"keep must be 'first' or 'last', not {keep!r}")
    names, rows = _table(doc)
    idxs = [_index(names, c) for c in columns] if columns else list(range(len(names)))

    def key(row: list[str]) -> tuple[str, ...]:
        cells = (row[i].strip() for i in idxs)
        return tuple(c.casefold() for c in cells) if ignore_case else tuple(cells)

    seen: set[tuple[str, ...]] = set()
    kept: list[list[str]] = []
    for row in rows if keep == "first" else reversed(rows):
        k = key(row)
        if k not in seen:
            seen.add(k)
            kept.append(row)
    if keep == "last":
        kept.reverse()
    _store(doc, names, kept)
    return len(rows) - len(kept)


# --------------------------------------------------------------------------- columns


def select_columns(doc: CsvDocument, columns: Sequence[ColumnRef]) -> None:
    """Keep only ``columns``, in the given order."""
    names, rows = _table(doc)
    idxs = [_index(names, c) for c in columns]
    if not idxs:
        raise ValueError("At least one column is required")
    if len(set(idxs)) != len(idxs):
        raise ValueError("A column is selected more than once")
    _store(doc, [names[i] for i in idxs], [[r[i] for i in idxs] for r in rows])


def drop_columns(doc: CsvDocument, columns: Sequence[ColumnRef]) -> None:
    """Remove ``columns`` from the table."""
    names, _ = _table(doc)
    drop = {_index(names, c) for c in columns}
    keep = [i for i in range(len(names)) if i not in drop]
    if not keep:
        raise ValueError("Cannot drop every column")
    select_columns(doc, keep)


def rename_columns(doc: CsvDocument, mapping: Mapping[ColumnRef, str]) -> None:
    """Rename columns (``{old: new}``); a table without a header gains one."""
    names, rows = _table(doc)
    for old, new in mapping.items():
        if not isinstance(new, str) or not new.strip():
            raise ValueError(f"Invalid new column name: {new!r}")
        names[_index(column_names(doc), old)] = new
    duplicates = sorted({n for n in names if names.count(n) > 1})
    if duplicates:
        raise ValueError(f"Duplicate column name after rename: {', '.join(duplicates)}")
    doc.has_header = True
    _store(doc, names, rows)


# --------------------------------------------------------------------------- join


def _join_key(row: Sequence[str], idxs: Sequence[int]) -> tuple[Any, ...] | None:
    key: list[Any] = []
    for i in idxs:
        text = row[i].strip()
        if not text:
            return None  # empty keys never match, like SQL NULL
        key.append(float(text) if infer_type(text) in _NUMERIC else text)
    return tuple(key)


def join(
    left: CsvDocument,
    right: CsvDocument,
    on: ColumnRef | Sequence[ColumnRef],
    *,
    right_on: ColumnRef | Sequence[ColumnRef] | None = None,
    how: str = "inner",
    suffix: str = "_right",
) -> CsvDocument:
    """Join two tables on key column(s) and return a new table (inputs are untouched).

    ``how`` is ``inner``, ``left``, ``right`` or ``outer``.  The result has every left
    column followed by the right table's non-key columns (name clashes get ``suffix``).
    Rows follow the left table's order; unmatched right rows (``right``/``outer``) come
    last.  Numeric keys match by value (``1`` = ``1.0``); empty keys never match.
    """
    if how not in JOIN_KINDS:
        raise ValueError(f"how must be one of {', '.join(JOIN_KINDS)}, not {how!r}")
    lnames, lrows = _table(left)
    rnames, rrows = _table(right)
    lrefs = [on] if isinstance(on, str | int) else list(on)
    if right_on is None:
        rrefs = lrefs
    else:
        rrefs = [right_on] if isinstance(right_on, str | int) else list(right_on)
    if not lrefs or len(lrefs) != len(rrefs):
        raise ValueError("on and right_on must name the same, non-zero number of columns")
    lkeys = [_index(lnames, c) for c in lrefs]
    rkeys = [_index(rnames, c) for c in rrefs]
    rrest = [i for i in range(len(rnames)) if i not in rkeys]

    out_names = list(lnames)
    for i in rrest:
        name = rnames[i]
        while name in out_names:
            name += suffix
        out_names.append(name)

    lookup: dict[tuple[Any, ...], list[int]] = {}
    for n, row in enumerate(rrows):
        k = _join_key(row, rkeys)
        if k is not None:
            lookup.setdefault(k, []).append(n)

    out: list[list[str]] = []
    matched: set[int] = set()
    blanks = [""] * len(rrest)
    for row in lrows:
        k = _join_key(row, lkeys)
        hits = lookup.get(k, []) if k is not None else []
        for n in hits:
            matched.add(n)
            out.append(row + [rrows[n][i] for i in rrest])
        if not hits and how in ("left", "outer"):
            out.append(row + blanks)
    if how in ("right", "outer"):
        for n, rrow in enumerate(rrows):
            if n in matched:
                continue
            base = [""] * len(lnames)
            for li, ri in zip(lkeys, rkeys, strict=True):
                base[li] = rrow[ri]
            out.append(base + [rrow[i] for i in rrest])
    return _new(left, out_names, out)


# --------------------------------------------------------------------------- group-by


def _parse_aggregate(names: Sequence[str], spec: AggSpec) -> tuple[str, int | None, str]:
    """Return ``(function, column index or None, output name)`` for one aggregate spec."""
    column: ColumnRef | None
    label: str | None = None
    if isinstance(spec, str):
        text = spec
        head, sep, tail = spec.partition("=")
        if sep and ":" not in head and head.strip():
            label, text = head.strip(), tail
        func, sep, col = text.partition(":")
        column = col.strip() if sep else None
    elif isinstance(spec, tuple) and len(spec) in (2, 3):
        func, column = spec[0], spec[1]
        label = spec[2] if len(spec) == 3 else None
    else:
        raise ValueError(f"Invalid aggregate: {spec!r}")
    func = func.strip().lower()
    func = _AGG_ALIASES.get(func, func)
    if func not in AGGREGATES:
        raise ValueError(f"Unknown aggregate {func!r} (use one of {', '.join(AGGREGATES)})")
    if column is None or column == "":
        if func != "count":
            raise ValueError(f"Aggregate {func!r} needs a column, e.g. {func}:amount")
        return func, None, label or "count"
    idx = _index(names, column)
    return func, idx, label or f"{func}_{names[idx]}"


def _aggregate(func: str, cells: list[str], kind: str) -> str:
    filled = [c.strip() for c in cells if c.strip()]
    if func == "count":
        return str(len(filled))
    if func == "nunique":
        return str(len(set(filled)))
    if func == "first":
        return filled[0] if filled else ""
    if func == "last":
        return filled[-1] if filled else ""
    nums = _numbers(filled)
    if func == "sum":
        return _fmt(sum(nums))
    if func == "mean":
        return _fmt(sum(nums) / len(nums)) if nums else ""
    if func == "median":
        return _fmt(statistics.median(nums)) if nums else ""
    if not filled:
        return ""
    pick = min if func == "min" else max
    if kind in _NUMERIC:
        return _fmt(pick(nums))
    return pick(filled)


def group_by(
    doc: CsvDocument,
    by: ColumnRef | Sequence[ColumnRef],
    aggregates: AggSpec | list[AggSpec],
    *,
    sort: bool = True,
) -> CsvDocument:
    """Group rows by ``by`` and compute aggregates; return a new table.

    An aggregate is ``"func:column"`` (optionally ``"name=func:column"``), bare
    ``"count"`` for the group size, or a ``(func, column[, name])`` tuple.  Functions:
    count, sum, mean (average/avg), min, max, median, first, last, nunique.  Empty cells
    are ignored; sum/mean/median use numeric cells only.  An empty ``by`` yields a single
    totals row.  Groups are sorted by key unless ``sort`` is false (first-seen order).
    """
    names, rows = _table(doc)
    by_idx = [_index(names, c) for c in ([by] if isinstance(by, str | int) else list(by))]
    specs = [_parse_aggregate(names, a) for a in _as_list(aggregates)]
    if not specs:
        raise ValueError("At least one aggregate is required")
    out_names = [names[i] for i in by_idx] + [label for _, _, label in specs]
    duplicates = sorted({n for n in out_names if out_names.count(n) > 1})
    if duplicates:
        raise ValueError(f"Duplicate output column: {', '.join(duplicates)}")
    kinds = column_types(rows, len(names))

    groups: dict[tuple[str, ...], list[list[str]]] = {}
    for row in rows:
        groups.setdefault(tuple(row[i].strip() for i in by_idx), []).append(row)
    if not by_idx and not groups:
        groups[()] = []

    out: list[list[str]] = []
    for key, members in groups.items():
        cells = list(key)
        for func, idx, _ in specs:
            if idx is None:
                cells.append(str(len(members)))
            else:
                cells.append(_aggregate(func, [r[idx] for r in members], kinds[idx]))
        out.append(cells)
    result = _new(doc, out_names, out)
    if sort and by_idx:
        sort_rows(result, list(range(len(by_idx))))
    return result


# --------------------------------------------------------------------------- statistics


def describe(doc: CsvDocument, columns: Sequence[ColumnRef] | None = None) -> list[dict[str, Any]]:
    """Return per-column statistics (JSON-serializable).

    Every column reports ``name``, ``type``, ``count`` (non-empty cells), ``empty``,
    ``unique``, ``min`` and ``max``; numeric columns add ``sum``, ``mean`` and ``median``.
    """
    names, rows = _table(doc)
    idxs = [_index(names, c) for c in columns] if columns else list(range(len(names)))
    kinds = column_types(rows, len(names))
    result: list[dict[str, Any]] = []
    for i in idxs:
        filled = [r[i].strip() for r in rows if r[i].strip()]
        info: dict[str, Any] = {
            "name": names[i],
            "type": kinds[i],
            "count": len(filled),
            "empty": len(rows) - len(filled),
            "unique": len(set(filled)),
            "min": None,
            "max": None,
        }
        if kinds[i] in _NUMERIC:
            nums = _numbers(filled)
            mean = sum(nums) / len(nums)
            info.update(
                min=min(nums),
                max=max(nums),
                sum=sum(nums),
                mean=round(mean, 10),
                median=statistics.median(nums),
            )
        elif filled:
            info.update(min=min(filled), max=max(filled))
        result.append(info)
    return result
