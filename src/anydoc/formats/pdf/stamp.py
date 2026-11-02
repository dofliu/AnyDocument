"""Page stamping for PDFs: text/image watermarks, headers/footers and page numbers.

Every function takes and returns PDF ``bytes`` (like :mod:`anydoc.formats.pdf.pages`).
Overlays are drawn with reportlab in the *visible* coordinate frame of each page (so rotated
pages stamp correctly) and merged with pypdf. CJK text is supported by embedding a system
CJK TrueType font when one is available (``font_path`` overrides the search), falling back
to reportlab's built-in ``STSong-Light`` CID font.
"""

from __future__ import annotations

import io
import math
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any

from pypdf import PageObject, PdfWriter, Transformation

from anydoc.formats.pdf.pages import _check, _dump, _reader

__all__ = [
    "add_header_footer",
    "add_image_watermark",
    "add_page_numbers",
    "add_text_watermark",
    "find_cjk_font",
]

Color = str | tuple[float, float, float]
Slots = str | tuple[str, str, str]

_FONT_CANDIDATES = (
    "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/noto-cjk/NotoSansCJK-Regular.ttc",
    "C:/Windows/Fonts/msjh.ttc",
    "C:/Windows/Fonts/mingliu.ttc",
    "/System/Library/Fonts/PingFang.ttc",
    "/System/Library/Fonts/STHeiti Light.ttc",
)
_POSITIONS = {
    "top-left",
    "top-center",
    "top-right",
    "bottom-left",
    "bottom-center",
    "bottom-right",
}


def find_cjk_font() -> Path | None:
    """Return the path of an installed CJK-capable TrueType font, or ``None``."""
    for cand in _FONT_CANDIDATES:
        if Path(cand).is_file():
            return Path(cand)
    return None


def _parse_color(color: Color) -> tuple[float, float, float]:
    if isinstance(color, str):
        h = color.strip().lstrip("#")
        if len(h) != 6:
            raise ValueError(f"Invalid color: {color!r} (expected #RRGGBB)")
        try:
            return (int(h[0:2], 16) / 255, int(h[2:4], 16) / 255, int(h[4:6], 16) / 255)
        except ValueError:
            raise ValueError(f"Invalid color: {color!r} (expected #RRGGBB)") from None
    if len(color) != 3 or not all(0 <= c <= 1 for c in color):
        raise ValueError("color tuple must be three floats in 0..1")
    return color


def _register_font(text: str, font_path: Path | None) -> str:
    """Register (once) and return a reportlab font name able to draw ``text``."""
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.cidfonts import UnicodeCIDFont
    from reportlab.pdfbase.ttfonts import TTFont

    path = font_path
    if path is None and not text.isascii():
        path = find_cjk_font()
    if path is not None:
        name = f"anydoc-{path.name}"
        if name not in pdfmetrics.getRegisteredFontNames():
            try:
                pdfmetrics.registerFont(TTFont(name, str(path)))
            except Exception as exc:
                raise ValueError(f"Cannot load font {path}: {exc}") from exc
        return name
    if text.isascii():
        return "Helvetica"
    if "STSong-Light" not in pdfmetrics.getRegisteredFontNames():
        pdfmetrics.registerFont(UnicodeCIDFont("STSong-Light"))
    return "STSong-Light"


def _stamp(
    data: bytes,
    pages: Iterable[int] | None,
    draw: Callable[[Any, float, float, int, int], None],
    *,
    under: bool = False,
    password: str = "",
) -> bytes:
    """Draw ``draw(canvas, width, height, index, page_count)`` onto the selected pages."""
    from reportlab.pdfgen import canvas

    reader = _reader(data, password)
    n = len(reader.pages)
    targets = set(range(n) if pages is None else _check(pages, n))
    writer = PdfWriter()
    for i, page in enumerate(reader.pages):
        added = writer.add_page(page)
        if i not in targets:
            continue
        box = added.mediabox
        w, h = float(box.width), float(box.height)
        x0, y0 = float(box.left), float(box.bottom)
        rot = int(added.rotation) % 360
        vw, vh = (h, w) if rot in (90, 270) else (w, h)
        buf = io.BytesIO()
        c = canvas.Canvas(buf, pagesize=(vw, vh))
        draw(c, vw, vh, i, n)
        c.showPage()
        c.save()
        overlay = _reader(buf.getvalue()).pages[0]
        # visible frame -> unrotated user space (page rotation is clockwise)
        ctm = {
            0: (1, 0, 0, 1, 0, 0),
            90: (0, 1, -1, 0, w, 0),
            180: (-1, 0, 0, -1, w, h),
            270: (0, -1, 1, 0, 0, h),
        }[rot]
        tr = Transformation(ctm=(ctm[0], ctm[1], ctm[2], ctm[3], ctm[4] + x0, ctm[5] + y0))
        _merge(added, overlay, tr, under)
    return _dump(writer)


