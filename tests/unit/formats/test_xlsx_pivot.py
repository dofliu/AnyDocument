"""Tests for XLSX pivot tables (T066)."""

from __future__ import annotations

from pathlib import Path

import pytest

from anydoc.core.registry import registry
from anydoc.external import convert_with_libreoffice
from anydoc.formats.xlsx import XlsxDocument, XlsxHandler


@pytest.fixture(autouse=True)
def _handler() -> None:
    registry.register(XlsxHandler, replace=True)


DATA = [
    ["區域", "產品", "數量", "金額"],
    ["北", "A", 1, 10],
    ["北", "B", 2, 20],
    ["南", "A", 3, 30],
    ["南", "A", 4, 40],
    ["南", "B", None, 50],
    ["東", "B", 5, 60],
]


def _doc() -> XlsxDocument:
    doc = XlsxHandler().create()
    doc.set_range(0, "A1", DATA)
    return doc


def _grid(doc: XlsxDocument, sheet: int) -> list[list[object]]:
    back = XlsxHandler().open(doc.to_bytes())
    return [list(r) for r in back.native.worksheets[sheet].iter_rows(values_only=True)]


def test_row_only_sum_with_total() -> None:
    doc = _doc()
    idx = doc.pivot_table(0, "A1:D7", "樞紐", rows=["區域"], values=["金額"])
    assert idx == 1
    g = _grid(doc, 1)
    assert g[0] == ["區域", "Sum of 金額"]
    assert g[1:] == [["北", 30], ["南", 120], ["東", 60], ["Grand Total", 210]]


def test_columns_and_totals() -> None:
    doc = _doc()
    doc.pivot_table(0, "A1:D7", "P", rows=["區域"], columns=["產品"], values=["金額"])
    g = _grid(doc, 1)
    assert g[0] == ["區域", "A", "B", "Grand Total"]
    assert g[1] == ["北", 10, 20, 30]
    assert g[2] == ["南", 70, 50, 120]
    assert g[3] == ["東", None, 60, 60]
    assert g[4] == ["Grand Total", 80, 130, 210]


def test_multiple_values_and_aggregates() -> None:
    doc = _doc()
    doc.pivot_table(
        0,
        "A1:D7",
        "P",
        rows=["區域"],
        values=[("數量", "count"), ("金額", "average"), ("金額", "max")],
    )
    g = _grid(doc, 1)
    assert g[0] == ["區域", "Count of 數量", "Average of 金額", "Max of 金額"]
    assert g[2] == ["南", 2, 40, 50]
    assert g[-1] == ["Grand Total", 5, 35, 60]


def test_median_min_and_no_totals() -> None:
    doc = _doc()
    doc.pivot_table(
        0, "A1:D7", "P", rows=["產品"], values=[("金額", "median"), ("金額", "min")], totals=False
    )
    g = _grid(doc, 1)
    assert g == [["產品", "Median of 金額", "Min of 金額"], ["A", 30, 10], ["B", 50, 20]]


def test_multi_row_fields_and_filters() -> None:
    doc = _doc()
    doc.pivot_table(
        0, "A1:D7", "P", rows=["區域", "產品"], values=["數量"], filters={"區域": ["北", "南"]}
    )
    g = _grid(doc, 1)
    assert g[0] == ["區域", "產品", "Sum of 數量"]
    assert g[1:] == [
        ["北", "A", 1],
        ["北", "B", 2],
        ["南", "A", 7],
        ["南", "B", 0],
        ["Grand Total", None, 10],
    ]


def test_multi_column_and_multi_value_headers() -> None:
    doc = _doc()
    doc.pivot_table(0, "A1:D7", "P", rows=["區域"], columns=["產品"], values=["數量", "金額"])
    g = _grid(doc, 1)
    assert g[0] == [
        "區域",
        "A / Sum of 數量",
        "A / Sum of 金額",
        "B / Sum of 數量",
        "B / Sum of 金額",
        "Grand Total / Sum of 數量",
        "Grand Total / Sum of 金額",
    ]


def test_columns_only() -> None:
    doc = _doc()
    doc.pivot_table(0, "A1:D7", "P", rows=[], columns=["產品"], values=["金額"])
    g = _grid(doc, 1)
    assert g == [["A", "B", "Grand Total"], [80, 130, 210]]


def test_blank_keys_and_source_sheet_untouched() -> None:
    doc = _doc()
    doc.set_cell(0, "A3", None)
    doc.pivot_table(0, "A1:D7", "P", rows=["區域"], values=["金額"])
    g = _grid(doc, 1)
    assert ["(blank)", 20] in g
    assert doc.native.worksheets[0]["A1"].value == "區域"


def test_formula_source_values_are_calculated() -> None:
    doc = _doc()
    doc.set_cell(0, "E1", "合計")
    for r in range(2, 8):
        doc.set_cell(0, f"E{r}", f"=D{r}*2")
    doc.pivot_table(0, "A1:E7", "P", rows=["區域"], values=["合計"], totals=False)
    g = _grid(doc, 1)
    assert g[1:] == [["北", 60], ["南", 240], ["東", 120]]


@pytest.mark.parametrize(
    "kwargs",
    [
        {"rows": ["沒有"], "values": ["金額"]},
        {"rows": ["區域"], "values": ["沒有"]},
        {"rows": ["區域"], "values": [("金額", "bogus")]},
        {"rows": ["區域"], "values": []},
        {"rows": [], "values": ["金額"]},
        {"rows": ["區域"], "values": ["金額"], "filters": {"沒有": 1}},
    ],
)
def test_errors(kwargs: dict[str, object]) -> None:
    doc = _doc()
    with pytest.raises(ValueError):
        doc.pivot_table(0, "A1:D7", "P", **kwargs)  # type: ignore[arg-type]
    assert len(doc.native.worksheets) == 1 or kwargs.get("rows") is None


def test_bad_source_and_duplicate_title() -> None:
    doc = _doc()
    with pytest.raises(ValueError):
        doc.pivot_table(0, "A1:D1", "P", rows=["區域"], values=["金額"])
    doc.set_cell(0, "B1", "區域")
    with pytest.raises(ValueError):
        doc.pivot_table(0, "A1:D7", "P", rows=["區域"], values=["金額"])


@pytest.mark.requires("libreoffice")
def test_libreoffice_roundtrip(tmp_path: Path) -> None:
    doc = _doc()
    doc.pivot_table(0, "A1:D7", "樞紐", rows=["區域"], columns=["產品"], values=["金額"])
    src = tmp_path / "p.xlsx"
    src.write_bytes(doc.to_bytes())
    out = convert_with_libreoffice(src, "xlsx", tmp_path / "o")
    back = XlsxHandler().open(out)
    assert back.native.worksheets[1].title == "樞紐"
    assert back.native.worksheets[1]["D5"].value == 210
