"""Tests for the PDF reader (T015)."""

from __future__ import annotations

import io
from collections.abc import Callable
from pathlib import Path

import pytest
from pypdf import PdfReader, PdfWriter
from reportlab.lib.pagesizes import A4, letter
from reportlab.pdfgen import canvas

import anydoc
from anydoc.core.errors import CorruptFileError, OperationNotSupportedError
from anydoc.core.model import Capability, Document
from anydoc.core.registry import registry
from anydoc.formats.pdf import PdfDocument, PdfHandler
from tests.fixtures.generate import BODY, TITLE

Sample = Callable[[str], Path]


@pytest.fixture(autouse=True)
def _handler() -> None:
    registry.register(PdfHandler, replace=True)


def _encrypted_pdf(sample: Sample, password: str = "secret") -> bytes:
    writer = PdfWriter(clone_from=str(sample("pdf")))
    writer.encrypt(password)
    buf = io.BytesIO()
    writer.write(buf)
    return buf.getvalue()


def _mixed_pdf() -> bytes:
    """Letter page with an image, plus an A4 page rotated 90 degrees."""
    from PIL import Image
    from reportlab.lib.utils import ImageReader

    img = io.BytesIO()
    Image.new("RGB", (20, 10), "blue").save(img, format="PNG")
    img.seek(0)
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=letter)
    c.drawString(72, 700, "Hello PDF")
    c.drawImage(ImageReader(img), 100, 400, width=200, height=100)
    c.showPage()
    c.setPageSize(A4)
    c.drawString(72, 700, "Rotated")
    c.showPage()
    c.save()
    writer = PdfWriter(clone_from=io.BytesIO(buf.getvalue()))
    writer.pages[1].rotate(90)
    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()


def test_entry_point_and_capabilities() -> None:
    from importlib.metadata import entry_points

    eps = {ep.name: ep.value for ep in entry_points(group="anydoc.formats")}
    assert eps["pdf"] == "anydoc.formats.pdf:PdfHandler"
    assert {Capability.READ, Capability.EXTRACT_TEXT, Capability.RENDER} <= PdfHandler.capabilities


def test_sniff() -> None:
    assert PdfHandler.sniff(b"%PDF-1.7\n", None) == 1.0
    assert PdfHandler.sniff(b"\n %PDF-1.4", None) == 1.0
    assert PdfHandler.sniff(b"hello", Path("a.pdf")) == 0.5
    assert PdfHandler.sniff(b"hello", Path("a.txt")) == 0.0


def test_open_sample_via_public_api(sample: Sample) -> None:
    doc = anydoc.open(sample("pdf"))
    assert isinstance(doc, Document)
    assert isinstance(doc, PdfDocument)
    assert doc.metadata.format == "pdf"
    assert doc.metadata.page_count == 2


def test_metadata(sample: Sample) -> None:
    doc = PdfHandler().open(sample("pdf"))
    assert doc.metadata.title == TITLE
    assert doc.metadata.mime == "application/pdf"
    assert doc.metadata.extra["encrypted"] is False
    assert doc.metadata.extra["pdf_version"]
    assert doc.page_count == 2


def test_dates_and_missing_info() -> None:
    buf = io.BytesIO()
    writer = PdfWriter()
    writer.add_blank_page(100, 100)
    writer.add_metadata({"/Author": "某人", "/CreationDate": "D:20240102030405Z"})
    writer.write(buf)
    doc = PdfHandler().open(buf.getvalue())
    assert doc.metadata.author == "某人"
    assert doc.metadata.created is not None and doc.metadata.created.year == 2024
    assert doc.metadata.modified is None

    bad = io.BytesIO()
    w2 = PdfWriter()
    w2.add_blank_page(10, 10)
    w2.add_metadata({"/ModDate": "garbage"})
    w2.write(bad)
    assert PdfHandler().open(bad.getvalue()).metadata.modified is None


def test_page_geometry_and_rotation() -> None:
    doc = PdfHandler().open(_mixed_pdf())
    p1, p2 = doc.pages()
    assert (p1.width, p1.height, p1.rotation) == (612.0, 792.0, 0)
    assert p2.rotation == 90
    # A4 portrait rotated by 90 degrees is displayed as landscape
    assert p2.width == pytest.approx(841.89, abs=0.01)
    assert p2.height == pytest.approx(595.28, abs=0.01)


def test_parts(sample: Sample) -> None:
    parts = PdfHandler().open(sample("pdf")).parts()
    assert [(p.kind, p.index, p.name) for p in parts] == [
        ("page", 0, "Page 1"),
        ("page", 1, "Page 2"),
    ]
    assert parts[0].size == pytest.approx((595.28, 841.89), abs=0.01)


