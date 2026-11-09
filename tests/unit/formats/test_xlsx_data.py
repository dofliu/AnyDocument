"""Tests for XLSX sort / filter / remove duplicates (T063)."""

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
            ["品名", "類別", "數量"],
            ["蘋果", "水果", 3],
            ["香蕉", "水果", 10],
            ["菠菜", "蔬菜", 7],
            ["Apple", "水果", None],
            ["胡蘿蔔", "蔬菜", 1],
        ],
    )
    return doc


def _rt(doc: XlsxDocument) -> XlsxDocument:
    return XlsxHandler().open(doc.to_bytes())


def _col(doc: XlsxDocument, letter: str, lo: int, hi: int) -> list[object]:
    ws = doc.native.worksheets[0]
    return [ws[f"{letter}{r}"].value for r in range(lo, hi + 1)]


def test_sort_numeric_asc_desc_blank_last() -> None:
    doc = _doc()
    assert doc.sort_range(0, "A1:C6", [("C", True)], has_header=True) == 5
    assert _col(doc, "C", 2, 6) == [1, 3, 7, 10, None]
    assert _col(doc, "A", 1, 1) == ["品名"]
    doc.sort_range(0, "A2:C6", [("C", False)])
    assert _col(doc, "C", 2, 6) == [10, 7, 3, 1, None]
    assert _col(doc, "A", 2, 3) == ["香蕉", "菠菜"]  # rows moved together


def test_sort_multi_key_and_offset_keys() -> None:
    doc = _doc()
    doc.sort_range(0, "A1:C6", ["B", ("C", False)], has_header=True)
    assert _col(doc, "B", 2, 6) == ["水果"] * 3 + ["蔬菜"] * 2
    assert _col(doc, "C", 2, 4) == [10, 3, None]
    assert _col(doc, "C", 5, 6) == [7, 1]
    doc2 = _doc()
    doc2.sort_range(0, "A2:C6", [(2, True)])
    assert _col(doc2, "C", 2, 6) == [1, 3, 7, 10, None]


def test_sort_text_case_insensitive_and_mixed_types() -> None:
    doc = XlsxHandler().create()
    doc.set_range(0, "A1", [["b"], [True], [5], ["A"], [None], [1.5]])
    doc.sort_range(0, "A1:A6", ["A"])
    assert _col(doc, "A", 1, 6) == [1.5, 5, "A", "b", True, None]


def test_sort_moves_styles_and_survives_roundtrip() -> None:
    doc = _doc()
    doc.set_fill(0, "A3:C3", "#FFFF00")  # 香蕉 row
    doc.sort_range(0, "A2:C6", [("C", True)])
    r = _rt(doc)
    row = next(i for i in range(2, 7) if r.native.worksheets[0][f"A{i}"].value == "香蕉")
    assert r.cell_format(0, f"A{row}")["fill"] is not None
    assert r.cell_format(0, "A2")["fill"] is None


def test_sort_errors() -> None:
    doc = _doc()
    with pytest.raises(ValueError):
        doc.sort_range(0, "A1:C6", [])
    with pytest.raises(ValueError):
        doc.sort_range(0, "A1:C6", ["F"])
    with pytest.raises(ValueError):
        doc.sort_range(0, "bad", ["A"])
    with pytest.raises(ValueError):
        doc.sort_range(0, "A:C", ["A"])
    doc.native.worksheets[0].merge_cells("A2:B2")
    with pytest.raises(ValueError, match="merged"):
        doc.sort_range(0, "A1:C6", ["A"])
    assert _doc().sort_range(0, "A1:C1", ["A"], has_header=True) == 0


def test_remove_duplicates_all_columns_and_subset() -> None:
    doc = XlsxHandler().create()
    doc.set_range(
        0,
        "A1",
        [["k", "v"], ["a", 1], ["A", 1], ["b", 2], ["a", 1], ["a", 2], [None, 3], [None, 3]],
    )
    assert doc.remove_duplicates(0, "A1:B8") == 3  # A==a case-insensitively, blanks equal
    assert _col(doc, "A", 1, 5) == ["k", "a", "b", "a", None]
    assert _col(doc, "B", 6, 8) == [None, None, None]
    doc2 = XlsxHandler().create()
    doc2.set_range(0, "A1", [["a", 1], ["a", 2], ["b", 3]])
    assert doc2.remove_duplicates(0, "A1:B3", ["A"], has_header=False) == 1
    assert _col(doc2, "B", 1, 3) == [1, 3, None]


def test_remove_duplicates_case_sensitive_and_none() -> None:
    doc = XlsxHandler().create()
    doc.set_range(0, "A1", [["a"], ["A"], ["b"]])
    assert doc.remove_duplicates(0, "A1:A3", has_header=False, case_sensitive=True) == 0
    assert doc.remove_duplicates(0, "A1:A3", has_header=False) == 1
    assert doc.remove_duplicates(0, "A1:A1") == 0


def test_auto_filter_with_criteria_and_roundtrip() -> None:
    doc = _doc()
    hidden = doc.set_auto_filter(0, "A1:C6", {"B": ["水果"]})
    assert hidden == [4, 6]
    info = doc.auto_filter(0)
    assert info == {"ref": "A1:C6", "columns": {"B": ["水果"]}, "hidden_rows": [4, 6]}
    r = _rt(doc)
    assert r.auto_filter(0) == info
    assert r.layout(0)["hidden_rows"] == [4, 6]


def test_auto_filter_predicate_blank_and_reapply() -> None:
    doc = _doc()
    assert doc.set_auto_filter(0, "A1:C6", {2: lambda v: isinstance(v, int) and v > 5}) == [
        2,
        5,
        6,
    ]
    # re-applying resets earlier hidden rows
    assert doc.set_auto_filter(0, "A1:C6", {"C": [3, None]}) == [3, 4, 6]
    assert doc.set_auto_filter(0, "A1:C6") == []
    assert doc.auto_filter(0) == {"ref": "A1:C6", "columns": {}, "hidden_rows": []}


def test_clear_auto_filter() -> None:
    doc = _doc()
    doc.set_auto_filter(0, "A1:C6", {"B": ["蔬菜"]})
    assert doc.clear_auto_filter(0) == 3
    assert doc.auto_filter(0) is None
    assert doc.clear_auto_filter(0) == 0


def test_auto_filter_bad_column() -> None:
    with pytest.raises(ValueError):
        _doc().set_auto_filter(0, "A1:C6", {"Z": ["x"]})


def test_cells_view_after_edit() -> None:
    doc = _doc()
    doc.sort_range(0, "A1:C6", [("C", True)], has_header=True)
    assert doc.rows(0)[1][2] == 1


@pytest.mark.requires("libreoffice")
def test_autofilter_survives_libreoffice_roundtrip(tmp_path: Path) -> None:
    doc = _doc()
    doc.set_auto_filter(0, "A1:C6", {"B": ["水果"]})
    src = tmp_path / "in.xlsx"
    src.write_bytes(doc.to_bytes())
    out = convert_with_libreoffice(src, "xlsx", tmp_path / "o")
    wb = load_workbook(out)
    assert wb.worksheets[0].auto_filter.ref == "A1:C6"
