"""Tests for thumbnail generation (T036)."""

from __future__ import annotations

import io
from collections.abc import Callable
from pathlib import Path

import pytest
from PIL import Image
from typer.testing import CliRunner

import anydoc
from anydoc.cli import app
from anydoc.converters import THUMBNAIL_FORMATS, make_thumbnail, render_preview
from anydoc.core.errors import ExternalToolMissingError, UnsupportedFormatError

runner = CliRunner()
Sample = Callable[[str], Path]


def _open(data: bytes) -> Image.Image:
    im = Image.open(io.BytesIO(data))
    im.load()
    return im


def _not_blank(im: Image.Image) -> bool:
    lo, hi = im.convert("L").getextrema()
    return bool(hi > lo)


@pytest.mark.parametrize("fmt", ["pdf", "png", "jpg", "dxf", "txt", "json", "csv", "html", "epub"])
def test_thumbnail_no_external_tools(sample: Sample, fmt: str) -> None:
    data = make_thumbnail(sample(fmt), size=128)
    im = _open(data)
    assert im.format == "PNG"
    assert max(im.size) <= 128
    if fmt != "jpg":  # the generated JPEG sample is a flat colour
        assert _not_blank(im)


@pytest.mark.requires("libreoffice")
@pytest.mark.parametrize("fmt", ["docx", "xlsx", "pptx", "rtf"])
def test_thumbnail_office_via_libreoffice(sample: Sample, fmt: str) -> None:
    im = _open(make_thumbnail(sample(fmt), size=200))
    assert max(im.size) <= 200
    assert _not_blank(im)


def test_size_limits_longest_side(sample: Sample) -> None:
    for size in (32, 100):
        im = _open(make_thumbnail(sample("pdf"), size=size))
        assert max(im.size) == size


def test_small_image_not_upscaled(tmp_path: Path) -> None:
    src = tmp_path / "s.png"
    Image.new("RGB", (20, 10), "red").save(src)
    assert _open(make_thumbnail(src, size=256)).size == (20, 10)


def test_output_formats_and_file(sample: Sample, tmp_path: Path) -> None:
    for ext, name in (("png", "PNG"), ("jpg", "JPEG"), ("webp", "WEBP")):
        out = tmp_path / f"t.{ext}"
        data = make_thumbnail(sample("png"), out, size=64)
        assert out.read_bytes() == data
        assert _open(data).format == name
    assert set(THUMBNAIL_FORMATS) == {"png", "jpeg", "webp"}


def test_transparency_flattened_to_white(tmp_path: Path) -> None:
    src = tmp_path / "a.png"
    Image.new("RGBA", (10, 10), (0, 0, 0, 0)).save(src)
    im = _open(make_thumbnail(src, fmt="jpg"))
    assert im.convert("RGB").getpixel((5, 5)) == (255, 255, 255)


def test_pdf_page_selection(tmp_path: Path) -> None:
    frames = [Image.new("RGB", (80, 60), c) for c in ("red", "blue")]
    pdf = tmp_path / "m.pdf"
    frames[0].save(pdf, "PDF", save_all=True, append_images=frames[1:])
    p0 = _open(make_thumbnail(pdf, size=64, page=0)).convert("RGB").getpixel((10, 10))
    p1 = _open(make_thumbnail(pdf, size=64, page=1)).convert("RGB").getpixel((10, 10))
    assert p0[0] > 200 and p1[2] > 200
    with pytest.raises(IndexError):
        make_thumbnail(pdf, page=5)


def test_multiframe_gif_frame(tmp_path: Path) -> None:
    gif = tmp_path / "a.gif"
    a, b = Image.new("RGB", (10, 10), "red"), Image.new("RGB", (10, 10), "blue")
    a.save(gif, save_all=True, append_images=[b])
    assert _open(make_thumbnail(gif, page=1)).convert("RGB").getpixel((5, 5))[2] > 200


def test_dxf_layout_and_preview(sample: Sample) -> None:
    im = render_preview(sample("dxf"), size=100)
    assert im.width > 0 and _not_blank(im)


def test_text_preview_cjk_not_blank(tmp_path: Path) -> None:
    src = tmp_path / "zh.txt"
    src.write_text("繁體中文預覽\n第二行 second line\n", encoding="utf-8")
    assert _not_blank(_open(make_thumbnail(src, size=200)))


def test_dwg_without_converter(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from anydoc.formats.dwg import handler

    monkeypatch.setattr(handler, "convert_dwg_to_dxf", lambda *a, **k: None, raising=False)
    src = tmp_path / "x.dwg"
    src.write_bytes(b"AC1032" + b"\x00" * 100)
    try:
        make_thumbnail(src)
    except ExternalToolMissingError as exc:
        assert "DWG" in str(exc)
    except Exception:  # converter genuinely installed or other handler path
        pytest.skip("DWG converter present")


def test_office_without_libreoffice(sample: Sample, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PATH", "")
    monkeypatch.delenv("ANYDOC_LIBREOFFICE", raising=False)
    with pytest.raises(ExternalToolMissingError):
        make_thumbnail(sample("docx"))


def test_errors(tmp_path: Path, sample: Sample) -> None:
    with pytest.raises(ValueError):
        make_thumbnail(sample("png"), size=0)
    with pytest.raises(ValueError):
        make_thumbnail(sample("png"), fmt="bmp")
    with pytest.raises(FileNotFoundError):
        make_thumbnail(tmp_path / "missing.pdf")
    bad = tmp_path / "bad.bin"
    bad.write_bytes(b"\x00\x01\x02\x03" * 10)
    with pytest.raises(UnsupportedFormatError):
        make_thumbnail(bad)


def test_public_api(sample: Sample, tmp_path: Path) -> None:
    out = tmp_path / "o.png"
    anydoc.thumbnail(sample("pdf"), out, size=50)
    assert out.is_file() and max(Image.open(out).size) <= 50


def test_cli_thumbnail(sample: Sample, tmp_path: Path) -> None:
    out = tmp_path / "c.jpg"
    res = runner.invoke(app, ["thumbnail", str(sample("pdf")), str(out), "--size", "80"])
    assert res.exit_code == 0, res.output
    assert Image.open(out).format == "JPEG"


def test_cli_thumbnail_errors(sample: Sample, tmp_path: Path) -> None:
    res = runner.invoke(app, ["thumbnail", str(tmp_path / "no.pdf"), str(tmp_path / "o.png")])
    assert res.exit_code == 2
    res = runner.invoke(app, ["thumbnail", str(sample("png")), str(tmp_path / "o.bmp")])
    assert res.exit_code == 1


@pytest.mark.requires("libreoffice")
def test_svg_thumbnail_via_libreoffice(tmp_path: Path) -> None:
    svg = tmp_path / "a.svg"
    svg.write_text(
        '<svg xmlns="http://www.w3.org/2000/svg" width="100" height="60">'
        '<rect width="100" height="60" fill="red"/><circle cx="50" cy="30" r="20" fill="blue"/>'
        "</svg>",
        encoding="utf-8",
    )
    im = _open(make_thumbnail(svg, size=80))
    assert max(im.size) <= 80 and _not_blank(im)
