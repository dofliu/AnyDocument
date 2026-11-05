"""Mail merge: a DOCX template with ``{{field}}`` placeholders plus CSV/XLSX/JSON records."""

from __future__ import annotations

import copy
import io
import re
import tempfile
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any, Literal

from docx import Document as load_docx
from docx.oxml.ns import qn

from anydoc.converters.tabular import _read_csv, _read_json, _read_xlsx, _unique_names
from anydoc.core.errors import AnyDocError, CorruptFileError
from anydoc.formats.docx.replace import (
    _paragraphs,
    _replace_in_paragraph,
    _roots,
    _run_segments,
)

MissingPolicy = Literal["keep", "blank", "error"]

PLACEHOLDER = re.compile(r"\{\{\s*([^{}]*?)\s*\}\}")
"""Matches ``{{field}}`` (inner whitespace allowed)."""

Record = dict[str, Any]


@dataclass(frozen=True)
class MergeData:
    """Records loaded from a data source."""

    fields: list[str]
    records: list[Record]


def format_value(value: Any) -> str:
    """Render a cell value as text for insertion into the template."""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    if isinstance(value, datetime):
        return (
            value.isoformat(sep=" ")
            if (value.hour or value.minute or value.second)
            else (value.date().isoformat())
        )
    if isinstance(value, date):
        return value.isoformat()
    return str(value)


def load_records(source: str | Path, *, sheet: int | str = 0) -> MergeData:
    """Load records from a CSV/TSV, XLSX or JSON (list of objects) file.

    The first row of a tabular source is the header (field names).
    """
    path = Path(source)
    if not path.is_file():
        raise FileNotFoundError(path)
    suffix = path.suffix.lower()
    header: list[str] | None
    rows: list[list[Any]]
    if suffix in {".csv", ".tsv", ".txt"}:
        header, rows = _read_csv(path)
    elif suffix in {".xlsx", ".xlsm"}:
        header, rows = _read_xlsx(path, sheet)
    elif suffix == ".json":
        tables = _read_json(path)
        header, rows = next(iter(tables.values())) if tables else (None, [])
    else:
        raise AnyDocError(f"Unsupported mail merge data source: {path.name}")
    if header is None:
        raise CorruptFileError(f"Data source has no header row: {path.name}")
    header = _unique_names(header)
    records = [
        {h: (row[i] if i < len(row) else None) for i, h in enumerate(header)} for row in rows
    ]
    return MergeData(header, records)


def template_fields(template: str | Path | bytes) -> list[str]:
    """Return the distinct placeholder names used by a template, in order of appearance."""
    doc = load_docx(io.BytesIO(template) if isinstance(template, bytes) else str(template))
    seen: list[str] = []
    for _, root in _roots(doc, True, True):
        for p in _paragraphs(root):
            joined = "".join(text for _, text in _run_segments(p))
            for m in PLACEHOLDER.finditer(joined):
                if m.group(1) and m.group(1) not in seen:
                    seen.append(m.group(1))
    return seen


def _substitute(paragraphs: Iterator[Any], record: Record, missing: MissingPolicy) -> None:
    for p in paragraphs:
        joined = "".join(text for _, text in _run_segments(p))
        for name in dict.fromkeys(m.group(1) for m in PLACEHOLDER.finditer(joined)):
            if name in record:
                value = format_value(record[name])
            elif missing == "blank":
                value = ""
            elif missing == "error":
                raise KeyError(name)
            else:
                continue
            pattern = re.compile(r"\{\{\s*" + re.escape(name) + r"\s*\}\}")
            _replace_in_paragraph(p, pattern, value, False, 0)


def _check_missing(template_names: Sequence[str], fields: Sequence[str], missing: str) -> None:
    if missing not in {"keep", "blank", "error"}:
        raise ValueError(f"Invalid missing policy: {missing!r}")
    if missing == "error":
        unknown = [n for n in template_names if n not in fields]
        if unknown:
            raise AnyDocError("Template fields missing from data: " + ", ".join(unknown))


def _load(data: MergeData | str | Path) -> MergeData:
    return data if isinstance(data, MergeData) else load_records(data)


def _template_bytes(template: str | Path | bytes) -> bytes:
    if isinstance(template, bytes):
        return template
    return Path(template).read_bytes()


