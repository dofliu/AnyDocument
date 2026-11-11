"""Tests for PPTX tables and native charts (T072)."""

from __future__ import annotations

from pathlib import Path

import pytest

from anydoc.core.errors import AnyDocError
from anydoc.core.registry import registry
from anydoc.external import convert_with_libreoffice
from anydoc.formats.pptx import (
    ChartInfo,
    PptxDocument,
    PptxHandler,
    ShapeInfo,
    TableInfo,
)
from anydoc.formats.pptx.charts import CHART_KINDS

CM = 360000
BOX = {"left": "2cm", "top": "2cm", "width": "20cm"}
CHART_BOX = {**BOX, "height": "10cm"}
DATA = [["產品", "Q1", "Q2"], ["甲", 10, 20.5], ["乙", 30, None]]
CATS = ["一月", "二月", "三月"]
SERIES = {"營收": [10, 20, 30], "成本": [5, 8.5, None]}


@pytest.fixture(autouse=True)
def _handler() -> None:
    registry.register(PptxHandler, replace=True)


def _doc() -> PptxDocument:
    doc = PptxHandler().create()
    doc.add_slide("Blank")
    return doc


def _reopen(doc: PptxDocument) -> PptxDocument:
    return PptxHandler().open(doc.to_bytes())


def _shape(doc: PptxDocument, shape_id: int) -> ShapeInfo:
    return next(s for s in doc.slide(0).shapes if s.shape_id == shape_id)


def _table(doc: PptxDocument) -> int:
    return doc.add_table(0, DATA, **BOX)


# -- tables: creation ------------------------------------------------------------


def test_add_table_from_data_roundtrip() -> None:
    doc = _doc()
    tid = _table(doc)
    again = _reopen(doc)
    info = again.table_info(0, tid)
    assert isinstance(info, TableInfo)
    assert (info.rows, info.cols) == (3, 3)
    assert info.data == [["產品", "Q1", "Q2"], ["甲", "10", "20.5"], ["乙", "30", ""]]
    assert info.header is True and info.banded is True and info.first_col is False
    assert info.merges == []
    assert sum(info.col_widths) == 20 * CM
    shape = _shape(again, tid)
    assert shape.kind == "table"
    assert shape.table == info.data
    assert (shape.left, shape.top, shape.width) == (2 * CM, 2 * CM, 20 * CM)
    assert shape.height == sum(info.row_heights)
    assert "甲\t10\t20.5" in again.slide_text(0)


def test_add_empty_table_with_options() -> None:
    doc = _doc()
    tid = doc.add_table(
        0,
        rows=2,
        cols=3,
        left=0,
        top=0,
        width="12cm",
        height="4cm",
        header=False,
        banded=False,
        first_col=True,
        col_widths=["2cm", "4cm", "6cm"],
        size=14,
        font="Noto Sans CJK TC",
        align="center",
    )
    info = _reopen(doc).table_info(0, tid)
    assert info.data == [["", "", ""], ["", "", ""]]
    assert info.col_widths == [2 * CM, 4 * CM, 6 * CM]
    assert info.row_heights == [2 * CM, 2 * CM]
    assert (info.header, info.banded, info.first_col) == (False, False, True)
    # formatting applied to empty cells is used by text written later
    doc.set_table_cell(0, tid, 0, 0, "後填")
    fmt = _reopen(doc).table_cell_format(0, tid, 0, 0)
    assert fmt.paragraphs[0].align == "center"


def test_add_table_pads_short_rows_and_partial_data() -> None:
    doc = _doc()
    tid = doc.add_table(0, [["a"], ["b", "c"]], rows=3, **BOX)
    assert doc.table_info(0, tid).data == [["a", ""], ["b", "c"], ["", ""]]


def test_add_table_invalid_arguments() -> None:
    doc = _doc()
    with pytest.raises(ValueError):
        doc.add_table(0, **BOX)
    with pytest.raises(ValueError):
        doc.add_table(0, [], **BOX)
    with pytest.raises(ValueError):
        doc.add_table(0, DATA, rows=2, **BOX)
    with pytest.raises(ValueError):
        doc.add_table(0, DATA, cols=2, **BOX)
    with pytest.raises(ValueError):
        doc.add_table(0, DATA, col_widths=["1cm"], **BOX)
    with pytest.raises(ValueError):
        doc.add_table(0, DATA, left=0, top=0, width=0)
    with pytest.raises(ValueError):
        doc.add_table(0, DATA, align="diagonal", **BOX)
    with pytest.raises(IndexError):
        doc.add_table(5, DATA, **BOX)
    assert doc.slide(0).shapes == []


