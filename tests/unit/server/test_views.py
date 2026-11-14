"""Unit tests for the read-only document views (T101)."""

from __future__ import annotations

import io
import os
from collections.abc import Callable
from pathlib import Path

import pytest
from PIL import Image

import anydoc
from anydoc.core.errors import (
    CorruptFileError,
    ExternalToolMissingError,
    OperationNotSupportedError,
    UnsupportedFormatError,
)
from anydoc.server import views
from anydoc.server.views import (
    CACHE_DIR_NAME,
    IMAGE_MEDIA_TYPES,
    PartNotFoundError,
    document_info,
    document_parts,
    document_text,
    render_etag,
    render_formats,
    render_info,
    render_part,
    render_unit,
)

Sample = Callable[[str], Path]

SVG = (
    '<svg xmlns="http://www.w3.org/2000/svg" width="100" height="60">'
    '<rect width="100" height="60" fill="navy"/><text x="5" y="30">標題</text></svg>'
)


def _image(data: bytes) -> Image.Image:
    im = Image.open(io.BytesIO(data))
    im.load()
    return im


def _copy(sample: Sample, sample_id: str, tmp_path: Path) -> Path:
    """Copy a sample into its own folder so cache files do not leak between tests."""
    src = sample(sample_id)
    dst = tmp_path / src.name
    dst.write_bytes(src.read_bytes())
    return dst


# -- render_unit / render_formats -------------------------------------------------------


@pytest.mark.parametrize(
    ("format_id", "unit"),
    [
        ("pdf", "page"),
        ("docx", "page"),
        ("xlsx", "page"),
        ("pptx", "page"),
        ("odt", "page"),
        ("dxf", "layout"),
        ("dwg", "layout"),
        ("png", "frame"),
        ("tiff", "frame"),
        ("svg", "image"),
        ("txt", "preview"),
        ("csv", "preview"),
    ],
)
def test_render_unit(format_id: str, unit: str) -> None:
    assert render_unit(format_id) == unit


def test_render_formats() -> None:
    assert render_formats("pdf") == ("png", "jpeg", "webp")
    for fmt in ("svg", "dxf", "dwg"):
        assert render_formats(fmt) == ("png", "jpeg", "webp", "svg")
    assert set(render_formats("dxf")) == set(IMAGE_MEDIA_TYPES)


# -- document_info / document_parts -----------------------------------------------------


def test_document_info_pdf(sample: Sample) -> None:
    info = document_info(sample("pdf"))
    assert info["metadata"]["format"] == "pdf"
    assert info["metadata"]["page_count"] == 2
    assert info["part_count"] == 2
    assert {"read", "extract_text"} <= set(info["capabilities"])
    assert info["capabilities"] == sorted(info["capabilities"])
    assert info["render"] == {"unit": "page", "formats": ["png", "jpeg", "webp"]}


def test_document_info_matches_handler(sample: Sample) -> None:
    for sample_id in ("txt", "xlsx", "dxf"):
        path = sample(sample_id)
        doc = anydoc.open(path)
        try:
            expected = doc.metadata.to_dict()
            count = len(doc.parts())
        finally:
            doc.close()
        info = document_info(path)
        assert info["metadata"] == expected
        assert info["part_count"] == count


def test_document_info_format_override_drives_render_options(sample: Sample) -> None:
    assert document_info(sample("dxf"), "dxf")["render"]["unit"] == "layout"
    assert "svg" in document_info(sample("dxf"))["render"]["formats"]


def test_document_parts(sample: Sample) -> None:
    pages = document_parts(sample("pdf"))
    assert [p["kind"] for p in pages] == ["page", "page"]
    assert [p["index"] for p in pages] == [0, 1]
    assert all(len(p["size"]) == 2 for p in pages)
    sheets = document_parts(sample("xlsx"))
    assert [p["kind"] for p in sheets] == ["sheet", "sheet"]
    assert all(p["name"] for p in sheets)


def test_info_errors(tmp_path: Path) -> None:
    unknown = tmp_path / "blob.bin"
    unknown.write_bytes(bytes(range(256)) * 4)
    with pytest.raises(UnsupportedFormatError):
        document_info(unknown)
    broken = tmp_path / "broken.pdf"
    broken.write_bytes(b"%PDF-1.7\nthis is not a real pdf\n")
    with pytest.raises(CorruptFileError):
        document_parts(broken)


# -- document_text ----------------------------------------------------------------------


