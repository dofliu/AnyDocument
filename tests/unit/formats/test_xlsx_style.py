"""Tests for XLSX formatting and layout (T062)."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest
from openpyxl import load_workbook

from anydoc.core.registry import registry
from anydoc.external import convert_with_libreoffice
from anydoc.formats.xlsx import XlsxDocument, XlsxHandler
from anydoc.formats.xlsx import style as st


@pytest.fixture(autouse=True)
def _handler() -> None:
    registry.register(XlsxHandler, replace=True)


def _doc() -> XlsxDocument:
    doc = XlsxHandler().create()
    doc.set_range(
        0, "A1", [["品名", "數量", "單價"], ["蘋果", 3, 12.5], ["香蕉", 5, 8], ["櫻桃", 2, 99]]
    )
    return doc


def _rt(doc: XlsxDocument) -> XlsxDocument:
    return XlsxHandler().open(doc.to_bytes())


def test_number_formats_roundtrip() -> None:
    doc = _doc()
    assert doc.set_number_format(0, "C2:C4", "currency") == 3
    doc.set_number_format(0, "B2", "percent")
    doc.set_number_format(0, "B3", "0.000")
    doc.set_cell(0, "D1", date(2026, 1, 2))
    doc.set_number_format(0, "D1", "date")
    r = _rt(doc)
    assert r.cell_format(0, "C3")["number_format"] == '"$"#,##0.00'
    assert r.cell_format(0, "B2")["number_format"] == "0%"
    assert r.cell_format(0, "B3")["number_format"] == "0.000"
    assert r.cell_format(0, "D1")["number_format"] == "yyyy-mm-dd"
    assert doc.cell_format(0, "A1")["number_format"] == "General"


def test_font_roundtrip_and_merge() -> None:
    doc = _doc()
    doc.set_font(0, "A1:C1", bold=True, size=14, color="#FF0000", name="Microsoft JhengHei")
    doc.set_font(0, "A1", italic=True, underline=True, strike=True)
    doc.set_font(0, "B1", underline=False)
    r = _rt(doc)
    a1 = r.cell_format(0, "A1")["font"]
    assert a1 == {
        "name": "Microsoft JhengHei",
        "size": 14,
        "bold": True,
        "italic": True,
        "underline": "single",
        "strike": True,
        "color": "FF0000",
    }
    b1 = r.cell_format(0, "B1")["font"]
    assert b1["bold"] and b1["italic"] is False and b1["underline"] is None
    assert r.cell_format(0, "A2")["font"]["bold"] is False


def test_fill_set_and_remove() -> None:
    doc = _doc()
    doc.set_fill(0, "A1:C1", "yellow")
    doc.set_fill(0, "A2", "#00ff00")
    r = _rt(doc)
    assert r.cell_format(0, "B1")["fill"] == "FFFF00"
    assert r.cell_format(0, "A2")["fill"] == "00FF00"
    doc.set_fill(0, "B1", None)
    assert doc.cell_format(0, "B1")["fill"] is None
    assert any(c.fill_color for c in r.cells(0))


def test_alignment() -> None:
    doc = _doc()
    doc.set_alignment(0, "A1:C1", horizontal="center", vertical="top", wrap=True, indent=1)
    doc.set_alignment(0, "A1", rotation=45)
    r = _rt(doc)
    a = r.cell_format(0, "A1")["alignment"]
    assert a == {
        "horizontal": "center",
        "vertical": "top",
        "wrap": True,
        "indent": 1,
        "rotation": 45,
        "shrink": False,
    }
    assert r.cell_format(0, "B1")["alignment"]["rotation"] == 0
    with pytest.raises(ValueError):
        doc.set_alignment(0, "A1", horizontal="diagonal")
    with pytest.raises(ValueError):
        doc.set_alignment(0, "A1", vertical="nope")
    with pytest.raises(ValueError):
        doc.set_alignment(0, "A1", rotation=120)
    with pytest.raises(ValueError):
        doc.set_alignment(0, "A1", indent=-1)


def test_borders_all_sides_outline_and_remove() -> None:
    doc = _doc()
    doc.set_border(0, "A1:C1", "thin")
    doc.set_border(0, "A2:C4", "medium", color="blue", outline=True)
    doc.set_border(0, "A1", "double", sides=("left",))
    r = _rt(doc)
    assert r.cell_format(0, "B1")["border"] == dict.fromkeys(
        ("left", "right", "top", "bottom"), "thin"
    )
    assert r.cell_format(0, "A1")["border"]["left"] == "double"
    assert r.cell_format(0, "A1")["border"]["top"] == "thin"
    # outline: only the perimeter is drawn
    assert r.cell_format(0, "B3")["border"] == dict.fromkeys(("left", "right", "top", "bottom"))
    corner = r.cell_format(0, "A2")["border"]
    assert corner["left"] == "medium" and corner["top"] == "medium" and corner["right"] is None
    assert r.cell_format(0, "C4")["border"]["bottom"] == "medium"
    wb = load_workbook(__import__("io").BytesIO(doc.to_bytes()))
    assert wb.active["A2"].border.left.color.rgb == "FF0000FF"
    doc.set_border(0, "A1:C1", None)
    assert doc.cell_format(0, "B1")["border"]["top"] is None
    with pytest.raises(ValueError):
        doc.set_border(0, "A1", "wavy")
    with pytest.raises(ValueError):
        doc.set_border(0, "A1", "thin", sides=("middle",))


def test_column_width_row_height_and_validation() -> None:
    doc = _doc()
    doc.set_column_width(0, "A", 20)
    doc.set_column_width(0, 2, 12.5)
    doc.set_row_height(0, 1, 30)
    r = _rt(doc)
    lay = r.layout(0)
    assert lay["column_widths"]["A"] == 20 and lay["column_widths"]["B"] == 12.5
    assert lay["row_heights"][1] == 30
    for bad in (lambda: doc.set_column_width(0, "A", 300), lambda: doc.set_row_height(0, 0, 10)):
        with pytest.raises(ValueError):
            bad()
    with pytest.raises(ValueError):
        doc.set_row_height(0, 1, 500)


def test_auto_fit_columns_counts_cjk_double() -> None:
    doc = _doc()
    doc.set_cell(0, "A5", "非常非常長的中文品名")
    widths = doc.auto_fit_columns(0)
    assert widths["A"] > widths["B"]
    assert widths["B"] == 8  # minimum width
    assert _rt(doc).layout(0)["column_widths"]["A"] == widths["A"]


def test_freeze_panes() -> None:
    doc = _doc()
    doc.freeze_panes(0, "B2")
    assert _rt(doc).layout(0)["freeze_panes"] == "B2"
    doc.freeze_panes(0, "A2")
    assert _rt(doc).layout(0)["freeze_panes"] == "A2"
    doc.freeze_panes(0, None)
    assert _rt(doc).layout(0)["freeze_panes"] is None
    doc.freeze_panes(0, "A1")
    assert doc.layout(0)["freeze_panes"] is None
    with pytest.raises(ValueError):
        doc.freeze_panes(0, "bogus")


def test_hide_and_unhide_rows_cols() -> None:
    doc = _doc()
    doc.set_hidden(0, rows=(2, 3), cols=("B", "C"))
    lay = _rt(doc).layout(0)
    assert lay["hidden_rows"] == [2, 3]
    assert lay["hidden_columns"] == ["B", "C"]
    doc.set_hidden(0, rows=(2, 2), hidden=False)
    doc.set_hidden(0, cols=("B", "B"), hidden=False)
    lay = _rt(doc).layout(0)
    assert lay["hidden_rows"] == [3] and lay["hidden_columns"] == ["C"]
    with pytest.raises(ValueError):
        doc.set_hidden(0, rows=(3, 1))
    with pytest.raises(ValueError):
        doc.set_hidden(0, cols=("D", "B"))


def test_hide_sheet_keeps_one_visible() -> None:
    doc = _doc()
    doc.add_sheet("B")
    doc.set_sheet_state("B", "hidden")
    r = _rt(doc)
    assert [s.state for s in r.sheets()] == ["visible", "hidden"]
    doc.set_sheet_state("B", "veryHidden")
    assert doc.sheets()[1].state == "veryHidden"
    with pytest.raises(ValueError):
        doc.set_sheet_state(0, "hidden")
    with pytest.raises(ValueError):
        doc.set_sheet_state("B", "gone")
    doc.set_sheet_state("B", "visible")
    doc.set_sheet_state(0, "hidden")  # fine now: B is visible


def test_range_forms_and_errors() -> None:
    doc = _doc()
    assert doc.set_fill(0, "B:B", "red") == 4
    assert doc.set_fill(0, "2:2", "red") == 3
    assert doc.set_fill(0, "$A$1", "red") == 1
    assert doc.set_fill(0, "C4:A4", "red") == 3  # reversed corners
    for bad in ("", "1A", "A1:B", "A0:B1", "A1:", "AAAA"):
        with pytest.raises(ValueError):
            doc.set_fill(0, bad, "red")
    with pytest.raises(ValueError):
        doc.set_fill(0, "A1", "not-a-color")
    with pytest.raises(KeyError):
        doc.set_fill("nope", "A1", "red")


def test_color_hex() -> None:
    assert st.color_hex("#abc123") == "FFABC123"
    assert st.color_hex("80ABC123") == "80ABC123"
    assert st.color_hex("Grey") == "FF808080"


def test_formatting_preserves_values_formulas_and_cells_view(tmp_path: Path) -> None:
    doc = _doc()
    doc.set_cell(0, "D2", "=B2*C2")
    doc.set_font(0, "A1:D4", bold=True)
    doc.set_number_format(0, "D2", "thousands_decimal")
    out = doc.save(tmp_path / "f.xlsx")
    r = XlsxHandler().open(out)
    cell = next(c for c in r.cells(0) if c.coordinate == "D2")
    assert cell.formula == "=B2*C2" and cell.bold
    assert cell.number_format == "#,##0.00"
    assert r.rows(0)[1][:3] == ["蘋果", 3, 12.5]
    assert r.style_summary(0)["bold_cells"] >= 4


@pytest.mark.requires("libreoffice")
def test_formatting_survives_libreoffice_roundtrip(tmp_path: Path) -> None:
    doc = _doc()
    doc.set_fill(0, "A1:C1", "yellow")
    doc.set_font(0, "A1", bold=True)
    doc.set_number_format(0, "C2", "0.000")
    doc.set_column_width(0, "A", 25)
    doc.freeze_panes(0, "A2")
    src = doc.save(tmp_path / "lo.xlsx")
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    out = convert_with_libreoffice(src, "xlsx", out_dir)
    r = XlsxHandler().open(out)
    assert r.cell_format(0, "B1")["fill"] == "FFFF00"
    assert r.cell_format(0, "A1")["font"]["bold"] is True
    assert r.cell_format(0, "C2")["number_format"] == "0.000"
    assert r.layout(0)["freeze_panes"] == "A2"
    assert abs(r.layout(0)["column_widths"]["A"] - 25) < 1
