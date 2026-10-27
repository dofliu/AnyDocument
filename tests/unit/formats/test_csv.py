"""Tests for the CSV/TSV handler (T010)."""

from __future__ import annotations

from collections.abc import Callable
from datetime import date, datetime
from pathlib import Path

import pytest

import anydoc
from anydoc.core.errors import CorruptFileError
from anydoc.core.model import Capability, Document
from anydoc.core.registry import registry
from anydoc.formats.csv import (
    CsvHandler,
    TsvHandler,
    column_types,
    convert_value,
    decode_bytes,
    infer_type,
    sniff_delimiter,
)
from tests.fixtures.generate import TABLE_HEADER, TABLE_ROWS

Sample = Callable[[str], Path]
EXPECTED_ROWS = [[str(c) for c in r] for r in TABLE_ROWS]


@pytest.fixture(autouse=True)
def _handlers() -> None:
    for h in (CsvHandler, TsvHandler):
        registry.register(h, replace=True)


@pytest.mark.parametrize(
    ("key", "encoding", "bom"),
    [("csv", "utf-8", False), ("csv-bom", "utf-8", True), ("csv-big5", "big5", False)],
)
def test_encoding_detection(sample: Sample, key: str, encoding: str, bom: bool) -> None:
    doc = CsvHandler().open(sample(key))
    assert doc.encoding == encoding
    assert doc.has_bom is bom
    assert doc.header == list(TABLE_HEADER)
    assert doc.rows == EXPECTED_ROWS
    assert doc.metadata.extra["encoding"] == encoding


def test_cp950_only_bytes() -> None:
    # U+3000-range / extended chars valid in CP950 but not strict Big5
    data = "﹉,b\n1,2\n".encode("cp950", errors="strict")
    text, enc, _ = decode_bytes(data)
    assert enc in ("big5", "cp950")
    assert text.startswith("﹉")


def test_utf16_and_gb18030_and_invalid() -> None:
    assert decode_bytes("a,b\n".encode("utf-16"))[1:] == ("utf-16", True)
    assert decode_bytes("简体字".encode("gb18030"))[1] in ("big5", "cp950", "gb18030")
    with pytest.raises(CorruptFileError):
        decode_bytes(b"\xff\xfe\x00")
    with pytest.raises(CorruptFileError):
        decode_bytes(b"\x81\x20\xff\xff\x80\x80")


@pytest.mark.parametrize(
    ("text", "delim"),
    [
        ("a,b,c\n1,2,3\n", ","),
        ("a\tb\tc\n1\t2\t3\n", "\t"),
        ("a;b;c\n1;2;3\n", ";"),
        ("a|b\n1|2\n", "|"),
        ("single\nline\n", ","),
        ("", ","),
        ('"a,b";c\n"1,2";3\n', ";"),
    ],
)
def test_sniff_delimiter(text: str, delim: str) -> None:
    assert sniff_delimiter(text) == delim


def test_tsv_sample_and_default(sample: Sample) -> None:
    from tests.fixtures.generate import SAMPLES

    assert sniff_delimiter("a\tb\n1\t2\n") == "\t"
    assert TsvHandler.default_delimiter == "\t"
    assert "csv" in SAMPLES


@pytest.mark.parametrize(
    ("value", "kind"),
    [
        ("", "null"), ("  ", "null"), ("true", "bool"), ("FALSE", "bool"), ("42", "int"),
        ("-7", "int"), ("3.14", "float"), ("1e5", "float"), (".5", "float"),
        ("2024-01-31", "date"), ("2024-01-31T10:20:30", "datetime"), ("王小明", "str"),
        ("12abc", "str"),
    ],
)  # fmt: skip
def test_infer_type(value: str, kind: str) -> None:
    assert infer_type(value) == kind


def test_column_types_and_conversion() -> None:
    rows = [["1", "1.5", "x", "", "2024-01-01", "true"], ["2", "2", "3", "", "2024-02-01", "false"]]
    types = column_types(rows, 6)
    assert types == ["int", "float", "str", "null", "date", "bool"]
    assert convert_value("1", "int") == 1
    assert convert_value("2", "float") == 2.0
    assert convert_value("2024-01-01", "date") == date(2024, 1, 1)
    assert convert_value("2024-01-01T01:02:03", "datetime") == datetime(2024, 1, 1, 1, 2, 3)
    assert convert_value("true", "bool") is True
    assert convert_value("", "int") is None
    assert convert_value("abc", "str") == "abc"
    assert convert_value("abc", "weird") == "abc"


def test_typed_rows_and_records(sample: Sample) -> None:
    doc = CsvHandler().open(sample("csv"))
    assert doc.column_types == ["str", "str", "int"]
    assert doc.typed_rows()[0] == ["王小明", "研發部", 90]
    assert doc.records()[1] == {"姓名": "陳美玲", "部門": "行銷部", "分數": 85}