def merge_one(
    template: str | Path | bytes, record: Record, *, missing: MissingPolicy = "keep"
) -> bytes:
    """Fill ``template`` with one record and return the DOCX bytes."""
    doc = load_docx(io.BytesIO(_template_bytes(template)))
    try:
        for _, root in _roots(doc, True, True):
            _substitute(_paragraphs(root), record, missing)
    except KeyError as exc:
        raise AnyDocError(f"Template field missing from data: {exc.args[0]}") from None
    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


def _safe_name(text: str) -> str:
    cleaned = re.sub(r'[\\/:*?"<>|\s]+', "_", text).strip("._")
    return cleaned[:80]


def merge_to_files(
    template: str | Path,
    data: MergeData | str | Path,
    out_dir: str | Path,
    *,
    name_field: str | None = None,
    missing: MissingPolicy = "keep",
) -> list[Path]:
    """Write one DOCX per record into ``out_dir`` and return the paths.

    Files are named ``NNN.docx`` or ``NNN_<value of name_field>.docx``.
    """
    merged = _load(data)
    if name_field is not None and name_field not in merged.fields:
        raise AnyDocError(f"Unknown name field: {name_field}")
    _check_missing(template_fields(template), merged.fields, missing)
    raw = _template_bytes(template)
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    width = max(3, len(str(len(merged.records))))
    paths: list[Path] = []
    for i, record in enumerate(merged.records, start=1):
        stem = f"{i:0{width}d}"
        if name_field is not None:
            label = _safe_name(format_value(record.get(name_field)))
            if label:
                stem += f"_{label}"
        target = out / f"{stem}.docx"
        target.write_bytes(merge_one(raw, record, missing=missing))
        paths.append(target)
    return paths


def _page_break() -> Any:
    from docx.oxml import OxmlElement

    p = OxmlElement("w:p")
    r = OxmlElement("w:r")
    br = OxmlElement("w:br")
    br.set(qn("w:type"), "page")
    r.append(br)
    p.append(r)
    return p


def merge_to_single(
    template: str | Path,
    data: MergeData | str | Path,
    output: str | Path,
    *,
    missing: MissingPolicy = "keep",
) -> Path:
    """Write one DOCX holding every record, each starting on a new page.

    The template body is repeated once per record, so images and styles stay valid.
    Headers/footers are filled from the first record.
    """
    merged = _load(data)
    _check_missing(template_fields(template), merged.fields, missing)
    doc = load_docx(str(template))
    body = doc.element.body
    sect_pr = body.find(qn("w:sectPr"))
    originals = [c for c in body if c is not sect_pr]
    pristine = [copy.deepcopy(c) for c in originals]
    records = merged.records
    try:
        if not records:
            for c in originals:
                body.remove(c)
        for i, record in enumerate(records):
            if i == 0:
                elements = originals
            else:
                elements = [copy.deepcopy(c) for c in pristine]
                anchor = sect_pr if sect_pr is not None else None
                for el in [_page_break(), *elements]:
                    if anchor is not None:
                        anchor.addprevious(el)
                    else:
                        body.append(el)
            for el in elements:
                _substitute(_paragraphs(el), record, missing)
        if records:
            for _kind, root in _roots(doc, False, True):
                _substitute(_paragraphs(root), records[0], missing)
    except KeyError as exc:
        raise AnyDocError(f"Template field missing from data: {exc.args[0]}") from None
    target = Path(output)
    target.parent.mkdir(parents=True, exist_ok=True)
    doc.save(str(target))
    return target


def merge_to_pdf(
    template: str | Path,
    data: MergeData | str | Path,
    output: str | Path,
    *,
    missing: MissingPolicy = "keep",
) -> Path:
    """Merge all records into a single PDF (one or more pages per record) via LibreOffice."""
    from anydoc.core.convert import convert

    target = Path(output)
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="anydoc-merge-") as tmp:
        single = merge_to_single(template, data, Path(tmp) / "merged.docx", missing=missing)
        convert(single, target)
    return target


__all__ = [
    "PLACEHOLDER",
    "MergeData",
    "format_value",
    "load_records",
    "merge_one",
    "merge_to_files",
    "merge_to_pdf",
    "merge_to_single",
    "template_fields",
]
