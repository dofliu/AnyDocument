"""Tests for the image handlers (T022)."""

from __future__ import annotations

import io
import json
from collections.abc import Callable
from pathlib import Path

import pytest
from PIL import Image

import anydoc
from anydoc.core.errors import CorruptFileError, OperationNotSupportedError
from anydoc.core.model import Capability, Document
from anydoc.core.registry import registry
from anydoc.formats.image import (
    BmpHandler,
    GifHandler,
    ImageDocument,
    JpegHandler,
    PngHandler,
    SvgHandler,
    TiffHandler,
    WebpHandler,
)

Sample = Callable[[str], Path]

HANDLERS = [PngHandler, JpegHandler, GifHandler, BmpHandler, WebpHandler, TiffHandler, SvgHandler]
SVG = (
    '<svg xmlns="http://www.w3.org/2000/svg" width="10cm" height="5cm" viewBox="0 0 100 50">'
    "<title>圖示</title><rect width='10' height='10'/>"
    "<text x='1' y='2'>你好 <tspan>World</tspan></text></svg>"
)


@pytest.fixture(autouse=True)
def _handlers() -> None:
    for h in HANDLERS:
        registry.register(h, replace=True)


def _encode(fmt: str, size: tuple[int, int] = (40, 30), mode: str = "RGB", **kw: object) -> bytes:
    buf = io.BytesIO()
    Image.new(mode, size, "red").save(buf, format=fmt, **kw)  # type: ignore[arg-type]
    return buf.getvalue()


def _multi_tiff() -> bytes:
    frames = [Image.new("RGB", (20 + i, 10), c) for i, c in enumerate(("red", "green", "blue"))]
    buf = io.BytesIO()
    frames[0].save(buf, format="TIFF", save_all=True, append_images=frames[1:])
    return buf.getvalue()


def _exif_jpeg() -> bytes:
    exif = Image.Exif()
    exif[0x010E] = "Test desc"
    exif[0x013B] = "Dof"
    exif[0x0132] = "2024:05:06 07:08:09"
    exif[0x010F] = "ACME"
    buf = io.BytesIO()
    Image.new("RGB", (16, 8), "white").save(buf, format="JPEG", exif=exif)
    return buf.getvalue()


# -- detection and dispatch ----------------------------------------------------


@pytest.mark.parametrize(
    ("fmt", "pil", "ext"),
    [
        ("png", "PNG", "png"),
        ("jpeg", "JPEG", "jpg"),
        ("gif", "GIF", "gif"),
        ("bmp", "BMP", "bmp"),
        ("webp", "WEBP", "webp"),
        ("tiff", "TIFF", "tif"),
    ],
)
def test_open_each_raster_format(tmp_path: Path, fmt: str, pil: str, ext: str) -> None:
    f = tmp_path / f"a.{ext}"
    f.write_bytes(_encode(pil))
    doc = anydoc.open(f)
    assert isinstance(doc, ImageDocument)
    assert doc.format == fmt
    assert doc.size == (40, 30)
    assert doc.metadata.extra["width"] == 40
    assert doc.metadata.mime.startswith("image/")
    assert isinstance(doc, Document)
    assert doc.text() == ""


def test_generated_samples(sample: Sample) -> None:
    png = anydoc.open(sample("png"))
    assert (png.format, png.metadata.extra["width"], png.metadata.extra["height"]) == (
        "png",
        64,
        48,
    )
    jpg = anydoc.open(sample("jpg"))
    assert jpg.format == "jpeg"
    json.dumps(jpg.metadata.to_dict())


def test_content_wins_over_extension(tmp_path: Path) -> None:
    f = tmp_path / "wrong.jpg"
    f.write_bytes(_encode("PNG"))
    assert anydoc.open(f).format == "png"


