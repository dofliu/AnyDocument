"""Tests for PDF OCR (T047). A fake ``tesseract`` script stands in when the tool is absent."""

from __future__ import annotations

import io
import stat
import sys
from pathlib import Path

import pytest
from PIL import Image
from pypdf import PdfReader
from typer.testing import CliRunner

from anydoc.cli import app
from anydoc.core.errors import ExternalToolMissingError
from anydoc.external import ExternalToolError
from anydoc.formats.pdf import PdfHandler
from anydoc.formats.pdf.ocr import list_ocr_languages, ocr_pdf

runner = CliRunner()

FAKE = f"""#!{sys.executable}
import sys
args = sys.argv[1:]
if args == ["--list-langs"]:
    print("List of available languages in /x (3):")
    print("eng"); print("chi_tra"); print("osd")
    sys.exit(0)
img, base = args[0], args[1]
from PIL import Image
from reportlab.pdfgen import canvas
w, h = Image.open(img).size
c = canvas.Canvas(base + ".pdf", pagesize=(w * 72 / 300, h * 72 / 300))
c.drawString(20, 20, "OCRTEXT")
c.save()
"""


@pytest.fixture
def fake_tesseract(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    exe = tmp_path / "bin" / "tesseract"
    exe.parent.mkdir()
    exe.write_text(FAKE)
    exe.chmod(exe.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("ANYDOC_TESSERACT", str(exe))
    return exe


def _scanned_pdf(pages: int = 2, text_page: int | None = None) -> bytes:
    """Image-only PDF; optionally one page carries a real text layer."""
    from reportlab.lib.utils import ImageReader
    from reportlab.pdfgen import canvas

    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=(200, 200))
    for i in range(pages):
        img = Image.new("RGB", (200, 200), "white")
        c.drawImage(ImageReader(img), 0, 0, 200, 200)
        if i == text_page:
            c.drawString(10, 100, "HASTEXT")
        c.showPage()
    c.save()
    return buf.getvalue()


def _texts(data: bytes) -> list[str]:
    return [(p.extract_text() or "").strip() for p in PdfReader(io.BytesIO(data)).pages]


def test_list_languages(fake_tesseract: Path) -> None:
    assert list_ocr_languages() == ["eng", "chi_tra"]


def test_ocr_adds_text_layer_to_all_pages(fake_tesseract: Path) -> None:
    src = _scanned_pdf(2)
    assert _texts(src) == ["", ""]
    out = ocr_pdf(src)
    assert _texts(out) == ["OCRTEXT", "OCRTEXT"]
    reader = PdfReader(io.BytesIO(out))
    assert all(round(float(p.mediabox.width)) == 200 for p in reader.pages)


def test_ocr_selected_pages_only(fake_tesseract: Path) -> None:
    out = ocr_pdf(_scanned_pdf(3), [1])
    assert _texts(out) == ["", "OCRTEXT", ""]


def test_skip_text_pages_by_default(fake_tesseract: Path) -> None:
    src = _scanned_pdf(2, text_page=0)
    assert _texts(ocr_pdf(src)) == ["HASTEXT", "OCRTEXT"]
    forced = _texts(ocr_pdf(src, skip_text=False))
    assert forced == ["OCRTEXT", "OCRTEXT"]


def test_missing_language_reports_error(fake_tesseract: Path) -> None:
    with pytest.raises(ExternalToolError, match="deu"):
        ocr_pdf(_scanned_pdf(1), lang="eng+deu")


def test_invalid_arguments(fake_tesseract: Path) -> None:
    with pytest.raises(ValueError):
        ocr_pdf(_scanned_pdf(1), dpi=0)
    with pytest.raises(ValueError):
        ocr_pdf(_scanned_pdf(1), lang="")
    with pytest.raises(IndexError):
        ocr_pdf(_scanned_pdf(1), [5])


def test_tool_missing_degrades_gracefully(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANYDOC_TESSERACT", "/nonexistent/tesseract")
    with pytest.raises(ExternalToolMissingError):
        ocr_pdf(_scanned_pdf(1))


def test_encrypted_pdf_with_password(fake_tesseract: Path) -> None:
    from anydoc.formats.pdf.security import encrypt_pdf

    enc = encrypt_pdf(_scanned_pdf(1), user_password="pw")
    out = ocr_pdf(enc, password="pw")
    assert _texts(out) == ["OCRTEXT"]


def test_document_method(fake_tesseract: Path, tmp_path: Path) -> None:
    p = tmp_path / "s.pdf"
    p.write_bytes(_scanned_pdf(1))
    doc = PdfHandler().open(p)
    doc.ocr()
    assert "OCRTEXT" in doc.text()


def test_cli_ocr(fake_tesseract: Path, tmp_path: Path) -> None:
    src, dst = tmp_path / "s.pdf", tmp_path / "o.pdf"
    src.write_bytes(_scanned_pdf(2))
    res = runner.invoke(app, ["pdf", "ocr", str(src), "-o", str(dst), "--pages", "2"])
    assert res.exit_code == 0, res.output
    assert _texts(dst.read_bytes()) == ["", "OCRTEXT"]


def test_cli_ocr_errors(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    src = tmp_path / "s.pdf"
    src.write_bytes(_scanned_pdf(1))
    monkeypatch.setenv("ANYDOC_TESSERACT", "/nonexistent/tesseract")
    res = runner.invoke(app, ["pdf", "ocr", str(src), "-o", str(tmp_path / "o.pdf")])
    assert res.exit_code == 1
    res = runner.invoke(
        app, ["pdf", "ocr", str(tmp_path / "no.pdf"), "-o", str(tmp_path / "o.pdf")]
    )
    assert res.exit_code == 2


@pytest.mark.requires("tesseract")
def test_real_tesseract_english(tmp_path: Path) -> None:
    from reportlab.pdfgen import canvas

    img = Image.new("RGB", (900, 300), "white")
    from PIL import ImageDraw, ImageFont

    try:
        font = ImageFont.load_default(size=120)
    except TypeError:
        pytest.skip("Pillow too old for scalable default font")
    ImageDraw.Draw(img).text((30, 60), "HELLO", fill="black", font=font)
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=(300, 100))
    from reportlab.lib.utils import ImageReader

    c.drawImage(ImageReader(img), 0, 0, 300, 100)
    c.save()
    out = ocr_pdf(buf.getvalue(), lang="eng")
    assert "HELLO" in _texts(out)[0].upper()