def test_document_text_window(tmp_path: Path) -> None:
    src = tmp_path / "a.txt"
    src.write_text("零一二三四五六七八九", encoding="utf-8")
    whole = document_text(src)
    assert (whole.text, whole.offset, whole.length, whole.total) == (
        "零一二三四五六七八九",
        0,
        10,
        10,
    )
    assert whole.truncated is False
    middle = document_text(src, offset=2, limit=3)
    assert (middle.text, middle.offset, middle.length, middle.total) == ("二三四", 2, 3, 10)
    assert middle.truncated is True
    tail = document_text(src, offset=7, limit=50)
    assert (tail.text, tail.length, tail.truncated) == ("七八九", 3, False)
    beyond = document_text(src, offset=99)
    assert (beyond.text, beyond.offset, beyond.length, beyond.truncated) == ("", 10, 0, False)


def test_document_text_validation(tmp_path: Path) -> None:
    src = tmp_path / "a.txt"
    src.write_text("abc", encoding="utf-8")
    with pytest.raises(ValueError, match="offset"):
        document_text(src, offset=-1)
    with pytest.raises(ValueError, match="limit"):
        document_text(src, limit=0)


def test_document_text_matches_extract_text(sample: Sample) -> None:
    for sample_id in ("pdf", "docx", "csv-big5"):
        assert document_text(sample(sample_id)).text == anydoc.extract_text(sample(sample_id))


# -- render_part ------------------------------------------------------------------------


@pytest.mark.parametrize(("fmt", "pil"), [("png", "PNG"), ("jpeg", "JPEG"), ("jpg", "JPEG")])
def test_render_pdf_page(sample: Sample, fmt: str, pil: str) -> None:
    rendered = render_part(sample("pdf"), "pdf", 1, fmt=fmt, size=300)
    image = _image(rendered.data)
    assert image.format == pil
    assert max(image.size) == 300
    assert rendered.media_type == IMAGE_MEDIA_TYPES[rendered.format]
    assert rendered.format == ("jpeg" if fmt == "jpg" else fmt)


def test_render_webp_and_size(sample: Sample) -> None:
    small = _image(render_part(sample("pdf"), "pdf", fmt="WEBP", size=64).data)
    assert small.format == "WEBP"
    assert max(small.size) == 64
    large = _image(render_part(sample("pdf"), "pdf", size=800).data)
    assert max(large.size) == 800


def test_render_pages_differ(sample: Sample) -> None:
    first = render_part(sample("pdf"), "pdf", 0, size=200).data
    second = render_part(sample("pdf"), "pdf", 1, size=200).data
    assert first != second


def test_render_image_is_never_enlarged(sample: Sample) -> None:
    image = _image(render_part(sample("png"), "png", size=1024).data)
    assert image.size == (64, 48)


def test_render_text_preview(sample: Sample) -> None:
    image = _image(render_part(sample("txt"), "txt", size=256).data)
    lo, hi = image.convert("L").getextrema()
    assert hi > lo
    assert max(image.size) <= 256


def test_render_dxf_png_and_svg(sample: Sample) -> None:
    png = render_part(sample("dxf"), "dxf", 0, size=400)
    assert png.media_type == "image/png"
    assert max(_image(png.data).size) <= 400
    for index in (0, 1):
        svg = render_part(sample("dxf"), "dxf", index, fmt="svg")
        assert svg.media_type == "image/svg+xml"
        assert b"<svg" in svg.data[:400]


def test_render_svg_source_is_passed_through(tmp_path: Path) -> None:
    src = tmp_path / "a.svg"
    src.write_text(SVG, encoding="utf-8")
    rendered = render_part(src, "svg", fmt="svg")
    assert rendered.data == SVG.encode()
    assert rendered.media_type == "image/svg+xml"
    with pytest.raises(PartNotFoundError):
        render_part(src, "svg", 1, fmt="svg")
    info = render_info(src, "svg")
    assert (info.unit, info.count, info.formats) == ("image", 1, ("png", "jpeg", "webp", "svg"))


def test_render_part_out_of_range(sample: Sample) -> None:
    with pytest.raises(PartNotFoundError) as excinfo:
        render_part(sample("pdf"), "pdf", 2)
    assert (excinfo.value.part, excinfo.value.count) == (2, 2)
    assert "2" in str(excinfo.value)
    with pytest.raises(PartNotFoundError):
        render_part(sample("pdf"), "pdf", -1)
    with pytest.raises(PartNotFoundError):
        render_part(sample("dxf"), "dxf", 2)
    with pytest.raises(PartNotFoundError):
        render_part(sample("dxf"), "dxf", 5, fmt="svg")
    with pytest.raises(PartNotFoundError):
        render_part(sample("txt"), "txt", 1)
    with pytest.raises(PartNotFoundError):
        render_part(sample("png"), "png", 1)


