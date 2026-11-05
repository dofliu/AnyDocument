"""Headers, footers, sections and page layout helpers for DOCX (python-docx based)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from docx.enum.section import WD_ORIENT, WD_SECTION
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Emu, Mm
from docx.text.paragraph import Paragraph

from anydoc.core.errors import OperationNotSupportedError
from anydoc.formats.docx.edit import _ALIGNMENTS, format_run

#: Paper sizes in millimetres, portrait ``(width, height)``.
PAPER_SIZES_MM: dict[str, tuple[float, float]] = {
    "A3": (297.0, 420.0),
    "A4": (210.0, 297.0),
    "A5": (148.0, 210.0),
    "B4": (250.0, 353.0),
    "B5": (176.0, 250.0),
    "LETTER": (215.9, 279.4),
    "LEGAL": (215.9, 355.6),
}

_SECTION_BREAKS = {
    "new_page": WD_SECTION.NEW_PAGE,
    "continuous": WD_SECTION.CONTINUOUS,
    "even_page": WD_SECTION.EVEN_PAGE,
    "odd_page": WD_SECTION.ODD_PAGE,
    "new_column": WD_SECTION.NEW_COLUMN,
}
_SECTION_BREAK_NAMES = {v: k for k, v in _SECTION_BREAKS.items()}


@dataclass(frozen=True)
class SectionInfo:
    """Page layout summary of one section (lengths in millimetres)."""

    index: int
    start_type: str
    orientation: str
    page_width: float
    page_height: float
    margin_top: float
    margin_bottom: float
    margin_left: float
    margin_right: float
    header_distance: float
    footer_distance: float
    different_first_page: bool
    paper: str | None


def _mm(value: Any) -> float:
    return float(round(Emu(int(value or 0)).mm, 1))


def section_at(doc: Any, index: int) -> Any:
    """Return section ``index`` (negative indexes allowed)."""
    sections = doc.sections
    if not -len(sections) <= index < len(sections):
        raise IndexError(f"Section index out of range: {index}")
    return sections[index]


def _match_paper(width: float, height: float) -> str | None:
    short, long_ = sorted((width, height))
    for name, (w, h) in PAPER_SIZES_MM.items():
        if abs(w - short) <= 1.0 and abs(h - long_) <= 1.0:
            return name
    return None


def section_info(doc: Any, index: int) -> SectionInfo:
    """Describe the layout of one section."""
    section = section_at(doc, index)
    real = index % len(doc.sections)
    width, height = _mm(section.page_width), _mm(section.page_height)
    orient = "landscape" if section.orientation == WD_ORIENT.LANDSCAPE else "portrait"
    return SectionInfo(
        index=real,
        start_type=_SECTION_BREAK_NAMES.get(section.start_type, "new_page"),
        orientation=orient,
        page_width=width,
        page_height=height,
        margin_top=_mm(section.top_margin),
        margin_bottom=_mm(section.bottom_margin),
        margin_left=_mm(section.left_margin),
        margin_right=_mm(section.right_margin),
        header_distance=_mm(section.header_distance),
        footer_distance=_mm(section.footer_distance),
        different_first_page=bool(section.different_first_page_header_footer),
        paper=_match_paper(width, height),
    )


def set_page_size(
    section: Any,
    paper: str | None = None,
    *,
    width: float | None = None,
    height: float | None = None,
    orientation: str | None = None,
) -> None:
    """Set paper size by name (``A4``…) or ``width``/``height`` in mm, then optionally orientation.

    ``width``/``height`` always describe the page as currently oriented unless ``orientation``
    is given, in which case the page is rotated to match it.
    """
    if paper is not None:
        key = paper.upper()
        if key not in PAPER_SIZES_MM:
            raise ValueError(f"Unknown paper size: {paper!r} (known: {', '.join(PAPER_SIZES_MM)})")
        w, h = PAPER_SIZES_MM[key]
        section.page_width, section.page_height = Mm(w), Mm(h)
        if section.orientation == WD_ORIENT.LANDSCAPE and orientation is None:
            orientation = "landscape"
    elif width is not None and height is not None:
        if width <= 0 or height <= 0:
            raise ValueError("Page width and height must be positive")
        section.page_width, section.page_height = Mm(width), Mm(height)
    elif width is not None or height is not None:
        raise ValueError("Give both width and height, or a paper name")
    if orientation is not None:
        set_orientation(section, orientation)


def set_orientation(section: Any, orientation: str) -> None:
    """Switch a section to ``portrait`` or ``landscape``, swapping page dimensions as needed."""
    key = orientation.lower()
    if key not in ("portrait", "landscape"):
        raise ValueError(f"Unknown orientation: {orientation!r}")
    width, height = int(section.page_width), int(section.page_height)
    landscape = key == "landscape"
    if (width < height) == landscape:
        section.page_width, section.page_height = Emu(height), Emu(width)
    section.orientation = WD_ORIENT.LANDSCAPE if landscape else WD_ORIENT.PORTRAIT


def set_margins(
    section: Any,
    *,
    top: float | None = None,
    bottom: float | None = None,
    left: float | None = None,
    right: float | None = None,
    header: float | None = None,
    footer: float | None = None,
    gutter: float | None = None,
) -> None:
    """Set page margins (millimetres); ``None`` leaves a value unchanged."""
    values = {
        "top_margin": top,
        "bottom_margin": bottom,
        "left_margin": left,
        "right_margin": right,
        "header_distance": header,
        "footer_distance": footer,
        "gutter": gutter,
    }
    for attr, value in values.items():
        if value is None:
            continue
        if value < 0:
            raise ValueError(f"Margin must not be negative: {attr}={value}")
        setattr(section, attr, Mm(value))


def add_section(doc: Any, start_type: str = "new_page") -> Any:
    """Append a new section (inherits the previous section's layout)."""
    if start_type not in _SECTION_BREAKS:
        raise ValueError(
            f"Unknown section break: {start_type!r} (known: {', '.join(_SECTION_BREAKS)})"
        )
    return doc.add_section(_SECTION_BREAKS[start_type])


def _add_field(paragraph: Paragraph, instruction: str, placeholder: str = "1") -> Any:
    """Append a complex field (``PAGE``, ``NUMPAGES``…) to a paragraph; returns the last run."""

    def make_run() -> Any:
        return paragraph.add_run()

    def fld_char(kind: str) -> Any:
        run = make_run()
        element = OxmlElement("w:fldChar")
        element.set(qn("w:fldCharType"), kind)
        run._r.append(element)
        return run

    fld_char("begin")
    run = make_run()
    instr = OxmlElement("w:instrText")
    instr.set(qn("xml:space"), "preserve")
    instr.text = f" {instruction} "
    run._r.append(instr)
    fld_char("separate")
    result = make_run()
    result.text = placeholder
    fld_char("end")
    return result


def _target(section: Any, kind: str, which: str) -> Any:
    if kind not in ("header", "footer"):
        raise ValueError(f"kind must be 'header' or 'footer', got {kind!r}")
    if which not in ("primary", "first", "even"):
        raise ValueError(f"which must be 'primary', 'first' or 'even', got {which!r}")
    if which == "first":
        section.different_first_page_header_footer = True
    elif which == "even":
        section.part.document.settings.odd_and_even_pages_header_footer = True
    name = {"primary": "", "first": "first_page_", "even": "even_page_"}[which] + kind
    part = getattr(section, name)
    part.is_linked_to_previous = False
    return part


def set_header_footer(
    section: Any,
    kind: str,
    text: str = "",
    *,
    which: str = "primary",
    alignment: str | None = None,
    page_number: bool = False,
    total_pages: bool = False,
    **fmt: Any,
) -> None:
    """Replace a section's header/footer content.

    ``text`` may contain ``{page}`` and ``{pages}`` placeholders that become live ``PAGE`` and
    ``NUMPAGES`` fields. ``page_number=True`` appends ``{page}`` (and ``/ {pages}`` with
    ``total_pages=True``) when the text has no placeholder. ``which`` selects the
    ``primary``, ``first`` (title page) or ``even`` variant.
    """
    part = _target(section, kind, which)
    if (page_number or total_pages) and "{page}" not in text and "{pages}" not in text:
        suffix = "{page}" + (" / {pages}" if total_pages else "")
        text = f"{text} {suffix}".strip()
    paragraphs = part.paragraphs
    paragraph = paragraphs[0]
    for extra in paragraphs[1:]:
        extra._p.getparent().remove(extra._p)
    for child in list(paragraph._p):
        if child.tag != qn("w:pPr"):
            paragraph._p.remove(child)
    if alignment is not None:
        if alignment not in _ALIGNMENTS:
            raise ValueError(f"Unknown alignment: {alignment!r}")
        paragraph.alignment = _ALIGNMENTS[alignment]
    _fill(paragraph, text, fmt)


def _fill(paragraph: Paragraph, text: str, fmt: dict[str, Any]) -> None:
    import re

    for token in re.split(r"(\{pages?\})", text):
        if not token:
            continue
        if token in ("{page}", "{pages}"):
            run = _add_field(paragraph, "PAGE" if token == "{page}" else "NUMPAGES")
            format_run(run, **fmt)
        else:
            run = paragraph.add_run(token)
            format_run(run, **fmt)


def clear_header_footer(section: Any, kind: str, which: str = "primary") -> None:
    """Remove a header/footer variant's content (it stays defined but empty)."""
    set_header_footer(section, kind, "", which=which)


def add_page_number(
    section: Any,
    *,
    position: str = "footer",
    alignment: str = "center",
    template: str = "{page}",
    **fmt: Any,
) -> None:
    """Insert a page-number field built from ``template`` into the header or footer."""
    if position not in ("header", "footer"):
        raise OperationNotSupportedError(f"Unsupported page number position: {position!r}")
    set_header_footer(section, position, template, alignment=alignment, **fmt)


def header_footer_fields(section: Any, kind: str, which: str = "primary") -> list[str]:
    """Return the field instructions (e.g. ``["PAGE", "NUMPAGES"]``) present in a header/footer."""
    part = _target_readonly(section, kind, which)
    if part is None:
        return []
    fields: list[str] = []
    for element in part._element.iter(qn("w:instrText")):
        if element.text and element.text.strip():
            fields.append(element.text.strip().split()[0].upper())
    for element in part._element.iter(qn("w:fldSimple")):
        instr = element.get(qn("w:instr")) or ""
        if instr.strip():
            fields.append(instr.strip().split()[0].upper())
    return fields


def _target_readonly(section: Any, kind: str, which: str) -> Any:
    if kind not in ("header", "footer") or which not in ("primary", "first", "even"):
        raise ValueError("Invalid header/footer selector")
    name = {"primary": "", "first": "first_page_", "even": "even_page_"}[which] + kind
    part = getattr(section, name)
    return None if part.is_linked_to_previous else part
