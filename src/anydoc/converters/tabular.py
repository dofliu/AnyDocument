"""XLSX, CSV, TSV and JSON interconversion (multi-sheet export, typed cells)."""

from __future__ import annotations

import csv
import json
import re
from collections.abc import Callable
from datetime import date, datetime, time
from pathlib import Path
from typing import Any

from openpyxl import Workbook

from anydoc.core.convert import ConversionGraph, Converter, graph
from anydoc.core.errors import CorruptFileError
from anydoc.formats.csv import CsvHandler
from anydoc.formats.xlsx import XlsxDocument, XlsxHandler

Table = tuple[list[str] | None, list[list[Any]]]
"""A table as ``(header, rows)``; ``header`` is ``None`` when the data has no header row."""

_INVALID_SHEET_CHARS = re.compile(r"[\[\]:*?/\\]")
_INVALID_FILE_CHARS = re.compile(r'[\\/:*?"<>|\s]+')
_MAX_SHEET_NAME = 31


# --------------------------------------------------------------------------------------------
# Readers: every source yields typed Python values (int/float/bool/date/datetime/str/None)
# --------------------------------------------------------------------------------------------


def _unique_names(names: list[str]) -> list[str]:
    """Make header names non-empty and unique (``colN`` for blanks, ``name_2`` for repeats)."""
    seen: dict[str, int] = {}
    out: list[str] = []
    for i, raw in enumerate(names):
        name = raw.strip() or f"col{i + 1}"
        if name in seen:
            seen[name] += 1
            name = f"{name}_{seen[name]}"
            seen[name] = 1
        else:
            seen[name] = 1
        out.append(name)
    return out


def _read_csv(src: Path) -> Table:
    doc = CsvHandler().open(src)
    try:
        width = doc.column_count
        types = doc.column_types
        from anydoc.formats.csv.parse import convert_value

        rows: list[list[Any]] = []
        for raw in doc.rows:
            row = [convert_value(c, types[i]) if i < len(raw) else None for i, c in enumerate(raw)]
            row.extend([None] * (width - len(row)))
            rows.append(row)
        header = _unique_names(list(doc.header)) if doc.has_header else None
        if header is not None:
            header.extend(f"col{i + 1}" for i in range(len(header), width))
        return header, rows
    finally:
        doc.close()


def _sheet_rows(doc: XlsxDocument, sheet: int | str) -> list[list[Any]]:
    rows = doc.rows(sheet)
    width = max((len(r) for r in rows), default=0)
    return [list(r) + [None] * (width - len(r)) for r in rows]


def _table_from_rows(rows: list[list[Any]]) -> Table:
    """Treat the first row as header when it is all non-empty text."""
    first = rows[0] if rows else []
    cells = [v for v in first if v is not None]
    if cells and all(isinstance(v, str) and v.strip() for v in cells):
        return _unique_names([v if isinstance(v, str) else "" for v in first]), rows[1:]
    return None, rows


def _open_xlsx(src: Path) -> XlsxDocument:
    return XlsxHandler().open(src)


def _read_xlsx(src: Path, sheet: int | str = 0) -> Table:
    doc = _open_xlsx(src)
    try:
        return _table_from_rows(_sheet_rows(doc, sheet))
    finally:
        doc.close()


def _read_xlsx_all(src: Path) -> dict[str, Table]:
    doc = _open_xlsx(src)
    try:
        return {s.name: _table_from_rows(_sheet_rows(doc, s.index)) for s in doc.sheets()}
    finally:
        doc.close()


def _json_cell(value: Any) -> Any:
    if value is None or isinstance(value, str | int | float | bool | date | datetime):
        return value
    return json.dumps(value, ensure_ascii=False)


def _table_from_records(records: list[Any]) -> Table:
    if all(isinstance(r, dict) for r in records):
        dicts = [{str(k): v for k, v in r.items()} for r in records]
        keys: list[str] = []
        for r in dicts:
            for k in r:
                if k not in keys:
                    keys.append(k)
        if not keys:
            return None, [[] for _ in dicts]
        return keys, [[_json_cell(r.get(k)) for k in keys] for r in dicts]
    if all(isinstance(r, list) for r in records):
        width = max((len(r) for r in records), default=0)
        return None, [[_json_cell(v) for v in r] + [None] * (width - len(r)) for r in records]
    raise ValueError("JSON array must contain only objects or only arrays")


def _tables_from_json(data: Any) -> dict[str, Table]:
    """Map JSON to named tables.

    Accepted shapes: array of objects, array of arrays, object whose values are arrays
    (one table per key, i.e. one sheet each), or a single flat object (one row).
    """
    if isinstance(data, list):
        return {"Sheet1": _table_from_records(data)}
    if isinstance(data, dict):
        if data and all(isinstance(v, list) for v in data.values()):
            return {str(k): _table_from_records(v) for k, v in data.items()}
        return {"Sheet1": _table_from_records([data])}
    raise ValueError("JSON root must be an array or an object to be converted to a table")