def test_capabilities_and_sniff() -> None:
    assert Capability.CONVERT in PngHandler.capabilities
    assert PngHandler.sniff(b"\x89PNG\r\n\x1a\n", None) == 1.0
    assert PngHandler.sniff(b"zzzz", Path("x.png")) == 0.4
    assert PngHandler.sniff(b"zzzz", Path("x.txt")) == 0.0
    assert WebpHandler.sniff(b"RIFF\0\0\0\0WEBP", None) == 1.0
    assert WebpHandler.sniff(b"RIFF\0\0\0\0WAVE", None) == 0.0
    assert SvgHandler.sniff(b"<svg ", None) == 1.0
    assert SvgHandler.sniff(b"hello", Path("a.svg")) == 0.5


def test_corrupt_image_raises(tmp_path: Path) -> None:
    f = tmp_path / "bad.png"
    f.write_bytes(b"\x89PNG\r\n\x1a\n" + b"garbage")
    with pytest.raises(CorruptFileError):
        anydoc.open(f)
    with pytest.raises(CorruptFileError):
        PngHandler().open(tmp_path / "missing.png")


# -- metadata, EXIF, frames ----------------------------------------------------


def test_exif_metadata() -> None:
    doc = JpegHandler().open(_exif_jpeg())
    exif = doc.exif()
    assert exif["Make"] == "ACME"
    assert doc.metadata.title == "Test desc"
    assert doc.metadata.author == "Dof"
    assert doc.metadata.created is not None
    assert (doc.metadata.created.year, doc.metadata.created.hour) == (2024, 7)
    json.dumps(doc.metadata.to_dict(), ensure_ascii=False)


def test_no_exif_is_empty() -> None:
    doc = PngHandler().open(_encode("PNG"))
    assert doc.exif() == {}
    assert doc.metadata.title is None and doc.metadata.created is None


def test_dpi_reported() -> None:
    doc = PngHandler().open(_encode("PNG", dpi=(300, 300)))
    assert doc.metadata.extra["dpi"] == [300.0, 300.0]


def test_multipage_tiff_parts() -> None:
    doc = TiffHandler().open(_multi_tiff())
    assert doc.frame_count == 3
    assert doc.metadata.page_count == 3
    assert [p.size for p in doc.parts()] == [(20.0, 10.0), (21.0, 10.0), (22.0, 10.0)]
    assert doc.frame(2).size == (22, 10)
    with pytest.raises(IndexError):
        doc.frame(3)


def test_animated_gif_frames() -> None:
    frames = [Image.new("RGB", (8, 8), c) for c in ("red", "blue")]
    buf = io.BytesIO()
    frames[0].save(buf, format="GIF", save_all=True, append_images=frames[1:])
    assert GifHandler().open(buf.getvalue()).frame_count == 2


# -- transformations and conversion --------------------------------------------


def test_resize_variants() -> None:
    doc = PngHandler().open(_encode("PNG"))
    assert doc.resize((20, 10)).size == (20, 10)
    assert doc.resize(scale=0.5).size == (20, 15)
    assert doc.resize((20, 20), keep_aspect=True).size == (20, 15)
    assert doc.size == (40, 30)  # original untouched
    with pytest.raises(ValueError):
        doc.resize()
    with pytest.raises(ValueError):
        doc.resize(scale=0)
    with pytest.raises(ValueError):
        doc.resize((0, 5))


def test_resize_keeps_all_tiff_frames() -> None:
    doc = TiffHandler().open(_multi_tiff()).resize(scale=2)
    assert doc.frame_count == 3 and doc.size == (40, 20)
    assert doc.metadata.page_count == 3


def test_rotate() -> None:
    doc = PngHandler().open(_encode("PNG"))
    assert doc.rotate(90).size == (30, 40)
    assert doc.rotate(90, expand=False).size == (40, 30)


@pytest.mark.parametrize("target", ["png", "jpeg", "jpg", "gif", "bmp", "webp", "tiff", "tif"])
def test_convert_roundtrip(tmp_path: Path, target: str) -> None:
    doc = PngHandler().open(_encode("PNG", mode="RGBA"))
    out = tmp_path / f"o.{target}"
    data = doc.convert(target, out)
    assert out.read_bytes() == data
    again = anydoc.open(out)
    assert isinstance(again, ImageDocument)
    assert again.size == (40, 30)