def _merge(page: PageObject, overlay: PageObject, tr: Transformation, under: bool) -> None:
    page.merge_transformed_page(overlay, tr, over=not under)


def add_text_watermark(
    data: bytes,
    text: str,
    *,
    pages: Iterable[int] | None = None,
    font_size: float = 48,
    color: Color = "#808080",
    opacity: float = 0.3,
    angle: float = 45,
    tile: bool = False,
    under: bool = False,
    font_path: Path | None = None,
    password: str = "",
) -> bytes:
    """Stamp a (possibly rotated, translucent) text watermark centred on each page.

    Args:
        data: Source PDF bytes.
        text: Watermark text; Chinese is supported.
        pages: 0-based pages to stamp (``None`` = all).
        font_size: Font size in points.
        color: ``#RRGGBB`` or an ``(r, g, b)`` tuple of floats in 0..1.
        opacity: 0 (invisible) .. 1 (opaque).
        angle: Counter-clockwise rotation in degrees.
        tile: Repeat the text in a grid over the page instead of a single centred stamp.
        under: Put the watermark beneath the page content.
        font_path: TrueType font file; by default a system CJK font for non-ASCII text.
    """
    if not text:
        raise ValueError("Watermark text must not be empty")
    if font_size <= 0:
        raise ValueError("font_size must be positive")
    if not 0 <= opacity <= 1:
        raise ValueError("opacity must be between 0 and 1")
    rgb = _parse_color(color)
    font = _register_font(text, font_path)

    def draw(c: Any, w: float, h: float, _i: int, _n: int) -> None:
        from reportlab.pdfbase.pdfmetrics import stringWidth

        c.setFillColorRGB(*rgb)
        c.setFillAlpha(opacity)
        c.setFont(font, font_size)
        tw = stringWidth(text, font, font_size)
        if not tile:
            c.saveState()
            c.translate(w / 2, h / 2)
            c.rotate(angle)
            c.drawCentredString(0, -font_size * 0.35, text)
            c.restoreState()
            return
        step_x, step_y = tw * 1.6, font_size * 4
        reach = math.hypot(w, h)
        c.saveState()
        c.translate(w / 2, h / 2)
        c.rotate(angle)
        rows = int(reach / step_y) + 1
        cols = int(reach / step_x) + 1
        for r in range(-rows, rows + 1):
            for k in range(-cols, cols + 1):
                c.drawCentredString(k * step_x, r * step_y, text)
        c.restoreState()

    return _stamp(data, pages, draw, under=under, password=password)


def add_image_watermark(
    data: bytes,
    image: bytes | Path,
    *,
    pages: Iterable[int] | None = None,
    scale: float = 0.5,
    opacity: float = 0.3,
    angle: float = 0,
    position: str = "center",
    under: bool = False,
    password: str = "",
) -> bytes:
    """Stamp an image (PNG with alpha is honoured) as a watermark.

    Args:
        data: Source PDF bytes.
        image: Image bytes or file path (any format Pillow reads).
        pages: 0-based pages to stamp (``None`` = all).
        scale: Image width as a fraction of the page width (aspect ratio preserved).
        opacity: 0 .. 1.
        angle: Counter-clockwise rotation in degrees.
        position: ``"center"`` or one of ``top-left`` ... ``bottom-right``.
        under: Put the watermark beneath the page content.
    """
    from PIL import Image
    from reportlab.lib.utils import ImageReader

    if not 0 < scale <= 1:
        raise ValueError("scale must be in (0, 1]")
    if not 0 <= opacity <= 1:
        raise ValueError("opacity must be between 0 and 1")
    if position != "center" and position not in _POSITIONS:
        raise ValueError(f"Unknown position: {position!r}")
    blob = image.read_bytes() if isinstance(image, Path) else image
    try:
        with Image.open(io.BytesIO(blob)) as im:
            im.load()
            pil = im.convert("RGBA")
    except Exception as exc:
        raise ValueError(f"Cannot read watermark image: {exc}") from exc
    png = io.BytesIO()
    pil.save(png, format="PNG")
    reader_img = ImageReader(io.BytesIO(png.getvalue()))
    iw, ih = pil.size
    margin = 24.0

    def draw(c: Any, w: float, h: float, _i: int, _n: int) -> None:
        dw = w * scale
        dh = dw * ih / iw
        if position == "center":
            cx, cy = w / 2, h / 2
        else:
            v, hz = position.split("-")
            cx = {"left": margin + dw / 2, "center": w / 2, "right": w - margin - dw / 2}[hz]
            cy = {"top": h - margin - dh / 2, "bottom": margin + dh / 2}[v]
        c.setFillAlpha(opacity)
        c.saveState()
        c.translate(cx, cy)
        c.rotate(angle)
        c.drawImage(reader_img, -dw / 2, -dh / 2, dw, dh, mask="auto")
        c.restoreState()

    return _stamp(data, pages, draw, under=under, password=password)


