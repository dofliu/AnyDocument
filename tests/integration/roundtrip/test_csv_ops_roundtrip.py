"""Round-trip tests for CSV data operations (T080): open -> operate -> save -> reopen."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest

import anydoc
from anydoc.converters import csv_to_xlsx
from anydoc.formats.csv import CsvDocument, CsvHandler
from anydoc.formats.xlsx import XlsxHandler


def _open(path: Path) -> CsvDocument:
    doc = anydoc.open(path)
    assert isinstance(doc, CsvDocument)
    return doc


@pytest.mark.parametrize("fmt", ["csv", "csv-bom", "csv-big5"])
def test_pipeline_preserves_encoding(
    sample: Callable[[str], Path], tmp_path: Path, fmt: str
) -> None:
    doc = _open(sample(fmt))
    encoding, has_bom = doc.encoding, doc.has_bom
    assert doc.filter_rows("分數>=80") == 1
    doc.sort_rows("分數")
    doc.rename_columns({"分數": "成績"})
    doc.select_columns(["成績", "姓名"])
    out = doc.save(tmp_path / "out.csv")

    again = _open(out)
    assert (again.encoding, again.has_bom) == (encoding, has_bom)
    assert again.header == ["成績", "姓名"]
    assert again.rows == [["85", "陳美玲"], ["90", "王小明"]]
    assert again.records() == [{"成績": 85, "姓名": "陳美玲"}, {"成績": 90, "姓名": "王小明"}]


def test_join_and_group_roundtrip(sample: Callable[[str], Path], tmp_path: Path) -> None:
    people = _open(sample("csv-big5"))
    lookup = tmp_path / "lookup.csv"
    lookup.write_text("部門,樓層\n研發部,3\n行銷部,5\n", encoding="utf-8")
    joined = people.join(_open(lookup), "部門", how="left")
    joined.save(tmp_path / "joined.csv")

    again = _open(tmp_path / "joined.csv")
    assert again.encoding == "big5"
    assert again.header == ["姓名", "部門", "分數", "樓層"]
    assert again.rows[2] == ["林志豪", "財務部", "78", ""]

    summary = again.group_by("樓層", ["count", "mean:分數"], sort=False)
    summary.save(tmp_path / "summary.csv")
    reread = _open(tmp_path / "summary.csv")
    assert reread.header == ["樓層", "count", "mean_分數"]
    assert reread.rows == [["3", "1", "90"], ["5", "1", "85"], ["", "1", "78"]]


def test_untouched_cells_are_byte_identical(tmp_path: Path) -> None:
    raw = '代號,說明,金額\n007,"含,逗號",1.50\n010,"多行\n文字",2.00\n003,普通,10\n'
    src = tmp_path / "raw.csv"
    src.write_text(raw, encoding="utf-8")
    doc = _open(src)
    doc.sort_rows("代號")
    doc.dedupe()
    out = doc.save(tmp_path / "sorted.csv")
    assert out.read_text(encoding="utf-8") == (
        '代號,說明,金額\n003,普通,10\n007,"含,逗號",1.50\n010,"多行\n文字",2.00\n'
    )


def test_operated_table_converts_to_xlsx(sample: Callable[[str], Path], tmp_path: Path) -> None:
    doc = _open(sample("csv"))
    summary = doc.group_by([], ["人數=count", "平均=mean:分數", "最高=max:分數"])
    src = summary.save(tmp_path / "summary.csv")
    out = csv_to_xlsx(src, tmp_path / "summary.xlsx")
    book = XlsxHandler().open(out)
    try:
        assert "人數" in book.text() and "90" in book.text()
    finally:
        book.close()
    assert CsvHandler().open(src).rows == [["3", "84.3333333333", "90"]]
