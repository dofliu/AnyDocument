"""Tests for PDF annotations (T044)."""

from __future__ import annotations

import io
import json
from pathlib import Path

import pytest
from pypdf import PdfReader
from typer.testing import CliRunner

from anydoc.cli import app
from anydoc.core.errors import OperationNotSupportedError
from anydoc.formats.pdf import PdfHandler
from anydoc.formats.pdf import annotations as an

runner = CliRunner()


def _pdf(pages: int = 2) -> bytes:
    from reportlab.pdfgen import canvas

    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=(300, 400))
    for i in range(pages):
        c.drawString(20, 380, f"Page {i + 1}")
        c.linkURL("https://example.com", (10, 10, 50, 30))
        c.showPage()
    c.save()
    return buf.getvalue()


def test_empty_and_links_skipped() -> None:
    assert an.list_annotations(_pdf()) == []
    links = an.list_annotations(_pdf(), include_links=True)
    assert {a.type for a in links} == {"link"}


def test_add_and_list_all_kinds() -> None:
    d = an.add_highlight(
        _pdf(), 0, [(20, 370, 80, 390), (20, 340, 80, 360)], contents="hl", author="Bob"
    )
    d = an.add_text_annotation(d, 0, "中文備註", point=(100, 300))
    d = an.add_text_annotation(d, 1, "Box text", rect=(20, 300, 200, 340))
    d = an.add_rectangle(d, 1, (30, 200, 130, 260), fill="FFEEEE", contents="r")
    d = an.add_stamp(d, 1, "APPROVED", (150, 20, 280, 70))
    items = an.list_annotations(d)
    assert [(a.page, a.type) for a in items] == [
        (0, "highlight"),
        (0, "text"),
        (1, "freetext"),
        (1, "square"),
        (1, "stamp"),
    ]
    assert items[0].contents == "hl" and items[0].author == "Bob"
    assert items[0].rect == (20.0, 340.0, 80.0, 390.0)
    assert items[1].contents == "中文備註"
    assert items[4].contents == "APPROVED" and items[4].color is not None
    assert len(PdfReader(io.BytesIO(d)).pages) == 2
    json.dumps([a.to_dict() for a in items])


def test_stamp_has_appearance_and_validation() -> None:
    d = an.add_stamp(_pdf(1), 0, "A(B)", (10, 10, 110, 50))
    annot = PdfReader(io.BytesIO(d)).pages[0]["/Annots"][-1].get_object()
    assert "/AP" in annot and b"Tj" in annot["/AP"]["/N"].get_object().get_data()
    with pytest.raises(ValueError):
        an.add_stamp(_pdf(1), 0, "X", (10, 10, 10, 50))
    with pytest.raises(ValueError):
        an.add_highlight(_pdf(1), 0, [])
    with pytest.raises(IndexError):
        an.add_rectangle(_pdf(1), 5, (0, 0, 10, 10))


def test_remove_filters() -> None:
    d = an.add_highlight(_pdf(), 0, (20, 370, 80, 390))
    d = an.add_rectangle(d, 1, (30, 200, 130, 260))
    only_hl = an.remove_annotations(d, types={"highlight"})
    assert [a.type for a in an.list_annotations(only_hl)] == ["square"]
    page0 = an.remove_annotations(d, pages=[0])
    assert [a.type for a in an.list_annotations(page0)] == ["square"]
    none = an.remove_annotations(d)
    assert an.list_annotations(none) == []
    assert len(an.list_annotations(none, include_links=True)) == 2  # links preserved


def test_encrypted_rejected() -> None:
    from anydoc.formats.pdf.security import encrypt_pdf

    enc = encrypt_pdf(_pdf(1), user_password="pw")
    with pytest.raises(OperationNotSupportedError):
        an.add_rectangle(enc, 0, (0, 0, 10, 10), password="pw")
    assert an.list_annotations(enc, "pw") == []


def test_document_api(tmp_path: Path) -> None:
    p = tmp_path / "a.pdf"
    p.write_bytes(_pdf())
    doc = PdfHandler().open(p)
    doc.add_highlight(0, (20, 370, 80, 390))
    doc.add_text_annotation(0, "n", point=(50, 50))
    doc.add_rectangle(1, (10, 10, 50, 50))
    doc.add_stamp(1, "DRAFT", (100, 100, 200, 150))
    assert len(doc.annotations()) == 4
    doc.remove_annotations(types={"stamp"})
    assert len(doc.annotations()) == 3
    out = tmp_path / "o.pdf"
    doc.save(out)
    assert len(an.list_annotations(out.read_bytes())) == 3


def test_cli(tmp_path: Path) -> None:
    src = tmp_path / "s.pdf"
    src.write_bytes(_pdf())
    cur = src
    for i, kind in enumerate(["highlight", "note", "box", "rect", "stamp"]):
        out = tmp_path / f"o{i}.pdf"
        r = runner.invoke(
            app,
            [
                "pdf",
                "annotate",
                str(cur),
                "-o",
                str(out),
                "--kind",
                kind,
                "--text",
                "TXT",
                "--rect",
                "20,300,200,340",
            ],
        )
        assert r.exit_code == 0, r.output
        cur = out
    r = runner.invoke(app, ["pdf", "annots", str(cur)])
    assert r.exit_code == 0
    assert [a["type"] for a in json.loads(r.output)] == [
        "highlight",
        "text",
        "freetext",
        "square",
        "stamp",
    ]
    cleared = tmp_path / "c.pdf"
    assert runner.invoke(app, ["pdf", "remove-annots", str(cur), "-o", str(cleared)]).exit_code == 0
    assert an.list_annotations(cleared.read_bytes()) == []
    bad = runner.invoke(
        app, ["pdf", "annotate", str(src), "-o", str(tmp_path / "x.pdf"), "--kind", "zzz"]
    )
    assert bad.exit_code != 0
    bad = runner.invoke(
        app,
        [
            "pdf",
            "annotate",
            str(src),
            "-o",
            str(tmp_path / "x.pdf"),
            "--kind",
            "box",
            "--rect",
            "1,2",
        ],
    )
    assert bad.exit_code != 0