def test_headerless_records() -> None:
    doc = CsvHandler().open(b"1,2\n3,4\n")
    assert not doc.has_header
    assert doc.header == []
    assert doc.records() == [{"col1": 1, "col2": 2}, {"col1": 3, "col2": 4}]


def test_parts_text_metadata(sample: Sample) -> None:
    doc = CsvHandler().open(sample("csv"))
    assert isinstance(doc, Document)
    [part] = doc.parts()
    assert (part.kind, part.index, part.size) == ("table", 0, (3.0, 3.0))
    assert doc.text().splitlines()[0] == "姓名\t部門\t分數"
    assert "王小明\t研發部\t90" in doc.text()
    assert doc.metadata.mime == "text/csv"
    assert doc.metadata.extra["row_count"] == 3
    assert doc.metadata.extra["column_types"] == ["str", "str", "int"]


def test_tsv_open(tmp_path: Path) -> None:
    p = tmp_path / "a.tsv"
    p.write_text("姓名\t分數\n王\t1\n李\t2\n", encoding="utf-8")
    doc = TsvHandler().open(p)
    assert doc.format == "tsv"
    assert doc.delimiter == "\t"
    assert doc.metadata.mime == "text/tab-separated-values"
    assert doc.rows == [["王", "1"], ["李", "2"]]


def test_quoted_fields_and_ragged_rows() -> None:
    doc = CsvHandler().open(b'a,b\n"x,1","line\nbreak"\n\n3\n')
    assert doc.rows == [["x,1", "line\nbreak"], ["3"]]
    assert doc.column_count == 2


def test_malformed_raises() -> None:
    import csv as _csv

    _csv.field_size_limit(10)
    try:
        with pytest.raises(CorruptFileError):
            CsvHandler().open(b"a,b\n" + b"x" * 50 + b",1\n")
    finally:
        _csv.field_size_limit(131072)


def test_unreadable_path(tmp_path: Path) -> None:
    with pytest.raises(CorruptFileError):
        CsvHandler().open(tmp_path)


@pytest.mark.parametrize("key", ["csv", "csv-bom", "csv-big5"])
def test_roundtrip_preserves_bytes(sample: Sample, key: str, tmp_path: Path) -> None:
    src = sample(key)
    doc = CsvHandler().open(src)
    out = doc.save(tmp_path / "out.csv")
    assert out.read_bytes() == src.read_bytes()
    again = CsvHandler().open(out)
    assert again.rows == doc.rows
    assert again.header == doc.header


def test_save_convert_encoding_and_delimiter(sample: Sample, tmp_path: Path) -> None:
    doc = CsvHandler().open(sample("csv"))
    out = doc.save(tmp_path / "x.tsv", encoding="big5", delimiter="\t")
    again = TsvHandler().open(out)
    assert again.encoding == "big5"
    assert again.delimiter == "\t"
    assert again.rows == doc.rows


def test_save_in_place_and_without_path(tmp_path: Path) -> None:
    p = tmp_path / "e.csv"
    p.write_text("a,b\n1,2\n", encoding="utf-8")
    doc = CsvHandler().open(p)
    doc.rows.append(["3", "4"])
    doc.save()
    assert p.read_text(encoding="utf-8") == "a,b\n1,2\n3,4\n"
    with pytest.raises(ValueError):
        CsvHandler().open(b"a,b\n1,2\n").save()


def test_create_and_write(tmp_path: Path) -> None:
    doc = CsvHandler().create()
    doc.has_header = True
    doc.header = ["名稱", "值"]
    doc.rows.append(["甲", "1"])
    out = doc.save(tmp_path / "new.csv")
    assert CsvHandler().open(out).rows == [["甲", "1"]]
    doc.close()


def test_sniff_and_capabilities(tmp_path: Path) -> None:
    assert CsvHandler.sniff(b"", Path("x.CSV")) == 0.9
    assert CsvHandler.sniff(b"", Path("x.txt")) == 0.0
    assert CsvHandler.sniff(b"", None) == 0.0
    assert Capability.WRITE in CsvHandler.capabilities


def test_anydoc_open_integration(sample: Sample) -> None:
    for key, fmt in (("csv", "csv"), ("csv-bom", "csv"), ("csv-big5", "csv")):
        doc = anydoc.open(sample(key))
        assert doc.format == fmt
        assert doc.text().startswith("姓名")


def test_entry_points_load() -> None:
    registry.unregister("csv")
    registry.unregister("tsv")
    loaded = registry.load_entry_points()
    assert {"csv", "tsv"} <= set(loaded)
