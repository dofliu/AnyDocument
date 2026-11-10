"""Tests for XLSX chart creation (T064)."""

from __future__ import annotations

from pathlib import Path

import pytest
from openpyxl import load_workbook

from anydoc.core.registry import registry
from anydoc.external import convert_with_libreoffice
from anydoc.formats.xlsx import XlsxDocument, XlsxHandler


@pytest.fixture(autouse=True)
def _handler() -> None:
    registry.register(XlsxHandler, replace=True)


def _doc() -> XlsxDocument:
    doc = XlsxHandler().create()
    doc.set_range(
        0,
        "A1",
        [
            ["月份", "營收", "成本"],
            ["一月", 10, 4],
            ["二月", 14, 6],
            ["三月", 9, 5],
            ["四月", 20, 8],
        ],
    )
    return doc


def _rt(doc: XlsxDocument) -> XlsxDocument:
    return XlsxHandler().open(doc.to_bytes())


@pytest.mark.parametrize(
    ("kind", "expected"),
    [("column", "column"), ("bar", "bar"), ("line", "line"), ("pie", "pie")],
)
def test_add_chart_kinds_roundtrip(kind: str, expected: str) -> None:
    doc = _doc()
    data = "B1:B5" if kind == "pie" else "B1:C5"
    idx = doc.add_chart(0, kind, data, categories="A2:A5", title="營收圖", anchor="E2")
    assert idx == 0
    info = _rt(doc).charts(0)
    assert len(info) == 1
    chart = info[0]
    assert chart["kind"] == expected
    assert chart["title"] == "營收圖"
    assert chart["anchor"] == "E2"
    assert len(chart["series"]) == (1 if kind == "pie" else 2)
    assert "$A$2:$A$5" in chart["series"][0]["categories"]
    assert "$B$2:$B$5" in chart["series"][0]["values"]


def test_series_names_from_header() -> None:
    doc = _doc()
    doc.add_chart(0, "line", "B1:C5", categories="A2:A5")
    series = _rt(doc).charts(0)[0]["series"]
    assert "B1" in series[0]["name"]
    assert "C1" in series[1]["name"]


def test_without_titles_from_data() -> None:
    doc = _doc()
    doc.add_chart(0, "column", "B2:C5", titles_from_data=False)
    series = _rt(doc).charts(0)[0]["series"]
    assert len(series) == 2
    assert "$B$2:$B$5" in series[0]["values"]


def test_series_in_rows() -> None:
    doc = _doc()
    doc.add_chart(0, "line", "A2:E3", series_in_rows=True, titles_from_data=True)
    series = _rt(doc).charts(0)[0]["series"]
    assert len(series) == 2


def test_stacked_column_and_axis_titles() -> None:
    doc = _doc()
    doc.add_chart(0, "column", "B1:C5", stacked=True, x_title="月", y_title="金額")
    chart = _rt(doc).charts(0)[0]
    assert chart["stacked"] is True
    assert chart["x_title"] == "月"
    assert chart["y_title"] == "金額"


def test_scatter_first_column_is_x() -> None:
    doc = _doc()
    doc.add_chart(0, "scatter", "B1:C5", title="散佈")
    chart = _rt(doc).charts(0)[0]
    assert chart["kind"] == "scatter"
    assert len(chart["series"]) == 1
    assert "$B$2:$B$5" in chart["series"][0]["categories"]
    assert "$C$2:$C$5" in chart["series"][0]["values"]


def test_scatter_with_explicit_x_and_rows() -> None:
    doc = _doc()
    doc.add_chart(0, "scatter", "B1:C5", categories="A2:A5")
    assert len(_rt(doc).charts(0)[0]["series"]) == 2
    doc2 = _doc()
    doc2.add_chart(0, "scatter", "A2:E3", series_in_rows=True, titles_from_data=False)
    assert len(_rt(doc2).charts(0)[0]["series"]) == 1


def test_legend_options_and_size() -> None:
    doc = _doc()
    doc.add_chart(0, "line", "B1:C5", legend=None, width=20, height=10)
    doc.add_chart(0, "line", "B1:C5", legend="b")
    native = doc.native.worksheets[0]._charts
    assert native[0].legend is None
    assert native[0].width == 20
    assert native[1].legend.position == "b"


def test_multiple_charts_and_remove() -> None:
    doc = _doc()
    assert doc.add_chart(0, "line", "B1:B5") == 0
    assert doc.add_chart(0, "pie", "C1:C5") == 1
    assert [c["kind"] for c in _rt(doc).charts(0)] == ["line", "pie"]
    assert doc.remove_charts(0, 0) == 1
    assert [c["kind"] for c in _rt(doc).charts(0)] == ["pie"]
    assert doc.remove_charts(0) == 1
    assert _rt(doc).charts(0) == []


def test_chart_on_other_sheet() -> None:
    doc = _doc()
    doc.add_sheet("圖表")
    doc.add_chart("圖表", "column", "B1:B5")
    assert _rt(doc).charts("圖表")[0]["kind"] == "column"
    assert _rt(doc).charts(0) == []


def test_errors() -> None:
    doc = _doc()
    with pytest.raises(ValueError, match="Unknown chart kind"):
        doc.add_chart(0, "radar", "B1:B5")
    with pytest.raises(ValueError, match="bounded|Invalid range"):
        doc.add_chart(0, "line", "zz")
    with pytest.raises(ValueError, match="no data"):
        doc.add_chart(0, "line", "B1:B1")
    with pytest.raises(ValueError, match="positive"):
        doc.add_chart(0, "line", "B1:B5", width=0)
    with pytest.raises(ValueError, match="series_in_rows"):
        doc.add_chart(0, "pie", "B1:C5", series_in_rows=True)
    with pytest.raises(ValueError, match="legend"):
        doc.add_chart(0, "line", "B1:B5", legend="x")
    with pytest.raises(ValueError, match="Y series"):
        doc.add_chart(0, "scatter", "B1:B5")
    with pytest.raises(ValueError, match="out of range"):
        doc.remove_charts(0, 3)


def test_chart_part_exists_in_package() -> None:
    import io
    import zipfile

    doc = _doc()
    doc.add_chart(0, "column", "B1:C5", categories="A2:A5")
    names = zipfile.ZipFile(io.BytesIO(doc.to_bytes())).namelist()
    assert any(n.startswith("xl/charts/chart") for n in names)
    wb = load_workbook(io.BytesIO(doc.to_bytes()))
    assert len(wb.worksheets[0]._charts) == 1


@pytest.mark.requires("libreoffice")
def test_chart_survives_libreoffice_roundtrip(tmp_path: Path) -> None:
    doc = _doc()
    doc.add_chart(0, "column", "B1:C5", categories="A2:A5", title="營收")
    src = tmp_path / "c.xlsx"
    src.write_bytes(doc.to_bytes())
    out = convert_with_libreoffice(src, "xlsx", tmp_path / "o")
    charts = XlsxHandler().open(out).charts(0)
    assert len(charts) == 1
    assert charts[0]["kind"] in ("column", "bar")
    assert len(charts[0]["series"]) == 2
