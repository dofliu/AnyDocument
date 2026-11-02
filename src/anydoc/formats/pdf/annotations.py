"""PDF annotations: list existing annotations and add highlight, note, box and stamp ones.

Functions take and return PDF ``bytes`` like :mod:`anydoc.formats.pdf.pages`. Page indexes are
0-based and rectangles are ``(x0, y0, x1, y1)`` in PDF user space (origin bottom-left).
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from pypdf import PdfWriter
from pypdf.annotations import FreeText, Highlight, Rectangle, Text
from pypdf.generic import (
    ArrayObject,
    DecodedStreamObject,
    DictionaryObject,
    FloatObject,
    NameObject,
    NumberObject,
    TextStringObject,
)

from anydoc.core.errors import OperationNotSupportedError
from anydoc.formats.pdf.pages import _check, _dump, _reader

__all__ = [
    "Annotation",
    "add_highlight",
    "add_rectangle",
    "add_stamp",
    "add_text_annotation",
    "list_annotations",
    "remove_annotations",
]

Rect = tuple[float, float, float, float]

_SKIP = {"/Link", "/Popup", "/Widget"}


@dataclass(frozen=True)
class Annotation:
    """One annotation found on a page."""

    page: int  # 0-based
    index: int  # position in the page's /Annots array
    type: str  # e.g. highlight, text, square, stamp, freetext
    rect: Rect | None
    contents: str
    author: str
    color: tuple[float, ...] | None

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serialisable dict."""
        return asdict(self)


def _floats(arr: Any) -> tuple[float, ...] | None:
    try:
        return tuple(round(float(v), 3) for v in arr)
    except (TypeError, ValueError):
        return None


def list_annotations(
    data: bytes, password: str = "", *, include_links: bool = False
) -> list[Annotation]:
    """List annotations of every page (links, popups and form widgets are skipped by default)."""
    reader = _reader(data, password)
    out: list[Annotation] = []
    for pno, page in enumerate(reader.pages):
        annots = page.get("/Annots")
        if annots is None:
            continue
        for idx, ref in enumerate(annots.get_object()):
            a = ref.get_object()
            subtype = str(a.get("/Subtype", ""))
            if subtype in _SKIP and not (include_links and subtype == "/Link"):
                continue
            rect = _floats(a.get("/Rect", ()))
            color = _floats(a["/C"]) if "/C" in a else None
            out.append(
                Annotation(
                    page=pno,
                    index=idx,
                    type=subtype.lstrip("/").lower(),
                    rect=rect if rect and len(rect) == 4 else None,
                    contents=str(a.get("/Contents", "")),
                    author=str(a.get("/T", "")),
                    color=color or None,
                )
            )
    return out


def _hex(color: str | tuple[float, float, float]) -> str:
    if isinstance(color, str):
        return color.lstrip("#")
    r, g, b = (max(0, min(255, round(c * 255))) for c in color)
    return f"{r:02x}{g:02x}{b:02x}"


def _prep(data: bytes, page: int, password: str) -> tuple[PdfWriter, int]:
    reader = _reader(data, password)
    if reader.is_encrypted:
        raise OperationNotSupportedError("Editing encrypted PDFs is not supported yet")
    writer = PdfWriter(clone_from=reader)
    (page,) = _check([page], len(writer.pages))
    return writer, page


def _last_annot(writer: PdfWriter, page: int) -> DictionaryObject:
    annots: Any = writer.pages[page]["/Annots"]
    last: DictionaryObject = annots[-1].get_object()
    return last


def _set_meta(writer: PdfWriter, page: int, author: str | None) -> None:
    if author:
        annot = _last_annot(writer, page)
        annot[NameObject("/T")] = TextStringObject(author)


def add_highlight(
    data: bytes,
    page: int,
    rects: Rect | list[Rect],
    *,
    color: str | tuple[float, float, float] = "FFFF00",
    contents: str = "",
    author: str | None = None,
    password: str = "",
) -> bytes:
    """Add a highlight over one or more rectangles of ``page``."""
    writer, page = _prep(data, page, password)
    rect_list: list[Rect] = (
        [rects] if isinstance(rects, tuple) else list(rects)  # a single rectangle is a tuple
    )
    if not rect_list:
        raise ValueError("At least one rectangle is required")
    quads = ArrayObject()
    for x0, y0, x1, y1 in rect_list:
        for v in (x0, y1, x1, y1, x0, y0, x1, y0):
            quads.append(FloatObject(v))
    xs = [r[0] for r in rect_list] + [r[2] for r in rect_list]
    ys = [r[1] for r in rect_list] + [r[3] for r in rect_list]
    annot = Highlight(
        rect=(min(xs), min(ys), max(xs), max(ys)), quad_points=quads, highlight_color=_hex(color)
    )
    if contents:
        annot[NameObject("/Contents")] = TextStringObject(contents)
    writer.add_annotation(page, annot)
    _set_meta(writer, page, author)
    return _dump(writer)


def add_text_annotation(
    data: bytes,
    page: int,
    text: str,
    *,
    rect: Rect | None = None,
    point: tuple[float, float] | None = None,
    font_size: float = 12,
    color: str | tuple[float, float, float] = "000000",
    author: str | None = None,
    password: str = "",
) -> bytes:
    """Add a text annotation.

    With ``rect`` a visible free-text box is created; with ``point`` (default when neither is
    given) a sticky-note icon holding ``text`` as its popup contents.
    """
    writer, page = _prep(data, page, password)
    if rect is not None:
        annot: Any = FreeText(
            text=text, rect=rect, font_size=f"{font_size:g}pt", font_color=_hex(color)
        )
    else:
        x, y = point or (36.0, 36.0)
        annot = Text(text=text, rect=(x, y, x + 20, y + 20), open=False)
    writer.add_annotation(page, annot)
    _set_meta(writer, page, author)
    return _dump(writer)


