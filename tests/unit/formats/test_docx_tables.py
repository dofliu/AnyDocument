"""Tests for DOCX tables and pictures (T052)."""

from __future__ import annotations

import io
from pathlib import Path

import pytest
from docx import Document as load_docx
from PIL import Image

import anydoc
from anydoc.core.errors import OperationNotSupportedError
from anydoc.core.registry import registry
from anydoc.formats.docx import DocxDocument, DocxHandler

W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


@pytest.fixture(autouse=True)
def _handler() -> None:
    registry.register(DocxHandler, replace=True)


def _doc() -> DocxDocument:
    return DocxHandler().create()


def _png(w: int = 40, h: int = 20) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (w, h), "red").save(buf, "PNG")
    return buf.getvalue()


def _reload(doc: DocxDocument) -> DocxDocument:
    return DocxHandler().open(doc.to_bytes())


def test_add_table_from_rows_with_header_and_chinese() -> None:
    doc = _doc()
    idx = doc.add_table([["名稱", "數量"], ["蘋果", 3], ["梨", None]], header=True)
    assert idx == 0
    assert doc.tables()[0].rows == [["名稱", "數量"], ["蘋果", "3"], ["梨", ""]]
    t = doc.native.tables[0]
    assert all(r.bold for r in t.cell(0, 0).paragraphs[0].runs)
    assert t.rows[0]._tr.trPr.find(f"{W}tblHeader") is not None


def test_add_table_empty_grid_alignment_and_errors() -> None:
    doc = _doc()
    doc.add_table(n_rows=2, n_cols=3, alignment="center")
    assert doc.tables()[0].rows == [["", "", ""], ["", "", ""]]
    with pytest.raises(ValueError):
        doc.add_table()
    with pytest.raises(ValueError):
        doc.add_table(n_rows=1, n_cols=1, alignment="x")
    with pytest.raises(ValueError):
        doc.add_table(n_rows=1, n_cols=1, style="NoSuchStyle")
    assert len(doc.tables()) == 1


def test_set_cell_text_and_format() -> None:
    doc = _doc()
    doc.add_table([["a", "b"], ["c", "d"]])
    doc.set_cell(0, 1, 1, "新值", bold=True, color="#FF0000")
    cell = doc.native.tables[0].cell(1, 1)
    assert cell.text == "新值"
    assert cell.paragraphs[0].runs[0].bold is True
    with pytest.raises(OperationNotSupportedError):
        doc.set_cell(0, 5, 0, "x")
    with pytest.raises(OperationNotSupportedError):
        doc.set_cell(3, 0, 0, "x")
    with pytest.raises(ValueError):
        doc.set_cell(0, 0, 0, "x", color="zz")


def test_merge_cells_horizontal_vertical_and_block() -> None:
    doc = _doc()
    doc.add_table(n_rows=3, n_cols=3)
    doc.merge_cells(0, 0, 0, 0, 2)
    doc.merge_cells(0, 1, 0, 2, 0)
    doc.merge_cells(0, 1, 1, 2, 2)
    t = doc.native.tables[0]
    assert t.cell(0, 0)._tc is t.cell(0, 2)._tc
    assert t.cell(1, 0)._tc is t.cell(2, 0)._tc
    assert t.cell(1, 1)._tc is t.cell(2, 2)._tc
    assert doc.merge_cells(0, 0, 0, 0, 0) is None
    with pytest.raises(ValueError):
        doc.merge_cells(0, 2, 2, 0, 0)
    with pytest.raises(OperationNotSupportedError):
        doc.merge_cells(0, 0, 0, 9, 9)
    # round trip keeps the merge
    t2 = _reload(doc).native.tables[0]
    assert t2.cell(0, 0)._tc is t2.cell(0, 2)._tc


def test_borders_and_shading() -> None:
    doc = _doc()
    doc.add_table(n_rows=1, n_cols=2, style=None)
    doc.set_table_borders(0, style="double", size=1.5, color="#00FF00")
    doc.set_table_borders(0, edges=["top"], style="dashed")
    doc.shade_cell(0, 0, 1, "FFFF00")
    doc.shade_cell(0, 0, 1, "CCCCCC")
    tbl = _reload(doc).native.tables[0]._tbl
    borders = tbl.tblPr.find(f"{W}tblBorders")
    assert [c.tag.split("}")[1] for c in borders] == [
        "top",
        "left",
        "bottom",
        "right",
        "insideH",
        "insideV",
    ]
    assert borders.find(f"{W}left").get(f"{W}val") == "double"
    assert borders.find(f"{W}left").get(f"{W}sz") == "12"
    assert borders.find(f"{W}left").get(f"{W}color") == "00FF00"
    assert borders.find(f"{W}top").get(f"{W}val") == "dashed"
    shds = tbl.findall(f".//{W}shd")
    assert len(shds) == 1 and shds[0].get(f"{W}fill") == "CCCCCC"
    with pytest.raises(ValueError):
        doc.set_table_borders(0, style="zigzag")
    with pytest.raises(ValueError):
        doc.set_table_borders(0, edges=["middle"])
    with pytest.raises(ValueError):
        doc.shade_cell(0, 0, 0, "nope")