# -- tables: content -------------------------------------------------------------


def test_set_cell_keeps_run_format() -> None:
    doc = _doc()
    tid = _table(doc)
    doc.format_table_cells(0, tid, rows=1, cols=0, bold=True, color="FF0000", size=20)
    doc.set_table_cell(0, tid, 1, 0, "新文字")
    doc.set_table_cell(0, tid, 2, 2, 3.0)
    again = _reopen(doc)
    fmt = again.table_cell_format(0, tid, 1, 0)
    assert fmt.text == "新文字"
    run = fmt.paragraphs[0].runs[0]
    assert (run.bold, run.color, run.size) == (True, "FF0000", 20.0)
    assert again.table_info(0, tid).data[2][2] == "3"


def test_set_cell_errors() -> None:
    doc = _doc()
    tid = _table(doc)
    box = doc.add_textbox(0, "x", left=0, top=0, width="2cm", height="1cm")
    with pytest.raises(IndexError):
        doc.set_table_cell(0, tid, 3, 0, "x")
    with pytest.raises(IndexError):
        doc.set_table_cell(0, tid, 0, -1, "x")
    with pytest.raises(AnyDocError):
        doc.set_table_cell(0, box, 0, 0, "x")
    with pytest.raises(AnyDocError):
        doc.table_info(0, 9999)
    doc.merge_table_cells(0, tid, 0, 0, 0, 1)
    with pytest.raises(AnyDocError):
        doc.set_table_cell(0, tid, 0, 1, "x")


def test_set_table_data_block_and_resize() -> None:
    doc = _doc()
    tid = _table(doc)
    doc.set_table_data(0, tid, [[1, 2], [3, 4]], start_row=1, start_col=1)
    assert doc.table_info(0, tid).data == [
        ["產品", "Q1", "Q2"],
        ["甲", "1", "2"],
        ["乙", "3", "4"],
    ]
    with pytest.raises(ValueError):
        doc.set_table_data(0, tid, [["x"] * 4])
    with pytest.raises(IndexError):
        doc.set_table_data(0, tid, [["x"]], start_row=-1)
    doc.set_table_data(0, tid, [["丙", 5, 6, 7], ["丁", 8, 9, 10]], start_row=3, resize=True)
    info = _reopen(doc).table_info(0, tid)
    assert (info.rows, info.cols) == (5, 4)
    assert info.data[3] == ["丙", "5", "6", "7"]
    assert info.data[4] == ["丁", "8", "9", "10"]
    assert info.data[0] == ["產品", "Q1", "Q2", ""]


def test_set_table_data_skips_merged_cells() -> None:
    doc = _doc()
    tid = _table(doc)
    doc.merge_table_cells(0, tid, 1, 0, 1, 1)
    doc.set_table_data(0, tid, [["A", "B", "C"]], start_row=1)
    assert doc.table_info(0, tid).data[1] == ["A", "", "C"]


# -- tables: rows and columns ----------------------------------------------------


def test_insert_and_delete_rows() -> None:
    doc = _doc()
    tid = _table(doc)
    before = doc.table_info(0, tid)
    assert doc.insert_table_row(0, tid, values=["丙", 1, 2]) == 3
    assert doc.insert_table_row(0, tid, 1, ["插入"]) == 1
    assert doc.insert_table_row(0, tid, 0) == 0
    info = _reopen(doc).table_info(0, tid)
    assert info.data == [
        ["", "", ""],
        ["產品", "Q1", "Q2"],
        ["插入", "", ""],
        ["甲", "10", "20.5"],
        ["乙", "30", ""],
        ["丙", "1", "2"],
    ]
    assert len(info.row_heights) == 6
    assert _shape(doc, tid).height == sum(info.row_heights) > sum(before.row_heights)
    doc.delete_table_row(0, tid, 0)
    doc.delete_table_row(0, tid, 1)
    assert _reopen(doc).table_info(0, tid).data == [
        ["產品", "Q1", "Q2"],
        ["甲", "10", "20.5"],
        ["乙", "30", ""],
        ["丙", "1", "2"],
    ]
    assert _shape(doc, tid).height == sum(doc.table_info(0, tid).row_heights)