def add_rectangle(
    data: bytes,
    page: int,
    rect: Rect,
    *,
    color: str | tuple[float, float, float] = "FF0000",
    fill: str | tuple[float, float, float] | None = None,
    width: float = 1.5,
    contents: str = "",
    author: str | None = None,
    password: str = "",
) -> bytes:
    """Add a rectangle (``/Square``) annotation, optionally filled."""
    writer, page = _prep(data, page, password)
    annot = Rectangle(rect=rect, interior_color=_hex(fill) if fill is not None else None)
    writer.add_annotation(page, annot)
    live = _last_annot(writer, page)
    live[NameObject("/C")] = ArrayObject(
        FloatObject(int(_hex(color)[i : i + 2], 16) / 255) for i in (0, 2, 4)
    )
    live[NameObject("/BS")] = DictionaryObject(
        {NameObject("/W"): FloatObject(width), NameObject("/S"): NameObject("/S")}
    )
    if contents:
        live[NameObject("/Contents")] = TextStringObject(contents)
    _set_meta(writer, page, author)
    return _dump(writer)


def add_stamp(
    data: bytes,
    page: int,
    label: str,
    rect: Rect,
    *,
    color: str | tuple[float, float, float] = "CC0000",
    author: str | None = None,
    password: str = "",
) -> bytes:
    """Add a rubber-stamp annotation (a coloured bordered box with ``label``).

    ``label`` should be ASCII (e.g. ``APPROVED``, ``DRAFT``, ``CONFIDENTIAL``); the stamp is
    drawn with Helvetica-Bold so it renders in every viewer.
    """
    writer, page = _prep(data, page, password)
    x0, y0, x1, y1 = rect
    w, h = x1 - x0, y1 - y0
    if w <= 0 or h <= 0:
        raise ValueError("Stamp rectangle must have positive width and height")
    hx = _hex(color)
    r, g, b = (int(hx[i : i + 2], 16) / 255 for i in (0, 2, 4))
    size = min(h * 0.5, w / max(1, len(label)) * 1.6)
    safe = label.encode("latin-1", "replace").decode("latin-1")
    safe = safe.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
    content = (
        f"q {r:.3f} {g:.3f} {b:.3f} RG {r:.3f} {g:.3f} {b:.3f} rg 2 w "
        f"1 1 {w - 2:.2f} {h - 2:.2f} re S "
        f"BT /F1 {size:.2f} Tf {w * 0.06:.2f} {(h - size) / 2 + size * 0.2:.2f} Td ({safe}) Tj ET Q"
    )
    ap = DecodedStreamObject()
    ap.set_data(content.encode("latin-1"))
    font = DictionaryObject(
        {
            NameObject("/Type"): NameObject("/Font"),
            NameObject("/Subtype"): NameObject("/Type1"),
            NameObject("/BaseFont"): NameObject("/Helvetica-Bold"),
        }
    )
    ap.update(
        {
            NameObject("/Type"): NameObject("/XObject"),
            NameObject("/Subtype"): NameObject("/Form"),
            NameObject("/BBox"): ArrayObject(FloatObject(v) for v in (0, 0, w, h)),
            NameObject("/Resources"): DictionaryObject(
                {NameObject("/Font"): DictionaryObject({NameObject("/F1"): font})}
            ),
        }
    )
    ap_ref = writer._add_object(ap)
    annot = DictionaryObject(
        {
            NameObject("/Type"): NameObject("/Annot"),
            NameObject("/Subtype"): NameObject("/Stamp"),
            NameObject("/Rect"): ArrayObject(FloatObject(v) for v in rect),
            NameObject("/Name"): NameObject("/" + "".join(c for c in label.title() if c.isalnum())),
            NameObject("/Contents"): TextStringObject(label),
            NameObject("/F"): NumberObject(4),
            NameObject("/C"): ArrayObject([FloatObject(r), FloatObject(g), FloatObject(b)]),
            NameObject("/AP"): DictionaryObject({NameObject("/N"): ap_ref}),
        }
    )
    writer.add_annotation(page, annot)
    _set_meta(writer, page, author)
    return _dump(writer)


def remove_annotations(
    data: bytes,
    *,
    pages: list[int] | None = None,
    types: set[str] | None = None,
    password: str = "",
) -> bytes:
    """Remove annotations (all, or only ``types`` such as ``{"highlight"}`` / given pages).

    Links, popups and form widgets are never removed.
    """
    reader = _reader(data, password)
    if reader.is_encrypted:
        raise OperationNotSupportedError("Editing encrypted PDFs is not supported yet")
    writer = PdfWriter(clone_from=reader)
    targets = _check(pages, len(writer.pages)) if pages is not None else range(len(writer.pages))
    wanted = {t.lower() for t in types} if types else None
    for pno in targets:
        page = writer.pages[pno]
        annots = page.get("/Annots")
        if annots is None:
            continue
        keep = ArrayObject()
        for ref in annots.get_object():
            sub = str(ref.get_object().get("/Subtype", ""))
            drop = sub not in _SKIP and (wanted is None or sub.lstrip("/").lower() in wanted)
            if not drop:
                keep.append(ref)
        page[NameObject("/Annots")] = keep
    return _dump(writer)