def _read_json(src: Path) -> dict[str, Table]:
    try:
        data = json.loads(src.read_bytes().decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CorruptFileError(f"Invalid JSON {src}: {exc}") from exc
    return _tables_from_json(data)


def _pick(tables: dict[str, Table], sheet: int | str | None) -> Table:
    if not tables:
        return None, []
    names = list(tables)
    if sheet is None:
        return tables[names[0]]
    if isinstance(sheet, int):
        try:
            return tables[names[sheet]]
        except IndexError:
            raise KeyError(f"No such sheet: {sheet!r}") from None
    if sheet not in tables:
        raise KeyError(f"No such sheet: {sheet!r}")
    return tables[sheet]


# --------------------------------------------------------------------------------------------
# Writers
# --------------------------------------------------------------------------------------------


def _csv_cell(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, datetime):
        if value.time() == time(0):
            return value.date().isoformat()
        return value.isoformat(sep=" ")
    if isinstance(value, date | time):
        return value.isoformat()
    return str(value)


def _write_csv(table: Table, dst: Path, *, delimiter: str = ",", encoding: str = "utf-8") -> Path:
    header, rows = table
    dst.parent.mkdir(parents=True, exist_ok=True)
    with dst.open("w", encoding=encoding, newline="") as fh:
        writer = csv.writer(fh, delimiter=delimiter, lineterminator="\n")
        if header is not None:
            writer.writerow(header)
        for row in rows:
            writer.writerow([_csv_cell(v) for v in row])
    return dst


def _json_default(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date | time):
        return value.isoformat()
    return str(value)


def _records(table: Table) -> list[Any]:
    header, rows = table
    if header is None:
        return [list(r) for r in rows]
    return [dict(zip(header, r, strict=False)) for r in rows]


def _write_json(payload: Any, dst: Path) -> Path:
    dst.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(payload, ensure_ascii=False, indent=2, default=_json_default)
    dst.write_text(text + "\n", encoding="utf-8")
    return dst


def _sheet_title(name: str, used: set[str]) -> str:
    base = _INVALID_SHEET_CHARS.sub("_", name).strip("'") or "Sheet"
    base = base[:_MAX_SHEET_NAME]
    title, n = base, 1
    while title.lower() in used:
        n += 1
        suffix = f"_{n}"
        title = base[: _MAX_SHEET_NAME - len(suffix)] + suffix
    used.add(title.lower())
    return title


def _write_xlsx(tables: dict[str, Table], dst: Path) -> Path:
    wb = Workbook()
    wb.remove(wb.active)  # type: ignore[arg-type]
    used: set[str] = set()
    for name, (header, rows) in tables.items() or {"Sheet1": (None, [])}.items():
        ws = wb.create_sheet(_sheet_title(name, used))
        if header is not None:
            ws.append(header)
        for row in rows:
            ws.append(list(row))
        for row_cells in ws.iter_rows():
            for cell in row_cells:
                v = cell.value
                if isinstance(v, datetime):
                    cell.number_format = (
                        "yyyy-mm-dd" if v.time() == time(0) else "yyyy-mm-dd hh:mm:ss"
                    )
                elif isinstance(v, date):
                    cell.number_format = "yyyy-mm-dd"
    dst.parent.mkdir(parents=True, exist_ok=True)
    wb.save(dst)
    wb.close()
    return dst


# --------------------------------------------------------------------------------------------
# Public conversion functions
# --------------------------------------------------------------------------------------------


def xlsx_to_csv(
    src: str | Path,
    dst: str | Path,
    *,
    sheet: int | str = 0,
    delimiter: str = ",",
    encoding: str = "utf-8",
) -> Path:
    """Export one worksheet (by index or name; default the first) to a CSV/TSV file.

    Formulas are written as their cached values; dates as ISO text; booleans as ``true``/``false``.
    """
    table = (None, _sheet_rows_of(Path(src), sheet))
    return _write_csv(table, Path(dst), delimiter=delimiter, encoding=encoding)


def _sheet_rows_of(src: Path, sheet: int | str) -> list[list[Any]]:
    doc = _open_xlsx(src)
    try:
        return _sheet_rows(doc, sheet)
    finally:
        doc.close()


def xlsx_to_csv_files(
    src: str | Path,
    out_dir: str | Path,
    *,
    delimiter: str = ",",
    encoding: str = "utf-8",
    stem: str | None = None,
) -> list[Path]:
    """Export every worksheet to ``<stem>-<sheet name>.csv`` inside ``out_dir``.

    Returns the written paths in sheet order.
    """
    src_p = Path(src)
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    base = stem or src_p.stem
    ext = "tsv" if delimiter == "\t" else "csv"
    doc = _open_xlsx(src_p)
    try:
        sheets = doc.sheets()
        rows_by_sheet = [_sheet_rows(doc, s.index) for s in sheets]
    finally:
        doc.close()
    written: list[Path] = []
    used: set[str] = set()
    for info, rows in zip(sheets, rows_by_sheet, strict=True):
        label = _INVALID_FILE_CHARS.sub("_", info.name).strip("_") or f"sheet{info.index + 1}"
        name = f"{base}-{label}"
        unique, n = name, 1
        while unique.lower() in used:
            n += 1
            unique = f"{name}_{n}"
        used.add(unique.lower())
        written.append(
            _write_csv(
                (None, rows), out / f"{unique}.{ext}", delimiter=delimiter, encoding=encoding
            )
        )
    return written


def csv_to_xlsx(src: str | Path, dst: str | Path, *, sheet_name: str = "Sheet1") -> Path:
    """Convert a CSV/TSV file to XLSX, writing numbers, booleans and dates as typed cells."""
    return _write_xlsx({sheet_name: _read_csv(Path(src))}, Path(dst))


def xlsx_to_json(src: str | Path, dst: str | Path, *, sheet: int | str | None = None) -> Path:
    """Convert a workbook to JSON.

    The first row is the header when it is all text; each data row becomes an object keyed by
    header (a list when there is no header). With ``sheet`` given, the output is that sheet's
    array. Otherwise a one-sheet workbook yields an array and a multi-sheet workbook yields an
    object ``{sheet name: array}``.
    """
    src_p = Path(src)
    if sheet is not None:
        payload: Any = _records(_read_xlsx(src_p, sheet))
    else:
        tables = _read_xlsx_all(src_p)
        if len(tables) == 1:
            payload = _records(next(iter(tables.values())))
        else:
            payload = {name: _records(t) for name, t in tables.items()}
    return _write_json(payload, Path(dst))


def json_to_xlsx(src: str | Path, dst: str | Path) -> Path:
    """Convert JSON (array of objects/arrays, or object of arrays = one sheet per key) to XLSX."""
    return _write_xlsx(_read_json(Path(src)), Path(dst))


def csv_to_json(src: str | Path, dst: str | Path) -> Path:
    """Convert a CSV/TSV file to a JSON array of typed objects (or arrays if headerless)."""
    return _write_json(_records(_read_csv(Path(src))), Path(dst))


def json_to_csv(
    src: str | Path,
    dst: str | Path,
    *,
    sheet: int | str | None = None,
    delimiter: str = ",",
    encoding: str = "utf-8",
) -> Path:
    """Convert JSON to CSV/TSV; for an object of arrays ``sheet`` picks the key (default first)."""
    table = _pick(_read_json(Path(src)), sheet)
    return _write_csv(table, Path(dst), delimiter=delimiter, encoding=encoding)


def csv_to_csv(
    src: str | Path, dst: str | Path, *, delimiter: str = ",", encoding: str = "utf-8"
) -> Path:
    """Re-encode / re-delimit a delimited text file (e.g. CSV <-> TSV, Big5 -> UTF-8)."""
    doc = CsvHandler().open(Path(src))
    try:
        rows = ([doc.header] if doc.has_header else []) + doc.rows
    finally:
        doc.close()
    dst_p = Path(dst)
    dst_p.parent.mkdir(parents=True, exist_ok=True)
    with dst_p.open("w", encoding=encoding, newline="") as fh:
        csv.writer(fh, delimiter=delimiter, lineterminator="\n").writerows(rows)
    return dst_p


def register_tabular(target: ConversionGraph | None = None) -> None:
    """Register XLSX/CSV/TSV/JSON converters on ``target`` (default: global graph)."""
    g = target if target is not None else graph

    def add(src: str, dst: str, fn: Callable[[Path, Path], Any]) -> None:
        def func(s: Path, d: Path) -> None:
            fn(s, d)

        g.add(Converter(src, dst, func, name=f"{src}->{dst}"))

    for delim_fmt, delim in (("csv", ","), ("tsv", "\t")):
        add("xlsx", delim_fmt, lambda s, d, dl=delim: xlsx_to_csv(s, d, delimiter=dl))  # type: ignore[misc]
        add(delim_fmt, "xlsx", csv_to_xlsx)
        add(delim_fmt, "json", csv_to_json)
        add("json", delim_fmt, lambda s, d, dl=delim: json_to_csv(s, d, delimiter=dl))  # type: ignore[misc]
    add("csv", "tsv", lambda s, d: csv_to_csv(s, d, delimiter="\t"))
    add("tsv", "csv", lambda s, d: csv_to_csv(s, d, delimiter=","))
    add("xlsx", "json", xlsx_to_json)
    add("json", "xlsx", json_to_xlsx)
