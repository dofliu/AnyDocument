"""Low-level helpers: encoding detection, delimiter sniffing and type inference."""

from __future__ import annotations

import csv
import io
import re
from collections.abc import Sequence
from datetime import date, datetime
from typing import Any

from anydoc.core.errors import CorruptFileError

CANDIDATE_DELIMITERS = (",", "\t", ";", "|")
_SNIFF_LINES = 50

_INT_RE = re.compile(r"^[+-]?\d+$")
_FLOAT_RE = re.compile(r"^[+-]?(\d+\.\d*|\.\d+|\d+)([eE][+-]?\d+)?$")
_BOOLS = {"true": True, "false": False}


def decode_bytes(data: bytes) -> tuple[str, str, bool]:
    """Decode CSV bytes, returning ``(text, encoding, has_bom)``.

    Tries BOMs first, then UTF-8, then CP950 (reported as ``big5`` when strict Big5
    also decodes), then GB18030.
    """
    if data.startswith(b"\xef\xbb\xbf"):
        return data.decode("utf-8-sig"), "utf-8", True
    if data.startswith((b"\xff\xfe", b"\xfe\xff")):
        try:
            return data.decode("utf-16"), "utf-16", True
        except UnicodeDecodeError as exc:
            raise CorruptFileError(f"Invalid UTF-16 text: {exc}") from exc
    try:
        return data.decode("utf-8"), "utf-8", False
    except UnicodeDecodeError:
        pass
    for enc in ("big5", "cp950", "gb18030"):
        try:
            return data.decode(enc), enc, False
        except UnicodeDecodeError:
            continue
    raise CorruptFileError("Cannot decode file as UTF-8, Big5/CP950 or GB18030")


def sniff_delimiter(text: str, default: str = ",") -> str:
    """Guess the field delimiter from the leading lines of ``text``."""
    lines = [ln for ln in text.splitlines() if ln.strip()][:_SNIFF_LINES]
    if not lines:
        return default
    best, best_score = default, 0.0
    for delim in CANDIDATE_DELIMITERS:
        try:
            counts = [len(r) for r in csv.reader(lines, delimiter=delim)]
        except csv.Error:
            continue
        mode = max(set(counts), key=counts.count)
        if mode < 2:
            continue
        score = counts.count(mode) / len(counts) * 100 + mode
        if score > best_score:
            best, best_score = delim, score
    return best


def infer_type(value: str) -> str:
    """Return the type name of one cell: null, bool, int, float, datetime, date or str."""
    v = value.strip()
    if not v:
        return "null"
    if v.lower() in _BOOLS:
        return "bool"
    if _INT_RE.match(v):
        return "int"
    if _FLOAT_RE.match(v):
        return "float"
    try:
        date.fromisoformat(v)
        return "date"
    except ValueError:
        pass
    try:
        datetime.fromisoformat(v)
        return "datetime"
    except ValueError:
        return "str"


def column_types(rows: Sequence[Sequence[str]], ncols: int) -> list[str]:
    """Infer one type per column (``null`` cells ignored; mixed int/float -> float)."""
    result: list[str] = []
    for c in range(ncols):
        kinds = {infer_type(r[c]) for r in rows if c < len(r)} - {"null"}
        if not kinds:
            result.append("null")
        elif len(kinds) == 1:
            result.append(next(iter(kinds)))
        elif kinds == {"int", "float"}:
            result.append("float")
        else:
            result.append("str")
    return result


def convert_value(value: str, type_name: str) -> Any:
    """Convert a raw cell to a Python value of ``type_name`` (``None`` for empty cells)."""
    v = value.strip()
    if not v or type_name in ("null", "str"):
        return None if not v else value
    if type_name == "bool":
        return _BOOLS[v.lower()]
    if type_name == "int":
        return int(v)
    if type_name == "float":
        return float(v)
    if type_name == "date":
        return date.fromisoformat(v)
    if type_name == "datetime":
        return datetime.fromisoformat(v)
    return value


def looks_like_header(rows: Sequence[Sequence[str]]) -> bool:
    """Heuristic: the first row is a header when it is all non-numeric and unique."""
    if len(rows) < 2:
        return bool(rows)
    first = [c.strip() for c in rows[0]]
    if any(infer_type(c) not in ("str", "null") for c in first):
        return False
    non_empty = [c for c in first if c]
    return bool(non_empty) and len(set(non_empty)) == len(non_empty)


def parse_rows(text: str, delimiter: str) -> list[list[str]]:
    """Parse ``text`` into rows of strings, dropping fully blank lines."""
    reader = csv.reader(io.StringIO(text, newline=""), delimiter=delimiter)
    try:
        return [row for row in reader if row]
    except csv.Error as exc:
        raise CorruptFileError(f"Malformed delimited text: {exc}") from exc
