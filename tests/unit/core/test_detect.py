"""Tests for content-based format detection."""

from __future__ import annotations

import io
import zipfile
from pathlib import Path

import pytest

from anydoc import detect
from anydoc.core.detect import detect_bytes
from anydoc.core.errors import UnsupportedFormatError


def make_zip(files: dict[str, bytes], *, mimetype: str | None = None) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        if mimetype:
            zf.writestr("mimetype", mimetype, compress_type=zipfile.ZIP_STORED)
        for name, data in files.items():
            zf.writestr(name, data)
    return buf.getvalue()


def ole(stream: str) -> bytes:
    return (
        b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\0" * 64 + stream.encode("utf-16-le") + b"\0" * 64
    )


ODF = "application/vnd.oasis.opendocument."
DXF = "0\nSECTION\n2\nHEADER\n9\n$ACADVER\n1\nAC1027\n0\nENDSEC\n0\nEOF\n"

CASES: list[tuple[str, bytes]] = [
    ("pdf", b"%PDF-1.7\n%\xe2\xe3\n"),
    ("docx", make_zip({"[Content_Types].xml": b"<x/>", "word/document.xml": b"<x/>"})),
    ("xlsx", make_zip({"[Content_Types].xml": b"<x/>", "xl/workbook.xml": b"<x/>"})),
    ("pptx", make_zip({"[Content_Types].xml": b"<x/>", "ppt/presentation.xml": b"<x/>"})),
    ("odt", make_zip({"content.xml": b"<x/>"}, mimetype=ODF + "text")),
    ("ods", make_zip({"content.xml": b"<x/>"}, mimetype=ODF + "spreadsheet")),
    ("odp", make_zip({"content.xml": b"<x/>"}, mimetype=ODF + "presentation")),
    ("epub", make_zip({"a.xhtml": b"<x/>"}, mimetype="application/epub+zip")),
    ("doc", ole("WordDocument")),
    ("xls", ole("Workbook")),
    ("ppt", ole("PowerPoint Document")),
    ("dxf", DXF.encode()),
    ("dxf", DXF.replace("\n", "\r\n").encode()),
    ("dwg", b"AC1027" + b"\0" * 20),
    ("png", b"\x89PNG\r\n\x1a\n" + b"\0" * 20),
    ("jpeg", b"\xff\xd8\xff\xe0" + b"\0" * 20),
    ("gif", b"GIF89a" + b"\0" * 20),
    ("bmp", b"BM" + b"\0" * 20),
    ("webp", b"RIFF\0\0\0\0WEBPVP8 "),
    ("tiff", b"II*\x00" + b"\0" * 8),
    ("tiff", b"MM\x00*" + b"\0" * 8),
    ("rtf", b"{\\rtf1\\ansi hello}"),
    ("html", "<!DOCTYPE html><html><body>中文</body></html>".encode()),
    ("svg", b'<?xml version="1.0"?><svg xmlns="http://www.w3.org/2000/svg"/>'),
    ("xml", '<?xml version="1.0"?><root>中文</root>'.encode()),
    ("json", '{"名稱": "值"}'.encode()),
    ("json", b"[1, 2, 3]"),
    ("csv", "姓名,年齡\n小明,3\n小華,4\n".encode()),
    ("csv", "姓名,年齡\n小明,3\n".encode("utf-8-sig")),
    ("csv", "姓名,年齡\n小明,3\n小華,4\n".encode("big5")),
    ("tsv", b"a\tb\n1\t2\n"),
    ("txt", "這是一段純文字。\n第二行。\n".encode()),
    ("txt", "這是 UTF-16 文字".encode("utf-16")),
    ("txt", b"plain ascii text"),
]


@pytest.mark.parametrize(("fmt", "data"), CASES)
def test_detect_bytes(fmt: str, data: bytes) -> None:
    assert detect_bytes(data).format == fmt


@pytest.mark.parametrize(("fmt", "data"), CASES)
def test_detect_path_ignores_wrong_extension(tmp_path: Path, fmt: str, data: bytes) -> None:
    f = tmp_path / "misleading.xyz"
    f.write_bytes(data)
    assert detect(f).format == fmt