def test_convert_jpeg_flattens_alpha() -> None:
    img = Image.new("RGBA", (4, 4), (255, 0, 0, 0))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    data = PngHandler().open(buf.getvalue()).to_bytes("jpeg")
    px = Image.open(io.BytesIO(data)).convert("RGB").getpixel((1, 1))
    assert isinstance(px, tuple) and min(px) > 240  # transparent → white background


def test_convert_palette_and_cmyk_to_bmp_jpeg() -> None:
    pal = PngHandler().open(_encode("PNG", mode="P"))
    assert pal.to_bytes("jpeg")
    gray = PngHandler().open(_encode("PNG", mode="LA"))
    assert gray.to_bytes("bmp")


def test_convert_multipage_tiff_preserved_and_flattened() -> None:
    doc = TiffHandler().open(_multi_tiff())
    assert TiffHandler().open(doc.to_bytes("tiff")).frame_count == 3
    assert PngHandler().open(doc.to_bytes("png")).frame_count == 1


def test_convert_unsupported_target() -> None:
    doc = PngHandler().open(_encode("PNG"))
    with pytest.raises(OperationNotSupportedError):
        doc.to_bytes("svg")


def test_save_follows_suffix_and_source(tmp_path: Path) -> None:
    f = tmp_path / "a.png"
    f.write_bytes(_encode("PNG"))
    doc = anydoc.open(f)
    assert doc.save() == f
    assert doc.save(tmp_path / "b.jpg").read_bytes()[:3] == b"\xff\xd8\xff"
    assert doc.save(tmp_path / "c.dat").read_bytes()[:4] == b"\x89PNG"
    with pytest.raises(ValueError):
        PngHandler().open(_encode("PNG")).save()
    doc.close()


# -- SVG -----------------------------------------------------------------------


def test_svg_open_and_metadata(tmp_path: Path) -> None:
    f = tmp_path / "a.svg"
    f.write_text(SVG, encoding="utf-8")
    doc = anydoc.open(f)
    assert doc.format == "svg"
    assert doc.metadata.mime == "image/svg+xml"
    assert doc.metadata.title == "圖示"
    assert doc.view_box == (0.0, 0.0, 100.0, 50.0)  # type: ignore[attr-defined]
    assert doc.metadata.extra["width"] == pytest.approx(377.95, abs=0.1)
    assert doc.metadata.extra["elements"]["rect"] == 1
    assert doc.parts()[0].kind == "image"
    assert doc.text() == "你好 World"
    json.dumps(doc.metadata.to_dict(), ensure_ascii=False)


def test_svg_size_from_viewbox_only() -> None:
    doc = SvgHandler().open(b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 30 20"/>')
    assert (doc.width, doc.height) == (30.0, 20.0)
    bare = SvgHandler().open(b"<svg/>")
    assert bare.parts()[0].size is None


def test_svg_save_roundtrip(tmp_path: Path) -> None:
    doc = SvgHandler().open(SVG.encode())
    out = doc.save(tmp_path / "o.svg")
    assert out.read_bytes() == SVG.encode()
    with pytest.raises(ValueError):
        doc.save()
    doc.close()


def test_svg_rejects_bad_input(tmp_path: Path) -> None:
    with pytest.raises(CorruptFileError):
        SvgHandler().open(b"<svg><unclosed></svg>")
    with pytest.raises(CorruptFileError):
        SvgHandler().open(b"<html/>")
    with pytest.raises(CorruptFileError):
        SvgHandler().open(tmp_path / "missing.svg")


def test_svg_blocks_entity_bomb() -> None:
    bomb = b'<!DOCTYPE svg [<!ENTITY a "aaaa"><!ENTITY b "&a;&a;&a;">]><svg>&b;</svg>'
    with pytest.raises(CorruptFileError):
        SvgHandler().open(bomb)


def test_entry_points_register_all_image_handlers() -> None:
    from importlib.metadata import entry_points

    names = {ep.name for ep in entry_points(group="anydoc.formats")}
    assert {"png", "jpeg", "gif", "bmp", "webp", "tiff", "svg"} <= names
