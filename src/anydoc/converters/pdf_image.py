"""PDF <-> raster image conversion (pypdfium2 rendering and Pillow PDF writing)."""

from __future__ import annotations

import io
from collections.abc import Sequence
from pathlib import Path

from PIL import Image, ImageSequence

from anydoc.core.convert import ConversionGraph, Converter, graph
from anydoc.core.errors import CorruptFileError
from anydoc.formats.pdf import PdfHandler

IMAGE_FORMATS: tuple[str, ...] = ("png", "jpeg", "gif", "bmp", "webp", "tiff")
"""Raster format ids that can be combined into a PDF."""

_EXTENSIONS = {
    "png": "png",
    "jpeg": "jpg",
    "gif": "gif",
    "bmp": "bmp",
    "webp": "webp",
    "tiff": "tiff",
}
_PIL = {
    "png": "PNG",
    "jpeg": "JPEG",
    "gif": "GIF",
    "bmp": "BMP",
    "webp": "WEBP",
    "tiff": "TIFF",
}
_ALIASES = {"jpg": "jpeg", "tif": "tiff"}


def _norm(fmt: str) -> str:
    fmt = fmt.lower().lstrip(".")
    fmt = _ALIASES.get(fmt, fmt)
    if fmt not in _PIL:
        raise ValueError(f"Unsupported image format: {fmt}")
    return fmt


def _render_pages(src: Path, dpi: float, pages: Sequence[int] | None) -> list[Image.Image]:
    doc = PdfHandler().open(src)
    try:
        count = len(doc.parts())
        indexes = list(range(count)) if pages is None else list(pages)
        images = []
        for i in indexes:
            png = doc.render_page(i, dpi=dpi)
            with Image.open(io.BytesIO(png)) as im:
                images.append(im.copy())
        return images
    finally:
        doc.close()


def _encode(image: Image.Image, fmt: str) -> bytes:
    if fmt == "jpeg" and image.mode not in ("RGB", "L"):
        image = image.convert("RGB")
    buf = io.BytesIO()
    image.save(buf, format=_PIL[fmt])
    return buf.getvalue()


def pdf_to_images(
    src: str | Path,
    out_dir: str | Path,
    fmt: str = "png",
    *,
    dpi: float = 144.0,
    pages: Sequence[int] | None = None,
    stem: str | None = None,
) -> list[Path]:
    """Render every page (or the 0-based ``pages``) of a PDF to image files.

    Files are named ``<stem>-<page number, 1-based, zero padded>.<ext>`` inside ``out_dir``.
    Returns the written paths in page order.
    """
    fmt = _norm(fmt)
    src_p = Path(src)
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    base = stem or src_p.stem
    indexes = pages
    images = _render_pages(src_p, dpi, indexes)
    numbers = list(range(len(images))) if indexes is None else list(indexes)
    width = max(3, len(str(max(numbers, default=0) + 1)))
    written: list[Path] = []
    for n, image in zip(numbers, images, strict=True):
        target = out / f"{base}-{n + 1:0{width}d}.{_EXTENSIONS[fmt]}"
        target.write_bytes(_encode(image, fmt))
        written.append(target)
    return written


def _frames(path: Path) -> list[Image.Image]:
    try:
        with Image.open(path) as im:
            return [_flatten(f.copy()) for f in ImageSequence.Iterator(im)]
    except OSError as exc:
        raise CorruptFileError(f"Cannot read image {path}: {exc}") from exc


def _flatten(frame: Image.Image) -> Image.Image:
    """Return an RGB/L frame suitable for PDF output (transparency over white)."""
    if frame.mode in ("RGBA", "LA", "P", "PA"):
        rgba = frame.convert("RGBA")
        base = Image.new("RGB", rgba.size, "white")
        base.paste(rgba, mask=rgba.getchannel("A"))
        return base
    if frame.mode in ("RGB", "L"):
        return frame
    return frame.convert("RGB")


def images_to_pdf(sources: Sequence[str | Path], dst: str | Path, *, dpi: float = 72.0) -> Path:
    """Combine one or more images into a single PDF (multi-frame images add one page per frame).

    Each page is sized from the image pixels at ``dpi``.
    """
    if not sources:
        raise ValueError("images_to_pdf needs at least one source image")
    if dpi <= 0:
        raise ValueError("dpi must be positive")
    frames: list[Image.Image] = []
    for s in sources:
        frames.extend(_frames(Path(s)))
    dst_p = Path(dst)
    dst_p.parent.mkdir(parents=True, exist_ok=True)
    frames[0].save(dst_p, format="PDF", save_all=True, append_images=frames[1:], resolution=dpi)
    return dst_p


def _pdf_to_image(fmt: str) -> Converter:
    def func(src: Path, dst: Path) -> None:
        # A graph edge yields a single file: the first page. Use pdf_to_images for all pages.
        image = _render_pages(src, 144.0, [0])[0]
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_bytes(_encode(image, fmt))

    return Converter("pdf", fmt, func, cost=1.0, name=f"pdf->{fmt} (first page)")


def _image_to_pdf(fmt: str) -> Converter:
    def func(src: Path, dst: Path) -> None:
        images_to_pdf([src], dst)

    return Converter(fmt, "pdf", func, cost=1.0, name=f"{fmt}->pdf (pillow)")


def register_pdf_image(target: ConversionGraph | None = None) -> None:
    """Register PDF <-> image converters on ``target`` (default: global graph)."""
    g = target if target is not None else graph
    for fmt in IMAGE_FORMATS:
        g.add(_pdf_to_image(fmt))
        g.add(_image_to_pdf(fmt))
