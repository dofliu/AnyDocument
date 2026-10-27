"""Format detection from file content (magic bytes), falling back to hints."""

from __future__ import annotations

import csv
import io
import json
import zipfile
from dataclasses import dataclass
from pathlib import Path

from anydoc.core.errors import UnsupportedFormatError

_HEAD_SIZE = 64 * 1024
_OLE_SCAN_SIZE = 4 * 1024 * 1024

_OLE_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"

# Extension -> format for formats that cannot be told apart from plain text by content.
_TEXT_EXT_HINTS = {
    "md": "markdown",
    "markdown": "markdown",
    "yaml": "yaml",
    "yml": "yaml",
    "tsv": "tsv",
    "csv": "csv",
}

MIME_TYPES: dict[str, str] = {
    "pdf": "application/pdf",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    "odt": "application/vnd.oasis.opendocument.text",
    "ods": "application/vnd.oasis.opendocument.spreadsheet",
    "odp": "application/vnd.oasis.opendocument.presentation",
    "epub": "application/epub+zip",
    "doc": "application/msword",
    "xls": "application/vnd.ms-excel",
    "ppt": "application/vnd.ms-powerpoint",
    "dxf": "image/vnd.dxf",
    "dwg": "image/vnd.dwg",
    "png": "image/png",
    "jpeg": "image/jpeg",
    "gif": "image/gif",
    "bmp": "image/bmp",
    "webp": "image/webp",
    "tiff": "image/tiff",
    "svg": "image/svg+xml",
    "rtf": "application/rtf",
    "html": "text/html",
    "xml": "application/xml",
    "json": "application/json",
    "csv": "text/csv",
    "tsv": "text/tab-separated-values",
    "markdown": "text/markdown",
    "yaml": "application/yaml",
    "txt": "text/plain",
}


@dataclass(frozen=True)
class Detection:
    """Result of format detection."""

    format: str
    mime: str


def _zip_format(source: Path | bytes) -> str | None:
    try:
        zf = zipfile.ZipFile(source if isinstance(source, Path) else io.BytesIO(source))
    except (zipfile.BadZipFile, OSError):
        return None
    with zf:
        names = set(zf.namelist())
        if "mimetype" in names:
            try:
                mt = zf.read("mimetype")[:100].decode("ascii", "ignore").strip()
            except (KeyError, RuntimeError):
                mt = ""
            for fmt in ("odt", "ods", "odp", "epub"):
                if mt == MIME_TYPES[fmt]:
                    return fmt
        if "[Content_Types].xml" in names:
            if any(n.startswith("word/") for n in names):
                return "docx"
            if any(n.startswith("xl/") for n in names):
                return "xlsx"
            if any(n.startswith("ppt/") for n in names):
                return "pptx"
    return None


def _ole_format(data: bytes) -> str | None:
    def utf16(s: str) -> bytes:
        return s.encode("utf-16-le")

    if utf16("WordDocument") in data:
        return "doc"
    if utf16("Workbook") in data:
        return "xls"
    if utf16("PowerPoint Document") in data:
        return "ppt"
    return None


def _decode_text(data: bytes) -> str | None:
    """Decode bytes as text, or return ``None`` if they look binary."""
    if data.startswith(b"\xef\xbb\xbf"):
        return data.decode("utf-8-sig", "replace")
    if data.startswith((b"\xff\xfe", b"\xfe\xff")):
        try:
            return data.decode("utf-16")
        except UnicodeDecodeError:
            return None
    if b"\x00" in data:
        return None
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError as exc:
        # A truncated head may cut a multi-byte character at the very end.
        if exc.start >= len(data) - 3:
            return data[: exc.start].decode("utf-8")
    try:
        return data.decode("cp950")
    except UnicodeDecodeError:
        return None


def _looks_like_dxf(text: str) -> bool:
    lines = [ln.strip() for ln in text.splitlines()[:12]]
    return len(lines) >= 4 and lines[0] == "0" and lines[1] == "SECTION" and lines[2] == "2"


