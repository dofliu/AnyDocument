"""Tests for XLSX creation and editing (T060)."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest
from openpyxl import load_workbook

import anydoc
from anydoc.core.model import Capability
from anydoc.core.registry import registry
from anydoc.formats.xlsx import XlsxDocument, XlsxHandler


@pytest.fixture(autouse=True)
def _handler() -> None:
    registry.register(XlsxHandler, replace=True)


def _new() -> XlsxDocument:
    return XlsxHandler().create()


def _roundtrip(doc: XlsxDocument) -> XlsxDocument:
    return XlsxHandler().open(doc.to_bytes())


def test_capabilities_and_create() -> None:
    assert {Capability.WRITE, Capability.EDIT} <= XlsxHandler.capabilities
    doc = _new()
    assert [s.name for s in doc.sheets()] == ["Sheet"]
    assert doc.rows() == []


def test_set_cell_types_and_roundtrip(tmp_path: Path) -> None:
    doc = _new()
    doc.set_cell(0, "A1", "名稱")
    doc.set_cell(0, "b1", 3.5)
    doc.set_cell(0, "C1", True)
    doc.set_cell(0, "D1", date(2026, 1, 2))
    doc.set_cell(0, "E1", "=B1*2")
    assert doc.rows()[0][:4] == ["名稱", 3.5, True, __import__("datetime").datetime(2026, 1, 2)]
    out = doc.save(tmp_path / "a.xlsx")
    wb = load_workbook(out)
    assert wb.active["E1"].value == "=B1*2"
    again = anydoc.open(out)
    kinds = {c.coordinate: c.type for c in again.cells()}  # type: ignore[attr-defined]
    assert kinds["A1"] == "string" and kinds["E1"] == "formula" and kinds["C1"] == "boolean"


def test_set_cell_clear_and_invalid_ref() -> None:
    doc = _new()
    doc.set_cell(0, "A1", 1)
    doc.set_cell(0, "A1", None)
    assert doc.cells() == []
    with pytest.raises(ValueError):
        doc.set_cell(0, "ZZZZ9", 1)
    with pytest.raises(KeyError):
        doc.set_cell("nope", "A1", 1)


def test_set_range_and_clear_range() -> None:
    doc = _new()
    rng = doc.set_range(0, "B2", [[1, 2, 3], [4, 5]])
    assert rng == "B2:D3"
    assert doc.rows()[1:] == [[None, 1, 2, 3], [None, 4, 5, None]]
    assert doc.set_range(0, "A10", []) == "A10:A10"
    assert doc.clear_range(0, "B2:C3") == 4
    assert doc.clear_range(0, "D2") == 1
    assert doc.cells() == []


def test_sheet_management() -> None:
    doc = _new()
    doc.set_cell(0, "A1", 1)
    assert doc.add_sheet("Data") == 1
    assert doc.add_sheet("First", 0) == 0
    assert [s.name for s in doc.sheets()] == ["First", "Sheet", "Data"]
    with pytest.raises(ValueError):
        doc.add_sheet("data")  # case-insensitive duplicate
    for bad in ("", "a/b", "x" * 32, "[x]"):
        with pytest.raises(ValueError):
            doc.add_sheet(bad)
    dup = doc.copy_sheet("Sheet", "Sheet copy")
    assert dup == 2
    assert doc.rows("Sheet copy") == [[1]]
    doc.move_sheet("Sheet copy", 0)
    assert doc.sheets()[0].name == "Sheet copy"
    with pytest.raises(IndexError):
        doc.move_sheet(0, 9)
    doc.delete_sheet("First")
    assert [s.name for s in doc.sheets()] == ["Sheet copy", "Sheet", "Data"]
    assert doc.metadata.extra["sheet_names"] == ["Sheet copy", "Sheet", "Data"]
    assert [p.name for p in _roundtrip(doc).parts()] == ["Sheet copy", "Sheet", "Data"]


def test_cannot_delete_last_sheet() -> None:
    doc = _new()
    with pytest.raises(ValueError):
        doc.delete_sheet(0)


def test_rename_updates_formula_references() -> None:
    doc = _new()
    doc.add_sheet("Calc")
    doc.set_cell("Calc", "A1", "=Sheet!A1+1")
    doc.set_cell("Calc", "A2", "='Sheet'!A1")
    doc.rename_sheet("Sheet", "My Data")
    formulas = {c.coordinate: c.formula for c in doc.cells("Calc")}
    assert formulas == {"A1": "='My Data'!A1+1", "A2": "='My Data'!A1"}
    doc.rename_sheet("My Data", "My Data")  # no-op
    with pytest.raises(ValueError):
        doc.rename_sheet("Calc", "my data")


def test_insert_and_delete_rows_cols_with_merges() -> None:
    doc = _new()
    doc.set_range(0, "A1", [[1, 2, 3], [4, 5, 6], [7, 8, 9]])
    doc.native.active.merge_cells("A3:B3")
    doc.insert_rows(0, 2, 2)
    assert doc.rows()[0] == [1, 2, 3]
    assert doc.rows()[1] == [None, None, None]
    assert doc.rows()[3] == [4, 5, 6]
    assert doc.merged_cells() == ["A5:B5"]
    doc.delete_rows(0, 2, 2)
    assert doc.rows() == [[1, 2, 3], [4, 5, 6], [7, None, 9]]  # merge cleared B3
    assert doc.merged_cells() == ["A3:B3"]
    doc.insert_cols(0, 1)
    assert doc.rows()[0] == [None, 1, 2, 3]
    assert doc.merged_cells() == ["B3:C3"]
    doc.delete_cols(0, 1)
    doc.delete_cols(0, 1)
    assert doc.rows()[0] == [2, 3]
    assert doc.merged_cells() == []  # merge shrank to one cell and was dropped
    doc.delete_rows(0, 1)
    assert doc.rows()[0] == [5, 6]


def test_merge_partially_deleted_and_removed() -> None:
    doc = _new()
    doc.set_range(0, "A1", [[1], [2], [3], [4], [5]])
    doc.native.active.merge_cells("A2:A4")
    doc.delete_rows(0, 3, 2)  # removes rows 3-4 -> merge becomes A2:A2 -> dropped
    assert doc.merged_cells() == []
    doc.native.active.merge_cells("A1:A2")
    doc.delete_rows(0, 1)
    assert doc.merged_cells() == []
    doc2 = _new()
    doc2.native.active.merge_cells("A2:A3")
    doc2.delete_rows(0, 2, 2)
    assert doc2.merged_cells() == []


def test_invalid_positions() -> None:
    doc = _new()
    with pytest.raises(ValueError):
        doc.insert_rows(0, 0)
    with pytest.raises(ValueError):
        doc.delete_cols(0, 1, 0)


def test_edit_existing_file_preserves_content(sample: object) -> None:
    sample_fn = sample  # fixture factory
    path = sample_fn("xlsx")  # type: ignore[operator]
    doc = anydoc.open(path)
    assert isinstance(doc, XlsxDocument)
    before = doc.rows()
    doc.set_cell(0, "Z1", "新增")
    assert doc.rows()[0][-1] == "新增" or doc.cells()[-1].value == "新增"
    out = doc.save(path.with_name("edited.xlsx"))
    again = anydoc.open(out)
    assert again.rows()[: len(before)] != []  # type: ignore[attr-defined]
    assert any(c.value == "新增" for c in again.cells())  # type: ignore[attr-defined]