def test_render_part_invalid_arguments(sample: Sample) -> None:
    with pytest.raises(ValueError, match="format"):
        render_part(sample("pdf"), "pdf", fmt="bmp")
    with pytest.raises(ValueError, match="size"):
        render_part(sample("pdf"), "pdf", size=0)
    with pytest.raises(OperationNotSupportedError, match="svg"):
        render_part(sample("pdf"), "pdf", fmt="svg")
    with pytest.raises(OperationNotSupportedError):
        render_part(sample("txt"), "txt", fmt="svg")


def test_render_info_counts(sample: Sample) -> None:
    assert render_info(sample("pdf"), "pdf").count == 2
    assert render_info(sample("dxf"), "dxf").count == 2
    assert render_info(sample("png"), "png").count == 1
    assert render_info(sample("epub"), "epub").count == 1  # a single text preview
    assert render_info(sample("pdf"), "pdf").unit == "page"


def test_dwg_without_converter(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from anydoc.formats.dwg import DwgDocument

    monkeypatch.setattr(DwgDocument, "dxf", None, raising=False)
    src = tmp_path / "x.dwg"
    src.write_bytes(b"AC1032" + b"\x00" * 100)
    doc = anydoc.open(src)
    try:
        if doc.format != "dwg":  # pragma: no cover - defensive
            pytest.skip("DWG stub not recognised")
        with pytest.raises(ExternalToolMissingError):
            views._open_drawing(doc)
    finally:
        doc.close()


def test_open_drawing_rejects_other_documents(sample: Sample) -> None:
    doc = anydoc.open(sample("txt"))
    try:
        with pytest.raises(OperationNotSupportedError):
            views._open_drawing(doc)
    finally:
        doc.close()


# -- ETag -------------------------------------------------------------------------------


def test_render_etag(tmp_path: Path) -> None:
    src = tmp_path / "a.txt"
    src.write_text("one", encoding="utf-8")
    tag = render_etag(src, 0, "png", 256)
    assert tag.startswith('"') and tag.endswith('"') and len(tag) == 34
    assert render_etag(src, 0, "png", 256) == tag
    assert render_etag(src, 0, "jpg", 256) == render_etag(src, 0, "jpeg", 256)
    others = {
        render_etag(src, 1, "png", 256),
        render_etag(src, 0, "jpeg", 256),
        render_etag(src, 0, "png", 512),
    }
    assert tag not in others and len(others) == 3
    src.write_text("changed content", encoding="utf-8")
    assert render_etag(src, 0, "png", 256) != tag


# -- Office renditions (LibreOffice) ----------------------------------------------------


@pytest.mark.requires("libreoffice")
def test_office_rendition_is_cached(
    sample: Sample, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    src = _copy(sample, "pptx", tmp_path)
    calls: list[str] = []
    real = views.make_office_to_pdf

    def counting(fmt: str):  # type: ignore[no-untyped-def]
        calls.append(fmt)
        return real(fmt)

    monkeypatch.setattr(views, "make_office_to_pdf", counting)
    info = render_info(src, "pptx")
    assert (info.unit, info.count) == ("page", 2)
    first = render_part(src, "pptx", 0, size=200)
    second = render_part(src, "pptx", 1, size=200)
    assert first.data != second.data
    assert max(_image(first.data).size) == 200
    assert calls == ["pptx"]  # converted once, then served from the cache
    cached = list((tmp_path / CACHE_DIR_NAME).iterdir())
    assert len(cached) == 1 and cached[0].suffix == ".pdf"
    with pytest.raises(PartNotFoundError):
        render_part(src, "pptx", 2)

    # A changed document gets a fresh rendition and the stale one is dropped.
    os.utime(src, ns=(1, 1))
    render_part(src, "pptx", 0, size=100)
    assert calls == ["pptx", "pptx"]
    assert len(list((tmp_path / CACHE_DIR_NAME).iterdir())) == 1


@pytest.mark.requires("libreoffice")
def test_office_rendition_custom_cache_dir(sample: Sample, tmp_path: Path) -> None:
    src = _copy(sample, "docx", tmp_path)
    cache = tmp_path / "elsewhere"
    rendered = render_part(src, "docx", size=160, cache_dir=cache)
    assert max(_image(rendered.data).size) == 160
    assert [p.suffix for p in cache.iterdir()] == [".pdf"]
    assert not (tmp_path / CACHE_DIR_NAME).exists()


def test_office_without_libreoffice(
    sample: Sample, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    src = _copy(sample, "docx", tmp_path)
    monkeypatch.setenv("PATH", "")
    monkeypatch.delenv("ANYDOC_LIBREOFFICE", raising=False)
    with pytest.raises(ExternalToolMissingError):
        render_part(src, "docx")
    with pytest.raises(ExternalToolMissingError):
        render_info(src, "docx")
    assert list(tmp_path.rglob("*.pdf")) == []
    assert list(tmp_path.rglob("*.tmp")) == []
