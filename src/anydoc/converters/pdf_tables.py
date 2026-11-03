"""Export tables extracted from a PDF to CSV or XLSX."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from anydoc.converters.tabular import Table, _write_csv, _write_xlsx
from anydoc.formats.pdf.tables import PdfTable, extract_tables

__all__ = ["pdf_tables_to_csv", "pdf_tables_to_xlsx"]

_INT = re.compile(r"^[+-]?\d{1,18}$")
_FLOAT = re.compile(r"^[+-]?(\d+\.\d*|\.\d+)$")


def _infer(value: str) -> Any:
    v = value.strip()
    if _INT.match(v) and not (len(v.lstrip("+-")) > 1 and v.lstrip("+-").startswith("0")):
        return int(v)
    if _FLOAT.match(v):
        return float(v)
    return value if value != "" else None


def _to_table(t: PdfTable, *, header: bool, infer_types: bool) -> Table:
    rows: list[list[Any]] = [list(r) for r in t.rows]
    head: list[str] | None = None
    if header and rows:
        head = [str(c) for c in rows.pop(0)]
    if infer_types:
        rows = [[_infer(c) for c in r] for r in rows]
    return head, rows


def _read(
    src: str | Path, pages: Any, strategy: str, password: str, min_rows: int
) -> list[PdfTable]:
    return extract_tables(
        Path(src).read_bytes(), pages, strategy=strategy, password=password, min_rows=min_rows
    )


def pdf_tables_to_csv(
    src: str | Path,
    dst_dir: str | Path,
    *,
    pages: Any = None,
    strategy: str = "lines",
    password: str = "",
    header: bool = False,
    infer_types: bool = False,
    min_rows: int = 1,
    delimiter: str = ",",
    encoding: str = "utf-8",
) -> list[Path]:
    """Write every table found in ``src`` to ``dst_dir`` as ``<stem>_p<page>_t<n>.csv``.

    Returns the written paths (empty if the PDF has no tables). Pages in file names are 1-based.
    """
    source = Path(src)
    out_dir = Path(dst_dir)
    suffix = ".tsv" if delimiter == "\t" else ".csv"
    paths: list[Path] = []
    for t in _read(source, pages, strategy, password, min_rows):
        dst = out_dir / f"{source.stem}_p{t.page + 1}_t{t.index + 1}{suffix}"
        paths.append(
            _write_csv(
                _to_table(t, header=header, infer_types=infer_types),
                dst,
                delimiter=delimiter,
                encoding=encoding,
            )
        )
    return paths


def pdf_tables_to_xlsx(
    src: str | Path,
    dst: str | Path,
    *,
    pages: Any = None,
    strategy: str = "lines",
    password: str = "",
    header: bool = False,
    infer_types: bool = True,
    min_rows: int = 1,
) -> Path:
    """Write every table found in ``src`` to one workbook, one sheet per table.

    Sheets are named ``p<page>_t<n>`` (1-based). A PDF without tables yields a single empty sheet.
    """
    tables: dict[str, Table] = {}
    for t in _read(src, pages, strategy, password, min_rows):
        tables[f"p{t.page + 1}_t{t.index + 1}"] = _to_table(
            t, header=header, infer_types=infer_types
        )
    return _write_xlsx(tables, Path(dst))
