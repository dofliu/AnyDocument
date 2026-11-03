"""Tests for PDF bookmarks (T045)."""

from __future__ import annotations

import io
import json
from pathlib import Path

import pytest
from pypdf import PdfReader
from typer.testing import CliRunner

from anydoc.cli import app
from anydoc.core.errors import CorruptFileError, OperationNotSupportedError
from anydoc.formats.pdf import PdfHandler
from anydoc.formats.pdf import bookmarks as bm

runner = CliRunner()


def _pdf(pages: int = 4) -> bytes:
    from reportlab.pdfgen import canvas

    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=(300, 400))
    for i in range(pages):
        c.drawString(20, 380, f"Page {i + 1}")
        c.showPage()
    c.save()
    return buf.getvalue()


MARKS = [
    bm.Bookmark("第一章 緒論", 0),
    bm.Bookmark("1.1 背景", 1, 1),
    bm.Bookmark("1.1.1 細節", 2, 2),
    bm.Bookmark("1.2 目的", 2, 1),
    bm.Bookmark("Chapter 2", 3),
]


def test_empty_outline() -> None:
    assert bm.list_bookmarks(_pdf()) == []


def test_set_and_list_roundtrip() -> None:
    d = bm.set_bookmarks(_pdf(), MARKS)
    assert bm.list_bookmarks(d) == MARKS
    # Natively re-openable with pypdf and the hierarchy is real.
    outline = PdfReader(io.BytesIO(d)).outline
    assert len(outline) == 3  # 2 top-level items + 1 nested list
    assert PdfReader(io.BytesIO(d)).trailer["/Root"]["/PageMode"] == "/UseOutlines"


def test_set_accepts_dicts_and_replaces() -> None:
    d = bm.set_bookmarks(_pdf(), MARKS)
    d = bm.set_bookmarks(d, [{"title": "Only", "page": 3}])
    assert bm.list_bookmarks(d) == [bm.Bookmark("Only", 3, 0)]


def test_add_bookmark_appends() -> None:
    d = bm.add_bookmark(_pdf(), "A", 0)
    d = bm.add_bookmark(d, "A.1", 1, level=1)
    d = bm.add_bookmark(d, "B", 2)
    assert [(b.title, b.level) for b in bm.list_bookmarks(d)] == [("A", 0), ("A.1", 1), ("B", 0)]


def test_remove_bookmarks() -> None:
    d = bm.remove_bookmarks(bm.set_bookmarks(_pdf(), MARKS))
    assert bm.list_bookmarks(d) == []
    assert "/Outlines" not in PdfReader(io.BytesIO(d)).trailer["/Root"]
    assert len(PdfReader(io.BytesIO(d)).pages) == 4


def test_validation() -> None:
    with pytest.raises(ValueError, match="skips a level"):
        bm.set_bookmarks(_pdf(), [bm.Bookmark("x", 0, 1)])
    with pytest.raises(ValueError, match=">= 0"):
        bm.set_bookmarks(_pdf(), [bm.Bookmark("x", 0, -1)])
    with pytest.raises(ValueError, match="no page"):
        bm.set_bookmarks(_pdf(), [bm.Bookmark("x", None)])
    with pytest.raises(IndexError):
        bm.set_bookmarks(_pdf(), [bm.Bookmark("x", 9)])


def test_to_dict_and_empty_set() -> None:
    assert MARKS[1].to_dict() == {"title": "1.1 背景", "page": 1, "level": 1}
    d = bm.set_bookmarks(bm.set_bookmarks(_pdf(), MARKS), [])
    assert bm.list_bookmarks(d) == []


def test_invalid_pdf() -> None:
    with pytest.raises(CorruptFileError):
        bm.list_bookmarks(b"not a pdf")


def test_document_api(tmp_path: Path) -> None:
    p = tmp_path / "a.pdf"
    p.write_bytes(_pdf())
    doc = PdfHandler().open(p)
    doc.set_bookmarks(MARKS[:2])
    doc.add_bookmark("Last", 3)
    assert [b.title for b in doc.bookmarks()] == ["第一章 緒論", "1.1 背景", "Last"]
    doc.remove_bookmarks()
    assert doc.bookmarks() == []
    doc.close()


def test_encrypted_edit_rejected(tmp_path: Path) -> None:
    from anydoc.formats.pdf.security import encrypt_pdf

    p = tmp_path / "e.pdf"
    p.write_bytes(encrypt_pdf(_pdf(), user_password="pw"))
    doc = PdfHandler().open(p, password="pw")
    assert doc.bookmarks() == []
    with pytest.raises(OperationNotSupportedError):
        doc.add_bookmark("x", 0)
    doc.close()


def test_cli(tmp_path: Path) -> None:
    src = tmp_path / "a.pdf"
    src.write_bytes(_pdf())
    spec = tmp_path / "spec.json"
    spec.write_text(
        json.dumps([{"title": "首頁", "page": 1}, {"title": "子節", "page": 3, "level": 1}]),
        encoding="utf-8",
    )
    out = tmp_path / "b.pdf"
    r = runner.invoke(app, ["pdf", "set-bookmarks", str(src), "-o", str(out), "--spec", str(spec)])
    assert r.exit_code == 0, r.output
    r = runner.invoke(app, ["pdf", "bookmarks", str(out)])
    assert r.exit_code == 0
    assert json.loads(r.output) == [
        {"title": "首頁", "page": 1, "level": 0},
        {"title": "子節", "page": 3, "level": 1},
    ]
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps([{"title": "x", "page": 99}]))
    assert (
        runner.invoke(
            app, ["pdf", "set-bookmarks", str(src), "-o", str(out), "--spec", str(bad)]
        ).exit_code
        != 0
    )
    cleared = tmp_path / "c.pdf"
    assert (
        runner.invoke(app, ["pdf", "remove-bookmarks", str(out), "-o", str(cleared)]).exit_code == 0
    )
    r = runner.invoke(app, ["pdf", "bookmarks", str(cleared)])
    assert json.loads(r.output) == []