def test_detect_returns_mime(tmp_path: Path) -> None:
    f = tmp_path / "a.txt"
    f.write_bytes(b"%PDF-1.4\n")
    d = detect(f)
    assert (d.format, d.mime) == ("pdf", "application/pdf")


def test_extension_wrong_for_binary(tmp_path: Path) -> None:
    f = tmp_path / "report.docx"
    f.write_bytes(b"%PDF-1.4\n")
    assert detect(str(f)).format == "pdf"


@pytest.mark.parametrize(("name", "fmt"), [("a.md", "markdown"), ("a.yml", "yaml")])
def test_extension_hint_for_ambiguous_text(tmp_path: Path, name: str, fmt: str) -> None:
    f = tmp_path / name
    f.write_text("# 標題\n內容\n", encoding="utf-8")
    assert detect(f).format == fmt


def test_extension_hint_does_not_override_content(tmp_path: Path) -> None:
    f = tmp_path / "a.md"
    f.write_text('{"a": 1}', encoding="utf-8")
    assert detect(f).format == "json"


def test_zip_without_known_layout_is_unsupported() -> None:
    with pytest.raises(UnsupportedFormatError):
        detect_bytes(make_zip({"a.txt": b"hi"}))


def test_zip_with_unknown_mimetype_is_unsupported() -> None:
    with pytest.raises(UnsupportedFormatError):
        detect_bytes(make_zip({"a": b"1"}, mimetype="application/x-other"))


def test_corrupt_zip_is_unsupported() -> None:
    with pytest.raises(UnsupportedFormatError):
        detect_bytes(b"PK\x03\x04garbage-not-a-zip")


def test_unknown_ole_is_unsupported() -> None:
    with pytest.raises(UnsupportedFormatError):
        detect_bytes(ole("Something"))


def test_ole_scans_whole_file_from_path(tmp_path: Path) -> None:
    f = tmp_path / "a.bin"
    f.write_bytes(
        b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\0" * 200_000 + "Workbook".encode("utf-16-le")
    )
    assert detect(f).format == "xls"


def test_binary_garbage_is_unsupported() -> None:
    with pytest.raises(UnsupportedFormatError):
        detect_bytes(bytes(range(256)))


def test_undecodable_non_utf8_is_unsupported() -> None:
    with pytest.raises(UnsupportedFormatError):
        detect_bytes(b"\xff\xff\xff\xff\xff\xff")


def test_bad_utf16_is_unsupported() -> None:
    with pytest.raises(UnsupportedFormatError):
        detect_bytes(b"\xff\xfe\x00\xd8")


def test_whitespace_only_is_unsupported() -> None:
    with pytest.raises(UnsupportedFormatError):
        detect_bytes(b"   \n\n")


def test_empty_file_is_unsupported(tmp_path: Path) -> None:
    f = tmp_path / "empty.pdf"
    f.write_bytes(b"")
    with pytest.raises(UnsupportedFormatError):
        detect(f)


def test_missing_file_raises(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        detect(tmp_path / "nope.pdf")


def test_truncated_multibyte_head_still_text(tmp_path: Path) -> None:
    f = tmp_path / "big.txt"
    f.write_bytes(("中" * 30000).encode())  # > 64 KiB head, cut mid-character
    assert detect(f).format == "txt"


def test_invalid_json_braces_fall_back_to_text() -> None:
    assert detect_bytes(b"{not json at all").format == "txt"


def test_single_column_text_is_not_csv() -> None:
    assert detect_bytes(b"a\nb\nc\n").format == "txt"


def test_inconsistent_columns_not_csv() -> None:
    assert detect_bytes(b"a,b\n1,2,3\n").format == "txt"


@pytest.mark.parametrize(
    ("name", "data", "fmt"),
    [
        ("a.csv", "品名;單價\n筆記本;45\n原子筆;12\n".encode(), "csv"),
        ("a.csv", b"a|b\n1|2\n", "csv"),
        ("a.tsv", b"a;b\n1;2\n", "tsv"),
        ("a.txt", b"a;b\n1;2\n", "txt"),
        (None, b"a;b\n1;2\n", "txt"),
        ("a.csv", b"just one line of prose; nothing else\n", "txt"),
    ],
)
def test_semicolon_pipe_need_extension_hint(name: str | None, data: bytes, fmt: str) -> None:
    assert detect_bytes(data, name).format == fmt
