"""Tests for PDF <-> image converters (T033)."""

from __future__ import annotations

import io
from pathlib import Path

import pytest
from PIL import Image
from typer.testing import CliRunner

import anydoc
from anydoc.cli import app
from anydoc.converters import (
    IMAGE_FORMATS,
    images_to_pdf,
    load_builtin_converters,
    pdf_to_images,
    register_pdf_image,
)
from anydoc.core.convert import ConversionGraph, graph
from anydoc.core.errors import CorruptFileError

runner = CliRunner()


def _multipage_pdf(tmp_path: Path, n: int = 3) -> Path:
    frames = [Image.new("RGB", (80 + 10 * i, 60), (50 * i, 20, 200)) for i in range(n)]
    path = tmp_path / "multi.pdf"
    frames[0].save(path, "PDF", save_all=True, append_images=frames[1:])
    return path


def test_registers_both_directions() -> None:
    g = ConversionGraph()
    register_pdf_image(g)
    pairs = {(c.src, c.dst) for c in g.converters()}
    for fmt in IMAGE_FORMATS:
        assert ("pdf", fmt) in pairs
        assert (fmt, "pdf") in pairs
    assert all(not c.requires for c in g.converters())


def test_global_graph_has_pdf_image_edges() -> None:
    load_builtin_converters()
    pairs = [(c.src, c.dst) for c in graph.converters()]
    assert ("pdf", "png") in pairs and ("png", "pdf") in pairs
    assert len(pairs) == len(set(pairs))


def test_pdf_to_png_every_page(tmp_path: Path) -> None:
    pdf = _multipage_pdf(tmp_path, 3)
    files = pdf_to_images(pdf, tmp_path / "out", dpi=72)
    assert [f.name for f in files] == ["multi-001.png", "multi-002.png", "multi-003.png"]
    sizes = [Image.open(f).size for f in files]
    assert sizes == [(80, 60), (90, 60), (100, 60)]


def test_pdf_to_images_dpi_and_pages(tmp_path: Path) -> None:
    pdf = _multipage_pdf(tmp_path, 3)
    files = pdf_to_images(pdf, tmp_path / "o", "jpg", dpi=144, pages=[2])
    assert [f.name for f in files] == ["multi-003.jpg"]
    with Image.open(files[0]) as im:
        assert im.format == "JPEG"
        assert im.size == (200, 120)


def test_pdf_to_images_errors(tmp_path: Path) -> None:
    pdf = _multipage_pdf(tmp_path, 1)
    with pytest.raises(ValueError):
        pdf_to_images(pdf, tmp_path, "xyz")
    with pytest.raises(IndexError):
        pdf_to_images(pdf, tmp_path, pages=[5])


def test_real_sample_pdf_renders(tmp_path: Path, sample) -> None:  # type: ignore[no-untyped-def]
    files = pdf_to_images(sample("pdf"), tmp_path)
    assert files and Image.open(files[0]).size[0] > 0


def test_images_to_pdf_merges_and_roundtrips(tmp_path: Path, sample) -> None:  # type: ignore[no-untyped-def]
    pdf = images_to_pdf([sample("png"), sample("jpg")], tmp_path / "m.pdf")
    assert anydoc.detect(pdf).format == "pdf"
    doc = anydoc.open(pdf)
    try:
        assert len(doc.parts()) == 2
    finally:
        doc.close()
    back = pdf_to_images(pdf, tmp_path / "back", dpi=72)
    assert [Image.open(f).size for f in back] == [(64, 48), (64, 48)]


def test_images_to_pdf_transparency_and_multiframe(tmp_path: Path) -> None:
    rgba = tmp_path / "a.png"
    Image.new("RGBA", (20, 20), (255, 0, 0, 0)).save(rgba)
    frames = [Image.new("P", (30, 30), i) for i in range(3)]
    tif = tmp_path / "m.tiff"
    frames[0].save(tif, save_all=True, append_images=frames[1:])
    pdf = images_to_pdf([rgba, tif], tmp_path / "o.pdf")
    imgs = pdf_to_images(pdf, tmp_path / "r", dpi=72)
    assert len(imgs) == 4
    with Image.open(imgs[0]) as first:
        assert first.convert("RGB").getpixel((10, 10)) == (255, 255, 255)


def test_images_to_pdf_validation(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        images_to_pdf([], tmp_path / "x.pdf")
    with pytest.raises(ValueError):
        images_to_pdf([tmp_path], tmp_path / "x.pdf", dpi=0)
    bad = tmp_path / "bad.png"
    bad.write_bytes(b"not an image")
    with pytest.raises(CorruptFileError):
        images_to_pdf([bad], tmp_path / "x.pdf")


def test_convert_png_to_pdf_and_back(tmp_path: Path, sample) -> None:  # type: ignore[no-untyped-def]
    pdf = anydoc.convert(sample("png"), tmp_path / "x.pdf")
    assert anydoc.detect(pdf).format == "pdf"
    png = anydoc.convert(pdf, tmp_path / "x.png")
    with Image.open(png) as im:
        assert im.format == "PNG"
        assert im.size == (128, 96)  # 64x48 px at 72 dpi rendered at 144 dpi


def test_convert_pdf_to_jpg_first_page(tmp_path: Path) -> None:
    pdf = _multipage_pdf(tmp_path, 2)
    out = anydoc.convert(pdf, tmp_path / "p.jpg")
    with Image.open(out) as im:
        assert im.format == "JPEG"
        assert im.size == (160, 120)


def test_cli_convert_jpg_to_pdf(tmp_path: Path, sample) -> None:  # type: ignore[no-untyped-def]
    out = tmp_path / "c.pdf"
    result = runner.invoke(app, ["convert", str(sample("jpg")), str(out)])
    assert result.exit_code == 0, result.output
    assert out.read_bytes().startswith(b"%PDF")


def test_encode_roundtrip_bytes(tmp_path: Path) -> None:
    pdf = _multipage_pdf(tmp_path, 1)
    (f,) = pdf_to_images(pdf, tmp_path / "w", "webp", dpi=72)
    assert Image.open(io.BytesIO(f.read_bytes())).format == "WEBP"