def test_rows_add_insert_delete() -> None:
    doc = _doc()
    doc.add_table([["h1", "h2"], ["a", "b"]])
    assert doc.add_table_row(0, ["c", "d"]) == 2
    assert doc.add_table_row(0, ["x", "y"], index=1) == 1
    assert doc.add_table_row(0, index=0) == 0
    assert doc.tables()[0].rows == [["", ""], ["h1", "h2"], ["x", "y"], ["a", "b"], ["c", "d"]]
    doc.delete_table_row(0, 0)
    assert doc.tables()[0].rows[0] == ["h1", "h2"]
    with pytest.raises(OperationNotSupportedError):
        doc.delete_table_row(0, 9)
    with pytest.raises(OperationNotSupportedError):
        doc.add_table_row(0, index=9)
    assert _reload(doc).tables()[0].rows[-1] == ["c", "d"]


def test_insert_row_clears_multi_paragraph_cells() -> None:
    doc = _doc()
    doc.add_table([["a", "b"]])
    cell = doc.native.tables[0].cell(0, 0)
    cell.add_paragraph("second")
    doc.add_table_row(0, index=0)
    assert doc.tables()[0].rows == [["", ""], ["a\nsecond", "b"]]


def test_columns_add_delete_and_widths() -> None:
    doc = _doc()
    doc.add_table([["a", "b"], ["c", "d"]])
    assert doc.add_table_column(0, ["e", "f"], width_mm=20) == 2
    assert doc.tables()[0].rows == [["a", "b", "e"], ["c", "d", "f"]]
    doc.delete_table_column(0, 1)
    assert doc.tables()[0].rows == [["a", "e"], ["c", "f"]]
    doc.set_column_width(0, 0, 50)
    doc.set_column_width(0, 1, 1, "in")
    doc.set_row_height(0, 0, 12, "pt")
    t = _reload(doc).native.tables[0]
    assert abs(t.columns[0].width - 50 * 36000) < 1000
    assert abs(t.cell(1, 1).width - 914400) < 1000
    assert abs(t.rows[0].height - 12 * 12700) < 1000
    with pytest.raises(OperationNotSupportedError):
        doc.delete_table_column(0, 5)
    with pytest.raises(OperationNotSupportedError):
        doc.set_column_width(0, 5, 10)
    with pytest.raises(OperationNotSupportedError):
        doc.set_row_height(0, 5, 10)
    with pytest.raises(ValueError):
        doc.set_column_width(0, 0, 0)
    with pytest.raises(ValueError):
        doc.set_row_height(0, 0, -1)
    with pytest.raises(ValueError):
        doc.set_column_width(0, 0, 5, "km")


def test_delete_column_with_merged_span() -> None:
    doc = _doc()
    doc.add_table(n_rows=2, n_cols=3)
    doc.merge_cells(0, 0, 0, 0, 2)
    doc.delete_table_column(0, 1)
    t = doc.native.tables[0]
    assert len(t.columns) == 2
    assert t.cell(0, 0)._tc is t.cell(0, 1)._tc
    assert len(t.rows[1].cells) == 2


def test_add_image_sizes_and_alignment(tmp_path: Path) -> None:
    doc = _doc()
    assert doc.add_image(_png(), width=40) == (40 * 36000, 20 * 36000)
    assert doc.add_image(_png(), height=10, alignment="center") == (20 * 36000, 10 * 36000)
    w, h = doc.add_image(_png(), width=2, height=1, unit="in")
    assert (w, h) == (2 * 914400, 914400)
    path = tmp_path / "p.png"
    path.write_bytes(_png(10, 10))
    doc.add_image(path)
    reopened = _reload(doc)
    assert len(reopened.images()) >= 1
    assert len(reopened.native.inline_shapes) == 4
    assert reopened.native.paragraphs[1].alignment == 1


def test_add_image_errors(tmp_path: Path) -> None:
    doc = _doc()
    n = len(doc.native.paragraphs)
    with pytest.raises(ValueError):
        doc.add_image(b"not an image")
    with pytest.raises(FileNotFoundError):
        doc.add_image(tmp_path / "missing.png")
    with pytest.raises(ValueError):
        doc.add_image(_png(), width=-1)
    with pytest.raises(ValueError):
        doc.add_image(_png(), alignment="x")
    assert len(doc.native.paragraphs) == n


def test_roundtrip_file_opens_with_python_docx(tmp_path: Path) -> None:
    doc = _doc()
    doc.add_heading("報表", 1)
    doc.add_table([["項目", "值"], ["甲", "1"]], header=True)
    doc.merge_cells(0, 1, 0, 1, 1)
    doc.add_image(_png(), width=30)
    out = doc.save(tmp_path / "t.docx")
    native = load_docx(str(out))
    assert len(native.tables) == 1
    assert "報表" in anydoc.extract_text(out)
