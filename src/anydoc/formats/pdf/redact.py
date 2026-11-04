"""PDF redaction: permanently remove text or regions from pages.

Redacted pages are *rasterised* with opaque black boxes burned into the pixels and rebuilt as
image-only pages, so the covered content (and all other text/vector data on that page) is truly
gone from the file rather than merely hidden. Pages without redactions are copied untouched.
Coordinates are PDF points measured from the top-left of the *displayed* (rotated) page.

Functions take and return PDF ``bytes`` like :mod:`anydoc.formats.pdf.pages`.
"""

from __future__ import annotations

import io
import re
from collections.abc import Iterable, Sequence
from dataclasses import asdict, dataclass
from typing import Any

import pdfplumber
import pypdfium2
from PIL import Image, ImageDraw
from pypdf import PdfReader, PdfWriter

from anydoc.core.errors import CorruptFileError, OperationNotSupportedError
from anydoc.formats.pdf.pages import _dump, _reader

__all__ = ["RedactResult", "find_text", "redact_pdf", "verify_redaction"]

Rect = tuple[float, float, float, float]


@dataclass(frozen=True)
class RedactResult:
    """Outcome of a redaction run."""

    data: bytes
    pages_redacted: list[int]
    regions: int
    matches: int

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serialisable summary (without the PDF bytes)."""
        d = asdict(self)
        d.pop("data")
        return d


def _compile(patterns: Iterable[str], regex: bool, case_sensitive: bool) -> list[re.Pattern[str]]:
    flags = 0 if case_sensitive else re.IGNORECASE
    out = []
    for p in patterns:
        if not p:
            raise ValueError("Empty search text")
        try:
            out.append(re.compile(p if regex else re.escape(p), flags))
        except re.error as exc:
            raise ValueError(f"Invalid pattern {p!r}: {exc}") from exc
    return out


def _page_rects(page: Any, pats: Sequence[re.Pattern[str]]) -> list[Rect]:
    """Return rectangles (top-left origin) of every pattern match on a pdfplumber page."""
    chars = page.chars
    text = "".join(c["text"] for c in chars)
    rects: list[Rect] = []
    for pat in pats:
        for m in pat.finditer(text):
            if m.end() == m.start():
                continue
            seg = chars[m.start() : m.end()]
            rects.append(
                (
                    min(c["x0"] for c in seg),
                    min(c["top"] for c in seg),
                    max(c["x1"] for c in seg),
                    max(c["bottom"] for c in seg),
                )
            )
    return rects


def find_text(
    data: bytes,
    patterns: Iterable[str],
    *,
    regex: bool = False,
    case_sensitive: bool = False,
    password: str = "",
) -> dict[int, list[Rect]]:
    """Locate text; return ``{page_index: [(x0, top, x1, bottom), ...]}`` (0-based pages)."""
    pats = _compile(patterns, regex, case_sensitive)
    _reader(data, password)
    found: dict[int, list[Rect]] = {}
    try:
        with pdfplumber.open(io.BytesIO(data), password=password or None) as pdf:
            for i, page in enumerate(pdf.pages):
                rects = _page_rects(page, pats)
                if rects and page.rotation % 360:
                    raise OperationNotSupportedError(
                        "Text search on rotated pages is not supported; use explicit regions"
                    )
                if rects:
                    found[i] = rects
    except OperationNotSupportedError:
        raise
    except Exception as exc:
        raise CorruptFileError(f"Cannot read PDF text: {exc}") from exc
    return found


def _norm(text: str) -> str:
    return "".join(text.split()).casefold()


def verify_redaction(
    data: bytes, texts: Iterable[str], *, password: str = "", case_sensitive: bool = False
) -> list[str]:
    """Return the subset of ``texts`` that can still be extracted from the PDF (empty = clean)."""
    reader = _reader(data, password)
    pages = []
    for p in reader.pages:
        try:
            pages.append(p.extract_text() or "")
        except Exception:
            pages.append("")
    blob = "".join(pages)
    blob = "".join(blob.split())
    if not case_sensitive:
        blob = blob.casefold()
    leaks = []
    for t in texts:
        needle = "".join(t.split()) if case_sensitive else _norm(t)
        if needle and needle in blob:
            leaks.append(t)
    return leaks


def _check_rect(r: Sequence[float]) -> Rect:
    if len(r) != 4:
        raise ValueError(f"Region must have 4 numbers, got {r!r}")
    x0, y0, x1, y1 = (float(v) for v in r)
    return (min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1))


def redact_pdf(
    data: bytes,
    *,
    texts: Iterable[str] = (),
    regions: Iterable[tuple[int, Sequence[float]]] = (),
    regex: bool = False,
    case_sensitive: bool = False,
    dpi: float = 200.0,
    padding: float = 1.0,
    password: str = "",
) -> RedactResult:
    """Permanently remove text matches and/or rectangular regions.

    ``regions`` is an iterable of ``(page_index, (x0, top, x1, bottom))`` in points from the
    top-left of the displayed page. Pages containing a match or region are rasterised at ``dpi``
    with black boxes (grown by ``padding`` points); their text layer, vector data and
    annotations are discarded. When ``texts`` are given the result is verified and
    :class:`~anydoc.core.errors.CorruptFileError` is raised if any text remains extractable.
    """
    if dpi < 36 or dpi > 1200:
        raise ValueError("dpi must be between 36 and 1200")
    text_list = list(texts)
    reader = _reader(data, password)
    count = len(reader.pages)
    boxes: dict[int, list[Rect]] = {}
    matches = 0
    if text_list:
        for i, rects in find_text(
            data, text_list, regex=regex, case_sensitive=case_sensitive, password=password
        ).items():
            boxes.setdefault(i, []).extend(rects)
            matches += len(rects)
    n_regions = 0
    for page_index, rect in regions:
        if not 0 <= page_index < count:
            raise IndexError(f"No such page: {page_index} (document has {count} pages)")
        boxes.setdefault(page_index, []).append(_check_rect(rect))
        n_regions += 1
    if not boxes:
        return RedactResult(data=data, pages_redacted=[], regions=0, matches=0)

    pdfium = pypdfium2.PdfDocument(data, password=password or None)
    writer = PdfWriter()
    scale = dpi / 72.0
    try:
        for i in range(count):
            if i not in boxes:
                writer.add_page(reader.pages[i])
                continue
            bitmap = pdfium[i].render(scale=scale)
            img = bitmap.to_pil().convert("RGB")
            draw = ImageDraw.Draw(img)
            for x0, y0, x1, y1 in boxes[i]:
                draw.rectangle(
                    [
                        (x0 - padding) * scale,
                        (y0 - padding) * scale,
                        (x1 + padding) * scale,
                        (y1 + padding) * scale,
                    ],
                    fill=(0, 0, 0),
                )
            buf = io.BytesIO()
            Image.Image.save(img, buf, format="PDF", resolution=dpi)
            writer.add_page(PdfReader(buf).pages[0])
    finally:
        pdfium.close()
    if reader.metadata:
        writer.add_metadata({k: str(v) for k, v in reader.metadata.items()})
    out = _dump(writer)
    if text_list:
        leaks = verify_redaction(out, text_list, case_sensitive=case_sensitive) if not regex else []
        if leaks:
            raise CorruptFileError(f"Redaction verification failed; still extractable: {leaks}")
    return RedactResult(data=out, pages_redacted=sorted(boxes), regions=n_regions, matches=matches)
