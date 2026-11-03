"""Tests for PDF table extraction and export (T046)."""

from __future__ import annotations

import io
import json
from pathlib import Path

import pytest
from openpyxl import load_workbook
from typer.testing import CliRunner

from anydoc.cli import app
from anydoc.converters.pdf_tables import pdf_tables_to_csv, pdf_tables_to_xlsx
from anydoc.core.errors import CorruptFileError, OperationNotSupportedError
from anydoc.formats.pdf import PdfHandler
from anydoc.formats.pdf.security import encrypt_pdf
from anydoc.formats.pdf.tables import PdfTable, extract_tables

runner = CliRunner()

DATA = [
    ["品名", "數量", "單價"],
    ["螺絲", "100", "1.5"],
    ["Nut", "250", "0.75"],
]


def _pdf(*, grid: bool = True, with_table_on: tuple[int, ...] = (0, 2), pages: int = 3) -> bytes:
    from reportlab.lib import colors
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.cidfonts import UnicodeCIDFont
    from reportlab.platypus import PageBreak, Paragraph, SimpleDocTemplate, Table, TableStyle

    pdfmetrics.registerFont(UnicodeCIDFont("STSong-Light"))
    style = [("FONTNAME", (0, 0), (-1, -1), "STSong-Light")]
    if grid:
        style.append(("GRID", (0, 0), (-1, -1), 0.8, colors.black))
    from reportlab.lib.styles import ParagraphStyle

    body = ParagraphStyle("b", fontName="STSong-Light")
    story: list = []
    for i in range(pages):
        story.append(Paragraph(f"Page {i + 1}", body))
        if i in with_table_on:
            story.append(Table(DATA, colWidths=[100, 80, 80], style=TableStyle(style)))
        if i < pages - 1:
            story.append(PageBreak())
    buf = io.BytesIO()
    SimpleDocTemplate(buf).build(story)
    return buf.getvalue()


def test_extract_ruled_tables() -> None:
    tables = extract_tables(_pdf())
    assert [(t.page, t.index) for t in tables] == [(0, 0), (2, 0)]
    assert tables[0].rows == DATA
    assert tables[0].row_count == 3 and tables[0].column_count == 3
    x0, top, x1, bottom = tables[0].bbox
    assert x0 < x1 and top < bottom


def test_page_selection_and_no_tables() -> None:
    assert [t.page for t in extract_tables(_pdf(), [2])] == [2]
    assert extract_tables(_pdf(), [1]) == []
    with pytest.raises(IndexError):
        extract_tables(_pdf(), [9])


def test_text_strategy_borderless() -> None:
    data = _pdf(grid=False, with_table_on=(0,), pages=1)
    assert extract_tables(data, strategy="lines") == []
    tables = extract_tables(data, strategy="text")
    assert tables
    flat = [c for r in tables[0].rows for c in r]
    assert "螺絲" in flat and "250" in flat


def test_filters_and_validation() -> None:
    assert extract_tables(_pdf(), min_rows=4) == []
    assert extract_tables(_pdf(), min_columns=4) == []
    with pytest.raises(ValueError):
        extract_tables(_pdf(), strategy="magic")


def test_invalid_and_encrypted() -> None:
    with pytest.raises(CorruptFileError):
        extract_tables(b"not a pdf")
    enc = encrypt_pdf(_pdf(), user_password="pw")
    with pytest.raises(OperationNotSupportedError):
        extract_tables(enc)
    assert len(extract_tables(enc, password="pw")) == 2


def test_to_dict() -> None:
    t = PdfTable(0, 0, (1.0, 2.0, 3.0, 4.0), [["a", "b"]])
    assert json.loads(json.dumps(t.to_dict()))["rows"] == [["a", "b"]]


def test_document_api(tmp_path: Path) -> None:
    f = tmp_path / "t.pdf"
    f.write_bytes(_pdf())
    doc = PdfHandler().open(f)
    assert len(doc.tables()) == 2
    assert len(doc.tables([0])) == 1
    doc.close()


def test_export_xlsx_roundtrip(tmp_path: Path) -> None:
    src = tmp_path / "t.pdf"
    src.write_bytes(_pdf())
    out = pdf_tables_to_xlsx(src, tmp_path / "o.xlsx")
    wb = load_workbook(out)
    assert wb.sheetnames == ["p1_t1", "p3_t1"]
    rows = [[c.value for c in r] for r in wb["p1_t1"].iter_rows()]
    assert rows[0] == ["品名", "數量", "單價"]
    assert rows[1] == ["螺絲", 100, 1.5]
    wb.close()
    hdr = pdf_tables_to_xlsx(src, tmp_path / "h.xlsx", header=True, infer_types=False)
    wb = load_workbook(hdr)
    assert wb["p1_t1"]["B2"].value == "100"
    wb.close()


def test_export_xlsx_without_tables(tmp_path: Path) -> None:
    src = tmp_path / "n.pdf"
    src.write_bytes(_pdf(with_table_on=()))
    out = pdf_tables_to_xlsx(src, tmp_path / "o.xlsx")
    wb = load_workbook(out)
    assert wb.sheetnames == ["Sheet1"]
    wb.close()


def test_export_csv(tmp_path: Path) -> None:
    src = tmp_path / "t.pdf"
    src.write_bytes(_pdf())
    paths = pdf_tables_to_csv(src, tmp_path / "csv")
    assert [p.name for p in paths] == ["t_p1_t1.csv", "t_p3_t1.csv"]
    lines = paths[0].read_text(encoding="utf-8").splitlines()
    assert lines[0] == "品名,數量,單價" and lines[2] == "Nut,250,0.75"
    tsv = pdf_tables_to_csv(src, tmp_path / "tsv", pages=[0], delimiter="\t", header=True)
    assert tsv[0].suffix == ".tsv"
    typed = pdf_tables_to_csv(src, tmp_path / "ty", pages=[0], infer_types=True)
    assert typed[0].read_text(encoding="utf-8").splitlines()[1] == "螺絲,100,1.5"


def test_cli(tmp_path: Path) -> None:
    src = tmp_path / "t.pdf"
    src.write_bytes(_pdf())
    res = runner.invoke(app, ["pdf", "tables", str(src)])
    assert res.exit_code == 0
    payload = json.loads(res.output)
    assert [p["page"] for p in payload] == [1, 3]
    assert payload[0]["rows"] == DATA

    res = runner.invoke(app, ["pdf", "tables", str(src), "--pages", "3"])
    assert [p["page"] for p in json.loads(res.output)] == [3]

    xl = tmp_path / "o.xlsx"
    assert runner.invoke(app, ["pdf", "tables", str(src), "-o", str(xl)]).exit_code == 0
    assert xl.exists()
    res = runner.invoke(app, ["pdf", "tables", str(src), "-o", str(tmp_path / "d")])
    assert res.exit_code == 0 and len(res.output.split()) == 2

    assert runner.invoke(app, ["pdf", "tables", str(src), "--strategy", "x"]).exit_code != 0
    assert runner.invoke(app, ["pdf", "tables", str(tmp_path / "missing.pdf")]).exit_code != 0
