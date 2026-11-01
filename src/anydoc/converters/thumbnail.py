"""Thumbnail / preview image generation for every supported format."""

from __future__ import annotations

import io
import shutil
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from anydoc.converters.office_pdf import OFFICE_PDF_SOURCES, make_office_to_pdf
from anydoc.core.detect import detect
from anydoc.core.errors import ExternalToolMissingError, UnsupportedFormatError
from anydoc.external import convert_with_libreoffice
from anydoc.external.tools import temp_workdir
from anydoc.formats.dwg import DwgHandler
from anydoc.formats.dxf import DxfDocument, DxfHandler
from anydoc.formats.image import ImageDocument, SvgHandler
from anydoc.formats.pdf import PdfHandler

THUMBNAIL_FORMATS: tuple[str, ...] = ("png", "jpeg", "webp")
"""Output image formats supported by :func:`make_thumbnail`."""

_RASTER = ("png", "jpeg", "gif", "bmp", "webp", "tiff")
_ALIASES = {"jpg": "jpeg"}
_PIL = {"png": "PNG", "jpeg": "JPEG", "webp": "WEBP"}
_FONT_CANDIDATES = (
    "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "C:/Windows/Fonts/msjh.ttc",
    "/System/Library/Fonts/PingFang.ttc",
)


def _flatten(image: Image.Image) -> Image.Image:
    if image.mode in ("RGBA", "LA", "P", "PA"):
        rgba = image.convert("RGBA")
        base = Image.new("RGB", rgba.size, "white")
        base.paste(rgba, mask=rgba.getchannel("A"))
        return base
    return image if image.mode in ("RGB", "L") else image.convert("RGB")


def _from_png(data: bytes) -> Image.Image:
    with Image.open(io.BytesIO(data)) as im:
        return _flatten(im.copy())


def _pdf_page(path: Path, page: int, size: int) -> Image.Image:
    doc = PdfHandler().open(path)
    try:
        if not 0 <= page < len(doc.parts()):
            raise IndexError(f"No such page: {page}")
        # Render near the final size so large pages are not rasterised in full.
        info = doc.pages()[page]
        width, height = info.width, info.height
        scale = max(0.1, min(4.0, size / max(width, height, 1.0) * 2))
        return _from_png(doc.render_page(page, scale=scale))
    finally:
        doc.close()


def _font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    for candidate in _FONT_CANDIDATES:
        if Path(candidate).is_file():
            try:
                return ImageFont.truetype(candidate, size)
            except OSError:
                continue
    return ImageFont.load_default(size)


def _text_preview(text: str, size: int) -> Image.Image:
    """Draw the first lines of ``text`` on a white page."""
    side = max(size * 2, 200)
    page = Image.new("RGB", (side, side), "white")
    draw = ImageDraw.Draw(page)
    font = _font(max(10, side // 18))
    line_h = int(font.size * 1.3) if hasattr(font, "size") else 14
    margin = side // 14
    y = margin
    chars = max(8, (side - 2 * margin) // max(1, int(getattr(font, "size", 10) * 0.9)))
    for raw in text.splitlines() or [""]:
        for i in range(0, max(len(raw), 1), chars):
            if y + line_h > side - margin:
                return page
            draw.text((margin, y), raw[i : i + chars], fill="black", font=font)
            y += line_h
    return page


def _office_pdf_to_image(path: Path, fmt: str, page: int, size: int) -> Image.Image:
    with temp_workdir("anydoc-thumb-") as work:
        pdf = work / "preview.pdf"
        make_office_to_pdf(fmt).func(path, pdf)
        return _pdf_page(pdf, page, size)


def _svg_to_image(path: Path) -> Image.Image:
    with temp_workdir("anydoc-thumb-svg-") as work:
        staged = work / "input.svg"
        shutil.copyfile(path, staged)
        out = convert_with_libreoffice(staged, "png", work)
        return _from_png(out.read_bytes())


def _dxf_preview(doc: DxfDocument, page: int, size: int, *, close: bool = True) -> Image.Image:
    try:
        names = doc.layout_names()
        layout = names[page] if 0 <= page < len(names) else "Model"
        return _from_png(doc.render("png", layout=layout, dpi=max(72.0, size / 2)))
    finally:
        if close:
            doc.close()


def render_preview(path: str | Path, *, size: int = 256, page: int = 0) -> Image.Image:
    """Return a full-quality preview image of ``path`` (``page``/slide/layout index, 0-based).

    Raises:
        UnsupportedFormatError: if the format has no preview.
        ExternalToolMissingError: if a needed external tool (LibreOffice, DWG converter) is absent.
    """
    p = Path(path)
    fmt = detect(p).format
    if fmt == "pdf":
        return _pdf_page(p, page, size)
    if fmt in _RASTER:
        doc = ImageDocument(p.read_bytes(), path=p)
        try:
            return _flatten(doc.frame(page).copy())
        finally:
            doc.close()
    if fmt == "svg":
        SvgHandler().open(p).close()  # validates the document
        return _svg_to_image(p)
    if fmt == "dxf":
        return _dxf_preview(DxfHandler().open(p), page, size)
    if fmt == "dwg":
        dwg = DwgHandler().open(p)
        try:
            if dwg.dxf is None:
                raise ExternalToolMissingError(
                    "DWG thumbnails need ODA File Converter or LibreDWG to convert to DXF"
                )
            return _dxf_preview(dwg.dxf, page, size, close=False)
        finally:
            dwg.close()
    if fmt in OFFICE_PDF_SOURCES:
        return _office_pdf_to_image(p, fmt, page, size)
    from anydoc import open as open_document

    try:
        doc2 = open_document(p)
    except UnsupportedFormatError as exc:
        raise UnsupportedFormatError(f"No thumbnail available for format: {fmt}") from exc
    try:
        return _text_preview(doc2.text(), size)
    finally:
        doc2.close()


def make_thumbnail(
    path: str | Path,
    out: str | Path | None = None,
    *,
    size: int = 256,
    fmt: str | None = None,
    page: int = 0,
) -> bytes:
    """Create a thumbnail whose longest side is at most ``size`` pixels.

    PDF/Office/ODF/RTF use the ``page``-th page or slide (Office formats need LibreOffice),
    DXF/DWG the ``page``-th layout, multi-frame images the ``page``-th frame, and text-like
    formats a rendering of their first lines. The output format is taken from ``fmt`` or
    ``out``'s extension (default PNG). Returns the encoded bytes and writes them to ``out``.
    """
    if size <= 0:
        raise ValueError("size must be positive")
    if fmt is None:
        fmt = Path(out).suffix.lstrip(".") if out is not None and Path(out).suffix else "png"
    fmt = _ALIASES.get(fmt.lower(), fmt.lower())
    if fmt not in _PIL:
        raise ValueError(
            f"Unsupported thumbnail format: {fmt} (expected one of {THUMBNAIL_FORMATS})"
        )
    image = render_preview(path, size=size, page=page)
    image = _flatten(image)
    image.thumbnail((size, size), Image.Resampling.LANCZOS)
    buf = io.BytesIO()
    image.save(buf, format=_PIL[fmt])
    data = buf.getvalue()
    if out is not None:
        dst = Path(out)
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_bytes(data)
    return data
