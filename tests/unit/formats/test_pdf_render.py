"""Tests for PDF page rendering (T016)."""

from __future__ import annotations

import io
from collections.abc import Callable
from pathlib import Path

import pytest
from PIL import Image
from pypdf import PdfWriter

from anydoc.core.errors import OperationNotSupportedError
from anydoc.core.model import Capability
from anydoc.core.registry import registry
from anydoc.formats.pdf import PdfDocument, PdfHandler

from .test_pdf import _encrypted_pdf, _mixed_pdf

Sample = Callable[[str], Path]
PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


@pytest.fixture(autouse=True)
def _handler() -> None:
    registry.register(PdfHandler, replace=True)


def _open(sample: Sample) -> PdfDocument:
    doc = PdfHandler().open(sample("pdf"))
    assert isinstance(doc, PdfDocument)
    return doc


def _png(data: bytes) -> Image.Image:
    assert data.startswith(PNG_MAGIC)
    return Image.open(io.BytesIO(data))


def test_render_capability() -> None:
    assert Capability.RENDER in PdfHandler.capabilities


def test_render_default_is_72_dpi(sample: Sample) -> None:
    doc = _open(sample)
    page = doc.pages()[0]
    im = _png(doc.render_page(0))
    assert abs(im.width - page.width) <= 1
    assert abs(im.height - page.height) <= 1


def test_render_dpi_scales_size(sample: Sample) -> None:
    doc = _open(sample)
    base = _png(doc.render_page(0, dpi=72))
    big = _png(doc.render_page(0, dpi=144))
    assert abs(big.width - 2 * base.width) <= 2
    assert abs(big.height - 2 * base.height) <= 2


def test_render_scale_overrides_dpi(sample: Sample) -> None:
    doc = _open(sample)
    by_scale = _png(doc.render_page(0, scale=0.5, dpi=300))
    base = _png(doc.render_page(0))
    assert abs(by_scale.width - base.width / 2) <= 1


def test_render_has_content_and_white_background(sample: Sample) -> None:
    im = _png(_open(sample).render_page(0, dpi=100)).convert("L")
    lo, hi = im.getextrema()
    assert hi == 255  # white page background
    assert lo < 128  # dark text was drawn


def test_render_saves_to_path(sample: Sample, tmp_path: Path) -> None:
    out = tmp_path / "page.png"
    data = _open(sample).render_page(0, path=out)
    assert out.read_bytes() == data


def test_render_applies_rotation() -> None:
    doc = PdfHandler().open(_mixed_pdf())
    assert isinstance(doc, PdfDocument)
    for i, page in enumerate(doc.pages()):
        im = _png(doc.render_page(i))
        assert abs(im.width - page.width) <= 1
        assert abs(im.height - page.height) <= 1
    assert doc.pages()[1].rotation == 90


def test_render_page_out_of_range(sample: Sample) -> None:
    doc = _open(sample)
    with pytest.raises(IndexError):
        doc.render_page(99)
    with pytest.raises(IndexError):
        doc.render_page(-1)


@pytest.mark.parametrize("kwargs", [{"dpi": 0}, {"dpi": -5}, {"scale": 0}])
def test_render_rejects_non_positive(sample: Sample, kwargs: dict[str, float]) -> None:
    with pytest.raises(ValueError):
        _open(sample).render_page(0, **kwargs)


def test_render_encrypted(sample: Sample) -> None:
    data = _encrypted_pdf(sample)
    locked = PdfHandler().open(data)
    assert isinstance(locked, PdfDocument)
    with pytest.raises(OperationNotSupportedError):
        locked.render_page(0)
    unlocked = PdfHandler().open(data, password="secret")
    assert isinstance(unlocked, PdfDocument)
    assert _png(unlocked.render_page(0)).width > 0


def test_render_blank_page() -> None:
    writer = PdfWriter()
    writer.add_blank_page(width=100, height=50)
    buf = io.BytesIO()
    writer.write(buf)
    doc = PdfHandler().open(buf.getvalue())
    assert isinstance(doc, PdfDocument)
    im = _png(doc.render_page(0, dpi=144)).convert("L")
    assert im.size == (200, 100)
    assert im.getextrema() == (255, 255)


def test_render_engine_failure_raises_corrupt(
    sample: Sample, monkeypatch: pytest.MonkeyPatch
) -> None:
    import pypdfium2

    from anydoc.core.errors import CorruptFileError

    def boom(*_a: object, **_k: object) -> None:
        raise pypdfium2.PdfiumError("bad")

    monkeypatch.setattr(pypdfium2, "PdfDocument", boom)
    with pytest.raises(CorruptFileError):
        _open(sample).render_page(0)