def test_insert_and_delete_columns() -> None:
    doc = _doc()
    tid = _table(doc)
    assert doc.insert_table_column(0, tid, values=["Q3", 7, 8], width="3cm") == 3
    assert doc.insert_table_column(0, tid, 1, ["代號"]) == 1
    assert doc.insert_table_column(0, tid, 0) == 0
    info = _reopen(doc).table_info(0, tid)
    assert info.cols == 6
    assert info.data[0] == ["", "產品", "代號", "Q1", "Q2", "Q3"]
    assert info.data[1] == ["", "甲", "", "10", "20.5", "7"]
    assert info.col_widths[-1] == 3 * CM
    assert info.col_widths[0] == info.col_widths[1]
    assert _shape(doc, tid).width == sum(info.col_widths)
    doc.delete_table_column(0, tid, 0)
    doc.delete_table_column(0, tid, 1)
    info = _reopen(doc).table_info(0, tid)
    assert info.data == [
        ["產品", "Q1", "Q2", "Q3"],
        ["甲", "10", "20.5", "7"],
        ["乙", "30", "", "8"],
    ]
    assert _shape(doc, tid).width == sum(info.col_widths)


def test_new_row_copies_cell_formatting_but_not_text() -> None:
    doc = _doc()
    tid = _table(doc)
    doc.format_table_cells(0, tid, rows=2, fill="FFEEAA", anchor="middle")
    doc.insert_table_row(0, tid)
    fmt = doc.table_cell_format(0, tid, 3, 0)
    assert (fmt.text, fmt.fill, fmt.anchor) == ("", "FFEEAA", "middle")


def test_row_column_errors() -> None:
    doc = _doc()
    tid = doc.add_table(0, [["a"]], **BOX)
    with pytest.raises(AnyDocError):
        doc.delete_table_row(0, tid, 0)
    with pytest.raises(AnyDocError):
        doc.delete_table_column(0, tid, 0)
    with pytest.raises(IndexError):
        doc.delete_table_row(0, tid, 1)
    with pytest.raises(IndexError):
        doc.delete_table_column(0, tid, -1)
    with pytest.raises(IndexError):
        doc.insert_table_row(0, tid, 2)
    with pytest.raises(IndexError):
        doc.insert_table_column(0, tid, 2)
    with pytest.raises(ValueError):
        doc.insert_table_row(0, tid, values=["a", "b"])
    with pytest.raises(ValueError):
        doc.insert_table_column(0, tid, values=["a", "b"])
    assert doc.table_info(0, tid).data == [["a"]]


def test_column_width_and_row_height() -> None:
    doc = _doc()
    tid = _table(doc)
    doc.set_table_column_width(0, tid, 0, "8cm")
    doc.set_table_row_height(0, tid, 1, "3cm")
    info = _reopen(doc).table_info(0, tid)
    assert info.col_widths[0] == 8 * CM
    assert info.row_heights[1] == 3 * CM
    shape = _shape(doc, tid)
    assert (shape.width, shape.height) == (sum(info.col_widths), sum(info.row_heights))
    with pytest.raises(IndexError):
        doc.set_table_column_width(0, tid, 3, "1cm")
    with pytest.raises(IndexError):
        doc.set_table_row_height(0, tid, 3, "1cm")
    with pytest.raises(ValueError):
        doc.set_table_column_width(0, tid, 0, 0)
    with pytest.raises(ValueError):
        doc.set_table_row_height(0, tid, 0, "-1cm")


# -- tables: merging -------------------------------------------------------------


def test_merge_and_unmerge_cells() -> None:
    doc = _doc()
    tid = doc.add_table(0, rows=4, cols=4, **BOX)
    doc.set_table_cell(0, tid, 0, 0, "合併標題")
    doc.merge_table_cells(0, tid, 0, 0, 0, 3)
    doc.merge_table_cells(0, tid, 3, 2, 1, 1)  # corners in any order
    again = _reopen(doc)
    info = again.table_info(0, tid)
    assert info.merges == [(0, 0, 0, 3), (1, 1, 3, 2)]
    assert info.data[0][0] == "合併標題"
    with pytest.raises(AnyDocError):
        again.merge_table_cells(0, tid, 0, 0, 1, 0)
    with pytest.raises(AnyDocError):
        again.unmerge_table_cells(0, tid, 2, 2)
    with pytest.raises(ValueError):
        again.merge_table_cells(0, tid, 3, 3, 3, 3)
    with pytest.raises(IndexError):
        again.merge_table_cells(0, tid, 0, 0, 9, 0)
    again.unmerge_table_cells(0, tid, 1, 1)
    assert _reopen(again).table_info(0, tid).merges == [(0, 0, 0, 3)]