def _slots(value: Slots | None, align: str) -> tuple[str, str, str]:
    if value is None:
        return ("", "", "")
    if isinstance(value, str):
        idx = {"left": 0, "center": 1, "right": 2}[align]
        out = ["", "", ""]
        out[idx] = value
        return (out[0], out[1], out[2])
    if len(value) != 3:
        raise ValueError("header/footer tuple must be (left, center, right)")
    return value


def _fill(text: str, page: int, total: int) -> str:
    return text.replace("{page}", str(page)).replace("{total}", str(total))


def add_header_footer(
    data: bytes,
    *,
    header: Slots | None = None,
    footer: Slots | None = None,
    pages: Iterable[int] | None = None,
    align: str = "center",
    font_size: float = 10,
    color: Color = "#000000",
    margin: float = 36,
    start: int = 1,
    font_path: Path | None = None,
    password: str = "",
) -> bytes:
    """Add header and/or footer text to each selected page.

    ``header``/``footer`` are either a string (placed according to ``align``) or a
    ``(left, center, right)`` tuple. ``{page}`` expands to ``index + start`` (so the first
    page is ``start``) and ``{total}`` to the document's page count.

    Args:
        data: Source PDF bytes.
        header: Header text or slots; ``None`` for none.
        footer: Footer text or slots; ``None`` for none.
        pages: 0-based pages to stamp (``None`` = all).
        align: ``left``/``center``/``right`` for plain-string header/footer.
        font_size: Font size in points.
        color: ``#RRGGBB`` or ``(r, g, b)`` floats.
        margin: Distance from the page edge in points.
        start: Number shown for the first page by ``{page}``.
        font_path: TrueType font file; default is a system CJK font for non-ASCII text.
    """
    if align not in ("left", "center", "right"):
        raise ValueError(f"Unknown align: {align!r}")
    if header is None and footer is None:
        raise ValueError("Give a header and/or a footer")
    if font_size <= 0:
        raise ValueError("font_size must be positive")
    rgb = _parse_color(color)
    hs, fs = _slots(header, align), _slots(footer, align)
    font = _register_font("".join(hs + fs), font_path)

    def draw(c: Any, w: float, h: float, i: int, n: int) -> None:
        c.setFillColorRGB(*rgb)
        c.setFont(font, font_size)
        xs = (margin, w / 2, w - margin)
        for slots, y in ((hs, h - margin - font_size * 0.8), (fs, margin)):
            for k, raw in enumerate(slots):
                if not raw:
                    continue
                text = _fill(raw, i + start, n)
                if k == 0:
                    c.drawString(xs[0], y, text)
                elif k == 1:
                    c.drawCentredString(xs[1], y, text)
                else:
                    c.drawRightString(xs[2], y, text)

    return _stamp(data, pages, draw, password=password)


def add_page_numbers(
    data: bytes,
    *,
    fmt: str = "{page} / {total}",
    position: str = "bottom-center",
    pages: Iterable[int] | None = None,
    start: int = 1,
    font_size: float = 10,
    color: Color = "#000000",
    margin: float = 36,
    font_path: Path | None = None,
    password: str = "",
) -> bytes:
    """Add page numbers (``fmt`` with ``{page}``/``{total}``) at one of six positions."""
    if position not in _POSITIONS:
        raise ValueError(f"Unknown position: {position!r}")
    where, align = position.split("-")
    kwargs: dict[str, Any] = {
        "pages": pages,
        "align": align,
        "font_size": font_size,
        "color": color,
        "margin": margin,
        "start": start,
        "font_path": font_path,
        "password": password,
    }
    if where == "top":
        return add_header_footer(data, header=fmt, **kwargs)
    return add_header_footer(data, footer=fmt, **kwargs)