def _looks_like_delimited(text: str, delimiter: str) -> bool:
    lines = [ln for ln in text.splitlines() if ln.strip()][:20]
    if len(lines) < 2:
        return False
    try:
        counts = [len(row) for row in csv.reader(lines, delimiter=delimiter)]
    except csv.Error:
        return False
    return counts[0] > 1 and len(set(counts)) == 1


def _text_format(text: str, ext_hint: str | None) -> str:
    stripped = text.lstrip()
    lower = stripped[:512].lower()
    if _looks_like_dxf(text):
        return "dxf"
    if stripped.startswith("{\\rtf"):
        return "rtf"
    if lower.startswith(("<!doctype html", "<html")) or "<html" in lower:
        return "html"
    if stripped.startswith("<"):
        if "<svg" in lower:
            return "svg"
        return "xml"
    if stripped.startswith(("{", "[")):
        try:
            json.loads(text)
            return "json"
        except ValueError:
            pass
    if ext_hint in ("md", "markdown", "yaml", "yml"):
        return _TEXT_EXT_HINTS[ext_hint]
    if _looks_like_delimited(text, "\t"):
        return "tsv"
    if _looks_like_delimited(text, ","):
        return "csv"
    # Semicolon/pipe tables are too ambiguous to claim without a .csv/.tsv extension.
    if ext_hint in ("csv", "tsv", "tab") and any(
        _looks_like_delimited(text, d) for d in (";", "|")
    ):
        return "csv" if ext_hint == "csv" else "tsv"
    return "txt"


def detect_bytes(data: bytes, filename: str | None = None) -> Detection:
    """Detect the format of in-memory content. ``filename`` is only a weak hint."""
    return _detect(data, data, filename)


def _detect(head: bytes, source: Path | bytes, filename: str | None) -> Detection:
    fmt = _detect_binary(head, source)
    if fmt is None and not head.startswith((b"PK", _OLE_MAGIC)):
        text = _decode_text(head)
        if text is not None and text.strip():
            ext = Path(filename).suffix.lower().lstrip(".") if filename else None
            fmt = _text_format(text, ext)
    if fmt is None:
        raise UnsupportedFormatError(f"Unrecognized file format: {filename or '<bytes>'}")
    return Detection(fmt, MIME_TYPES[fmt])


def _detect_binary(head: bytes, source: Path | bytes) -> str | None:
    if head.startswith(b"%PDF-") or b"%PDF-" in head[:1024]:
        return "pdf"
    if head.startswith(b"PK\x03\x04") or head.startswith(b"PK\x05\x06"):
        return _zip_format(source)
    if head.startswith(_OLE_MAGIC):
        data = head
        if isinstance(source, Path):
            with source.open("rb") as fh:
                data = fh.read(_OLE_SCAN_SIZE)
        elif len(source) > len(head):
            data = source[:_OLE_SCAN_SIZE]
        return _ole_format(data)
    if head.startswith(b"AC10") and head[:6].isascii() and head[4:6].isdigit():
        return "dwg"
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if head.startswith(b"\xff\xd8\xff"):
        return "jpeg"
    if head.startswith((b"GIF87a", b"GIF89a")):
        return "gif"
    if head.startswith(b"BM") and len(head) >= 14:
        return "bmp"
    if head.startswith(b"RIFF") and head[8:12] == b"WEBP":
        return "webp"
    if head.startswith((b"II*\x00", b"MM\x00*")):
        return "tiff"
    return None


def detect(path: str | Path) -> Detection:
    """Detect a file's format from its content; the extension is only a weak hint.

    Raises:
        UnsupportedFormatError: if the file is empty, binary-unknown or unreadable as text.
        FileNotFoundError: if ``path`` does not exist.
    """
    p = Path(path)
    with p.open("rb") as fh:
        head = fh.read(_HEAD_SIZE)
    if not head:
        raise UnsupportedFormatError(f"Empty file: {p}")
    return _detect(head, p, p.name)