def test_rows_and_columns_respect_merges() -> None:
    doc = _doc()
    tid = doc.add_table(0, rows=3, cols=3, **BOX)
    doc.merge_table_cells(0, tid, 0, 0, 1, 1)
    with pytest.raises(AnyDocError):
        doc.insert_table_row(0, tid, 1)
    with pytest.raises(AnyDocError):
        doc.insert_table_column(0, tid, 1)
    with pytest.raises(AnyDocError):
        doc.delete_table_row(0, tid, 0)
    with pytest.raises(AnyDocError):
        doc.delete_table_column(0, tid, 1)
    # outside the merged region everything still works and the merge survives
    assert doc.insert_table_row(0, tid, 2) == 2
    assert doc.insert_table_column(0, tid, 2) == 2
    doc.delete_table_row(0, tid, 3)
    doc.delete_table_column(0, tid, 3)
    info = _reopen(doc).table_info(0, tid)
    assert (info.rows, info.cols) == (3, 3)
    assert info.merges == [(0, 0, 1, 1)]


# -- tables: formatting ----------------------------------------------------------


def test_format_cells_selection_and_readback() -> None:
    doc = _doc()
    tid = _table(doc)
    doc.format_table_cells(
        0,
        tid,
        rows=0,
        bold=True,
        italic=True,
        underline=True,
        size=18,
        color="#FFFFFF",
        font="Noto Sans CJK TC",
        align="center",
        anchor="middle",
        fill="1F4E79",
    )
    doc.format_table_cells(0, tid, rows=[1, 2], cols=[1, 2], align="right")
    again = _reopen(doc)
    head = again.table_cell_format(0, tid, 0, 1)
    run = head.paragraphs[0].runs[0]
    assert (head.fill, head.anchor, head.paragraphs[0].align) == ("1F4E79", "middle", "center")
    assert (run.bold, run.italic, run.underline, run.size) == (True, True, True, 18.0)
    assert (run.color, run.font) == ("FFFFFF", "Noto Sans CJK TC")
    body = again.table_cell_format(0, tid, 1, 1)
    assert body.paragraphs[0].align == "right"
    assert (body.fill, body.paragraphs[0].runs[0].bold) == (None, None)
    assert again.table_cell_format(0, tid, 1, 0).paragraphs[0].align is None
    again.format_table_cells(0, tid, rows=0, fill="none")
    assert again.table_cell_format(0, tid, 0, 0).fill is None


def test_format_cells_errors_and_table_options() -> None:
    doc = _doc()
    tid = _table(doc)
    with pytest.raises(IndexError):
        doc.format_table_cells(0, tid, rows=3, bold=True)
    with pytest.raises(IndexError):
        doc.format_table_cells(0, tid, cols=[0, 7], bold=True)
    with pytest.raises(ValueError):
        doc.format_table_cells(0, tid, anchor="sideways")
    with pytest.raises(ValueError):
        doc.format_table_cells(0, tid, fill="red")
    with pytest.raises(ValueError):
        doc.format_table_cells(0, tid, align="diagonal")
    with pytest.raises(IndexError):
        doc.table_cell_format(0, tid, 0, 3)
    doc.set_table_options(0, tid, header=False, banded=False, first_col=True)
    info = _reopen(doc).table_info(0, tid)
    assert (info.header, info.banded, info.first_col) == (False, False, True)
    doc.set_table_options(0, tid)
    assert doc.table_info(0, tid).first_col is True


def test_table_supports_generic_shape_operations() -> None:
    doc = _doc()
    tid = _table(doc)
    doc.set_geometry(0, tid, left="5cm", top="6cm")
    shape = _shape(_reopen(doc), tid)
    assert (shape.left, shape.top) == (5 * CM, 6 * CM)
    doc.delete_shape(0, tid)
    assert _reopen(doc).slide(0).shapes == []


# -- charts ----------------------------------------------------------------------


