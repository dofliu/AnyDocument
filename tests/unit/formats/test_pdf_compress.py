"""Tests for PDF compression (T048)."""

from __future__ import annotations

import io
import json
import os
import random
from pathlib import Path

import pytest
from PIL import Image
from pypdf import PdfReader
from typer.testing import CliRunner

from anydoc.cli import app
from anydoc.core.errors import CorruptFileError
from anydoc.formats.pdf import PdfHandler
from anydoc.formats.pdf import compress as cp
from anydoc.formats.pdf.pages import merge_pdfs

runner = CliRunner()


def _noise_png(size: int, seed: int = 1) -> bytes:
    rng = random.Random(seed)
    img = Image.frombytes("RGB", (size, size), rng.randbytes(size * size * 3))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _pdf(images: int = 2, size: int = 600) -> bytes:
    from reportlab.lib.utils import ImageReader
    from reportlab.pdfgen import canvas

    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=(400, 500), pageCompression=0)
    c.drawString(20, 480, "壓縮測試 compress me")
    for i in range(images):
        data = _noise_png(size, i + 1)
        c.drawImage(ImageReader(io.BytesIO(data)), 20, 20 + i * 10, width=300, height=300)
    c.showPage()
    c.save()
    return buf.getvalue()


def test_compress_reduces_size_and_keeps_content() -> None:
    src = _pdf()
    res = cp.compress_pdf_with_stats(src)
    assert res.new_size < res.original_size
    assert res.ratio < 0.8
    assert res.images_recompressed >= 1
    reader = PdfReader(io.BytesIO(res.data))
    assert len(reader.pages) == 1
    assert "compress me" in reader.pages[0].extract_text()
    assert len(reader.pages[0].images) == 2


def test_downscale_limits_image_side() -> None:
    res = cp.compress_pdf_with_stats(_pdf(images=1), max_image_side=400)
    assert res.images_downscaled == 1
    img = PdfReader(io.BytesIO(res.data)).pages[0].images[0].image
    assert max(img.size) <= 400


def test_no_downscale_when_disabled() -> None:
    res = cp.compress_pdf_with_stats(_pdf(images=1, size=700), max_image_side=None)
    img = PdfReader(io.BytesIO(res.data)).pages[0].images[0].image
    assert img.size == (700, 700)


def test_identical_objects_are_merged() -> None:
    # Merging copies yields three identical image objects (reportlab itself would dedupe).
    src = merge_pdfs([_pdf(images=1)] * 3)
    off = cp.compress_pdf_with_stats(src, recompress_images=False)
    assert off.new_size < off.original_size / 2
    assert off.images_recompressed == 0


def test_lower_quality_is_smaller() -> None:
    src = _pdf(images=1, size=700)
    hi = cp.compress_pdf(src, image_quality=95, max_image_side=None)
    lo = cp.compress_pdf(src, image_quality=10, max_image_side=None)
    assert len(lo) < len(hi)


def test_already_small_pdf_returned_unchanged() -> None:
    first = cp.compress_pdf(_pdf())
    again = cp.compress_pdf_with_stats(first)
    assert again.data == first or again.new_size <= again.original_size
    tiny = cp.compress_pdf_with_stats(_pdf(images=0))
    assert tiny.new_size <= tiny.original_size


def test_invalid_arguments_and_corrupt() -> None:
    with pytest.raises(ValueError):
        cp.compress_pdf(_pdf(images=0), image_quality=0)
    with pytest.raises(ValueError):
        cp.compress_pdf(_pdf(images=0), max_image_side=0)
    with pytest.raises(CorruptFileError):
        cp.compress_pdf(b"not a pdf")


def test_result_to_dict() -> None:
    d = cp.compress_pdf_with_stats(_pdf(images=1)).to_dict()
    assert "data" not in d and d["new_size"] <= d["original_size"]
    json.dumps(d)


def test_document_method(tmp_path: Path) -> None:
    p = tmp_path / "a.pdf"
    p.write_bytes(_pdf())
    doc = PdfHandler().open(p)
    before = len(doc._data)  # type: ignore[attr-defined]
    res = doc.compress()  # type: ignore[attr-defined]
    assert res.new_size < before
    assert len(doc._data) == res.new_size  # type: ignore[attr-defined]
    assert "compress me" in doc.text()


def test_cli_compress(tmp_path: Path) -> None:
    src, out = tmp_path / "a.pdf", tmp_path / "b.pdf"
    src.write_bytes(_pdf())
    r = runner.invoke(app, ["pdf", "compress", str(src), "-o", str(out), "--quality", "50"])
    assert r.exit_code == 0, r.output
    assert out.stat().st_size < src.stat().st_size
    assert '"ratio"' in r.output


def test_cli_compress_errors(tmp_path: Path) -> None:
    src = tmp_path / "a.pdf"
    src.write_bytes(_pdf(images=0))
    r = runner.invoke(
        app, ["pdf", "compress", str(src), "-o", str(tmp_path / "o.pdf"), "--quality", "0"]
    )
    assert r.exit_code != 0
    bad = tmp_path / "bad.pdf"
    bad.write_bytes(b"junk")
    r = runner.invoke(app, ["pdf", "compress", str(bad), "-o", str(tmp_path / "o.pdf")])
    assert r.exit_code != 0
    assert os.path.exists(bad)