def test_text_chinese_and_english(sample: Sample) -> None:
    doc = PdfHandler().open(sample("pdf"))
    assert TITLE in doc.page_text(0)
    assert BODY in doc.page_text(0)
    assert "Page two: hello world" in doc.page_text(1)
    full = doc.text()
    assert full.index(TITLE) < full.index("Page two")


def test_words_have_coordinates() -> None:
    doc = PdfHandler().open(_mixed_pdf())
    words = doc.words(0)
    assert [w.text for w in words] == ["Hello", "PDF"]
    hello = words[0]
    assert hello.x0 == pytest.approx(72, abs=1)
    # y=700 from the bottom of a 792pt page -> about 92 from the top
    assert 80 < hello.top < hello.bottom < 100
    assert hello.x1 > hello.x0
    assert words[1].x0 > hello.x1


def test_images() -> None:
    doc = PdfHandler().open(_mixed_pdf())
    (img,) = doc.images(0)
    assert img.x0 == pytest.approx(100, abs=0.5)
    assert img.x1 == pytest.approx(300, abs=0.5)
    assert img.bottom - img.top == pytest.approx(100, abs=0.5)
    assert (img.src_width, img.src_height) == (20, 10)
    assert doc.images(1) == []


def test_page_index_out_of_range(sample: Sample) -> None:
    doc = PdfHandler().open(sample("pdf"))
    for call in (doc.page_text, doc.words, doc.images):
        with pytest.raises(IndexError):
            call(5)


def test_encrypted_locked_without_password(sample: Sample) -> None:
    doc = PdfHandler().open(_encrypted_pdf(sample))
    assert doc.is_encrypted and doc.is_locked
    assert doc.metadata.extra["encrypted"] is True
    assert doc.metadata.extra["locked"] is True
    assert doc.page_count is None
    assert doc.metadata.page_count is None
    for call in (doc.text, doc.pages, doc.parts, lambda: doc.page_text(0)):
        with pytest.raises(OperationNotSupportedError):
            call()


def test_encrypted_with_password(sample: Sample) -> None:
    doc = PdfHandler().open(_encrypted_pdf(sample), password="secret")
    assert doc.is_encrypted and not doc.is_locked
    assert doc.metadata.page_count == 2
    assert TITLE in doc.page_text(0)


def test_encrypted_with_wrong_password_is_locked(sample: Sample) -> None:
    doc = PdfHandler().open(_encrypted_pdf(sample), password="nope")
    assert doc.is_locked


def test_empty_password_pdf_opens_unlocked(sample: Sample) -> None:
    doc = PdfHandler().open(_encrypted_pdf(sample, password=""))
    assert doc.is_encrypted and not doc.is_locked
    assert TITLE in doc.text()


def test_open_from_bytes_has_no_path(sample: Sample) -> None:
    doc = PdfHandler().open(sample("pdf").read_bytes())
    assert doc.path is None
    with pytest.raises(ValueError):
        doc.save()


def test_save_roundtrip_and_in_place(sample: Sample, tmp_path: Path) -> None:
    src = tmp_path / "copy.pdf"
    src.write_bytes(sample("pdf").read_bytes())
    doc = PdfHandler().open(src)
    out = doc.save(tmp_path / "out.pdf")
    assert len(PdfReader(str(out)).pages) == 2
    assert doc.save() == src


def test_close_releases_plumber(sample: Sample) -> None:
    doc = PdfHandler().open(sample("pdf"))
    doc.page_text(0)
    doc.close()
    assert doc.page_text(1)  # re-opens lazily
    doc.close()
    doc.close()


@pytest.mark.parametrize("data", [b"", b"not a pdf", b"%PDF-1.4\ngarbage"])
def test_corrupt_input(data: bytes) -> None:
    with pytest.raises(CorruptFileError):
        PdfHandler().open(data)


def test_unparsable_content_raises_corrupt(sample: Sample, monkeypatch: pytest.MonkeyPatch) -> None:
    import pdfplumber

    def boom(*_a: object, **_k: object) -> None:
        raise ValueError("bad")

    doc = PdfHandler().open(sample("pdf"))
    monkeypatch.setattr(pdfplumber, "open", boom)
    with pytest.raises(CorruptFileError):
        doc.text()


def test_missing_file(tmp_path: Path) -> None:
    with pytest.raises(CorruptFileError):
        PdfHandler().open(tmp_path / "nope.pdf")


def test_cli_info(sample: Sample) -> None:
    from typer.testing import CliRunner

    from anydoc.cli import app

    result = CliRunner().invoke(app, ["info", str(sample("pdf"))])
    assert result.exit_code == 0, result.output
    assert "Page 2" in result.output