@pytest.mark.parametrize("kind", [k for k in CHART_KINDS if k != "scatter"])
def test_add_every_category_chart_kind(kind: str) -> None:
    doc = _doc()
    series = {"營收": SERIES["營收"]} if kind == "pie" else SERIES
    cid = doc.add_chart(0, kind, CATS, series, title=f"{kind} 圖", **CHART_BOX)
    again = _reopen(doc)
    info = again.chart_info(0, cid)
    assert isinstance(info, ChartInfo)
    assert (info.kind, info.stacking, info.title) == (kind, "none", f"{kind} 圖")
    assert info.categories == CATS
    assert [s.name for s in info.series] == list(series)
    assert info.series[0].values == [10.0, 20.0, 30.0]
    if kind != "pie":
        assert info.series[1].values == [5.0, 8.5, None]
    assert info.series[0].x_values is None
    assert info.legend == "right" and info.data_labels is False
    shape = _shape(again, cid)
    assert shape.kind == "chart" and shape.text == f"{kind} 圖"
    assert (shape.left, shape.width, shape.height) == (2 * CM, 20 * CM, 10 * CM)


@pytest.mark.parametrize("kind", ["column", "bar", "line", "area"])
@pytest.mark.parametrize("stacking", ["stacked", "percent"])
def test_stacked_charts(kind: str, stacking: str) -> None:
    doc = _doc()
    cid = doc.add_chart(0, kind, CATS, SERIES, stacking=stacking, **CHART_BOX)
    info = _reopen(doc).chart_info(0, cid)
    assert (info.kind, info.stacking) == (kind, stacking)


def test_scatter_chart() -> None:
    doc = _doc()
    cid = doc.add_chart(
        0,
        "scatter",
        None,
        [("量測", [(1, 2.5), (2, 4), (3.5, 9)]), ("對照", [(0, 0), (10, 10)])],
        x_title="時間",
        y_title="數值",
        **CHART_BOX,
    )
    info = _reopen(doc).chart_info(0, cid)
    assert info.kind == "scatter" and info.categories == []
    assert info.series[0].x_values == [1.0, 2.0, 3.5]
    assert info.series[0].values == [2.5, 4.0, 9.0]
    assert info.series[1].x_values == [0.0, 10.0]
    assert (info.x_title, info.y_title) == ("時間", "數值")


def test_chart_titles_legend_labels_and_colors() -> None:
    doc = _doc()
    cid = doc.add_chart(
        0,
        "column",
        CATS,
        SERIES,
        x_title="月份",
        y_title="金額",
        legend="bottom",
        data_labels=True,
        number_format="0.0",
        colors=["FF0000", "00AA00"],
        **CHART_BOX,
    )
    again = _reopen(doc)
    info = again.chart_info(0, cid)
    assert info.title is None
    assert (info.x_title, info.y_title, info.legend, info.data_labels) == (
        "月份",
        "金額",
        "bottom",
        True,
    )
    chart = next(s for s in again.native.slides[0].shapes if s.shape_id == cid).chart
    plot = chart.plots[0]
    assert plot.data_labels.number_format == "0.0"
    assert [str(s.format.fill.fore_color.rgb) for s in plot.series] == ["FF0000", "00AA00"]

    again.set_chart_options(
        0, cid, title="年度報告", x_title="", legend="none", data_labels=False, colors=["0000FF"]
    )
    info = _reopen(again).chart_info(0, cid)
    assert (info.title, info.x_title, info.y_title) == ("年度報告", None, "金額")
    assert info.legend is None and info.data_labels is False
    again.set_chart_options(0, cid, title="", legend="top")
    info = again.chart_info(0, cid)
    assert info.title is None and info.legend == "top"


@pytest.mark.parametrize("kind", ["pie", "doughnut", "line", "scatter"])
def test_series_colors_for_other_kinds(kind: str) -> None:
    doc = _doc()
    series: object = [("S", [(1, 1), (2, 2), (3, 3)])] if kind == "scatter" else {"S": [1, 2, 3]}
    cid = doc.add_chart(
        0,
        kind,
        None if kind == "scatter" else CATS,
        series,  # type: ignore[arg-type]
        colors=["112233", "445566", "778899", "ABCDEF"],
        legend=None,
        **CHART_BOX,
    )
    again = _reopen(doc)
    assert again.chart_info(0, cid).legend is None
    chart = next(s for s in again.native.slides[0].shapes if s.shape_id == cid).chart
    first = chart.plots[0].series[0]
    if kind in ("pie", "doughnut"):
        got = [str(first.points[i].format.fill.fore_color.rgb) for i in range(3)]
        assert got == ["112233", "445566", "778899"]
    elif kind == "line":
        assert str(first.format.line.color.rgb) == "112233"
    else:
        assert str(first.marker.format.fill.fore_color.rgb) == "112233"


def test_pie_chart_has_no_axis_titles() -> None:
    doc = _doc()
    with pytest.raises(AnyDocError):
        doc.add_chart(0, "pie", CATS, {"占比": [1, 2, 3]}, x_title="x", **CHART_BOX)
    cid = doc.add_chart(0, "pie", CATS, {"占比": [1, 2, 3]}, data_labels=True, **CHART_BOX)
    info = doc.chart_info(0, cid)
    assert (info.x_title, info.y_title, info.data_labels) == (None, None, True)


def test_update_chart_data() -> None:
    doc = _doc()
    cid = doc.add_chart(0, "line", CATS, SERIES, title="趨勢", legend="top", **CHART_BOX)
    doc.update_chart_data(0, cid, ["A", "B"], {"新系列": [1.5, 2.5]})
    info = _reopen(doc).chart_info(0, cid)
    assert info.categories == ["A", "B"]
    assert [(s.name, s.values) for s in info.series] == [("新系列", [1.5, 2.5])]
    assert (info.kind, info.title, info.legend) == ("line", "趨勢", "top")

    sid = doc.add_chart(0, "scatter", None, {"S": [(1, 1)]}, **CHART_BOX)
    doc.update_chart_data(0, sid, None, {"S2": [(2, 3), (4, 5)]})
    info = _reopen(doc).chart_info(0, sid)
    assert info.series[0].name == "S2"
    assert (info.series[0].x_values, info.series[0].values) == ([2.0, 4.0], [3.0, 5.0])
    with pytest.raises(ValueError):
        doc.update_chart_data(0, cid, ["A"], {"S": [1, 2]})


def test_chart_invalid_arguments() -> None:
    doc = _doc()
    bad: list[dict[str, object]] = [
        {"kind": "bubble3d"},
        {"stacking": "sideways"},
        {"kind": "pie", "stacking": "stacked"},
        {"kind": "pie"},  # two series
        {"legend": "middle"},
        {"categories": None},
        {"categories": []},
        {"series": {}},
        {"series": {"S": [1, 2]}},
        {"series": {"S": [1, "2", 3]}},
        {"series": {"S": [1, True, 3]}},
        {"colors": ["red"]},
        {"width": 0},
        {"kind": "scatter", "series": {"S": [1, 2, 3]}},
        {"kind": "scatter", "series": {"S": [(1, None)]}},
        {"kind": "scatter", "series": {"S": ["ab"]}},
        {"kind": "scatter", "series": {"S": [(1, 2)]}, "data_labels": True},
    ]
    for override in bad:
        args: dict[str, object] = {
            "kind": "column",
            "categories": CATS,
            "series": SERIES,
            **CHART_BOX,
            **override,
        }
        kind, categories, series = args.pop("kind"), args.pop("categories"), args.pop("series")
        with pytest.raises(ValueError):
            doc.add_chart(0, kind, categories, series, **args)  # type: ignore[arg-type]
    with pytest.raises(IndexError):
        doc.add_chart(3, "column", CATS, SERIES, **CHART_BOX)
    assert doc.slide(0).shapes == []

    cid = doc.add_chart(0, "column", CATS, SERIES, **CHART_BOX)
    tid = _table(doc)
    with pytest.raises(ValueError):
        doc.set_chart_options(0, cid, legend="middle")
    with pytest.raises(AnyDocError):
        doc.chart_info(0, tid)
    sid = doc.add_chart(0, "scatter", None, {"S": [(1, 2)]}, **CHART_BOX)
    with pytest.raises(AnyDocError):
        doc.set_chart_options(0, sid, number_format="0.0")
    doc.set_chart_options(0, sid, data_labels=False)
    assert doc.chart_info(0, sid).data_labels is False
    with pytest.raises(AnyDocError):
        doc.table_info(0, cid)
    with pytest.raises(AnyDocError):
        doc.update_chart_data(0, 9999, CATS, SERIES)


def test_chart_info_for_existing_chart_variants() -> None:
    """Charts made elsewhere (plain line, scatter with lines, bubble) are still classified."""
    from pptx.chart.data import BubbleChartData, CategoryChartData, XyChartData
    from pptx.enum.chart import XL_CHART_TYPE

    doc = _doc()
    shapes = doc.native.slides[0].shapes
    data = CategoryChartData()
    data.categories = CATS
    data.add_series("S", (1, 2, 3))
    xy = XyChartData()
    xy.add_series("P").add_data_point(1, 2)
    bubble = BubbleChartData()
    bubble.add_series("B").add_data_point(1, 2, 3)
    made = [
        (XL_CHART_TYPE.LINE, data, ("line", "none")),
        (XL_CHART_TYPE.LINE_STACKED, data, ("line", "stacked")),
        (XL_CHART_TYPE.LINE_STACKED_100, data, ("line", "percent")),
        (XL_CHART_TYPE.RADAR_FILLED, data, ("radar", "none")),
        (XL_CHART_TYPE.XY_SCATTER_LINES, xy, ("scatter", "none")),
        (XL_CHART_TYPE.PIE_EXPLODED, data, ("pie", "none")),
        (XL_CHART_TYPE.BUBBLE, bubble, ("bubble", "none")),
    ]
    ids = [shapes.add_chart(t, 0, 0, 5 * CM, 5 * CM, d).shape_id for t, d, _ in made]
    again = _reopen(doc)
    for shape_id, (_, _, expected) in zip(ids, made, strict=True):
        info = again.chart_info(0, shape_id)
        assert (info.kind, info.stacking) == expected
        assert info.series[0].values


# -- persistence -----------------------------------------------------------------


def test_edit_existing_file_and_save(tmp_path: Path) -> None:
    src = tmp_path / "deck.pptx"
    doc = _doc()
    tid = _table(doc)
    cid = doc.add_chart(0, "bar", CATS, SERIES, title="原始", **CHART_BOX)
    doc.save(src)

    opened = PptxHandler().open(src)
    opened.set_table_cell(0, tid, 1, 1, "99")
    opened.insert_table_row(0, tid, values=["丙", 7, 8])
    opened.update_chart_data(0, cid, CATS, {"營收": [1, 2, 3]})
    opened.set_chart_options(0, cid, title="更新後")
    out = opened.save(tmp_path / "out.pptx")

    final = PptxHandler().open(out)
    assert final.table_info(0, tid).data[1][1] == "99"
    assert final.table_info(0, tid).data[3] == ["丙", "7", "8"]
    info = final.chart_info(0, cid)
    assert (info.title, info.kind) == ("更新後", "bar")
    assert [s.values for s in info.series] == [[1.0, 2.0, 3.0]]
    assert "更新後" in final.text()


@pytest.mark.requires("libreoffice")
def test_libreoffice_roundtrip_keeps_table_and_chart(tmp_path: Path) -> None:
    doc = _doc()
    tid = _table(doc)
    doc.merge_table_cells(0, tid, 2, 1, 2, 2)
    doc.format_table_cells(0, tid, rows=0, fill="1F4E79", color="FFFFFF", bold=True)
    doc.add_slide("Blank")
    doc.add_chart(1, "column", CATS, SERIES, title="往返圖表", **CHART_BOX)
    doc.add_chart(
        1, "pie", CATS, {"占比": [1, 2, 3]}, left="2cm", top="12cm", width="8cm", height="6cm"
    )
    src = tmp_path / "tc.pptx"
    src.write_bytes(doc.to_bytes())

    pdf = convert_with_libreoffice(src, "pdf", tmp_path / "pdf")
    assert pdf.read_bytes().startswith(b"%PDF")

    back = PptxHandler().open(convert_with_libreoffice(src, "pptx", tmp_path / "pptx"))
    tables = [s for s in back.slide(0).shapes if s.kind == "table"]
    assert len(tables) == 1
    assert tables[0].table is not None
    assert tables[0].table[0] == ["產品", "Q1", "Q2"]
    assert tables[0].table[1] == ["甲", "10", "20.5"]
    assert back.table_info(0, tables[0].shape_id).merges == [(2, 1, 2, 2)]
    charts = [s for s in back.slide(1).shapes if s.kind == "chart"]
    assert len(charts) == 2
    infos = sorted((back.chart_info(1, c.shape_id) for c in charts), key=lambda i: i.kind)
    assert [i.kind for i in infos] == ["column", "pie"]
    assert infos[0].categories == CATS
    assert infos[0].series[0].values == [10.0, 20.0, 30.0]
    assert infos[0].title == "往返圖表"
