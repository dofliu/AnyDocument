"""Creation and basic editing helpers for DOCX documents (python-docx based)."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from docx.enum.text import WD_BREAK
from docx.shared import Pt, RGBColor
from docx.text.paragraph import Paragraph

from anydoc.core.errors import OperationNotSupportedError

_ALIGNMENTS = {"left": 0, "center": 1, "right": 2, "justify": 3}


def parse_color(color: str) -> RGBColor:
    """Parse ``"RRGGBB"`` or ``"#RRGGBB"`` into an ``RGBColor``."""
    value = color.lstrip("#")
    if len(value) != 6:
        raise ValueError(f"Invalid color: {color!r}")
    try:
        return RGBColor.from_string(value.upper())
    except ValueError as exc:
        raise ValueError(f"Invalid color: {color!r}") from exc


def format_run(
    run: Any,
    *,
    bold: bool | None = None,
    italic: bool | None = None,
    underline: bool | None = None,
    strike: bool | None = None,
    font: str | None = None,
    size: float | None = None,
    color: str | None = None,
) -> None:
    """Apply character formatting to a python-docx run (``None`` leaves a property unchanged)."""
    if bold is not None:
        run.bold = bold
    if italic is not None:
        run.italic = italic
    if underline is not None:
        run.underline = underline
    if strike is not None:
        run.font.strike = strike
    if font is not None:
        run.font.name = font
        # East Asian text needs the eastAsia font attribute as well.
        rpr = run._r.get_or_add_rPr()
        rfonts = rpr.get_or_add_rFonts()
        rfonts.set("{http://schemas.openxmlformats.org/wordprocessingml/2006/main}eastAsia", font)
    if size is not None:
        run.font.size = Pt(size)
    if color is not None:
        run.font.color.rgb = parse_color(color)


def set_alignment(paragraph: Paragraph, alignment: str | None) -> None:
    """Set paragraph alignment (``left``/``center``/``right``/``justify``)."""
    if alignment is None:
        return
    try:
        paragraph.alignment = _ALIGNMENTS[alignment.lower()]  # type: ignore[assignment]
    except KeyError:
        raise ValueError(f"Unknown alignment: {alignment!r}") from None


def add_paragraph(
    doc: Any,
    text: str = "",
    *,
    style: str | None = None,
    alignment: str | None = None,
    **fmt: Any,
) -> Paragraph:
    """Append a paragraph; extra keyword arguments format its single run."""
    if alignment is not None and alignment.lower() not in _ALIGNMENTS:
        raise ValueError(f"Unknown alignment: {alignment!r}")
    if fmt.get("color") is not None:
        parse_color(fmt["color"])
    paragraph: Paragraph = doc.add_paragraph()
    if style is not None:
        try:
            paragraph.style = style
        except KeyError:
            delete_paragraph(paragraph)
            raise ValueError(f"Unknown paragraph style: {style!r}") from None
    if text:
        format_run(paragraph.add_run(text), **fmt)
    set_alignment(paragraph, alignment)
    return paragraph


def add_heading(doc: Any, text: str, level: int = 1) -> Paragraph:
    """Append a heading (``level`` 0 = Title, 1-9 = Heading n)."""
    if not 0 <= level <= 9:
        raise ValueError(f"Heading level must be 0-9, got {level}")
    return doc.add_heading(text, level=level)  # type: ignore[no-any-return]


def add_list(
    doc: Any, items: Sequence[str], *, ordered: bool = False, level: int = 0
) -> list[Paragraph]:
    """Append a bulleted or numbered list; ``level`` 0-2 selects the nesting indent."""
    if not 0 <= level <= 2:
        raise ValueError(f"List level must be 0-2, got {level}")
    base = "List Number" if ordered else "List Bullet"
    style = base if level == 0 else f"{base} {level + 1}"
    return [doc.add_paragraph(item, style=style) for item in items]


def add_page_break(doc: Any) -> None:
    """Append a page break."""
    doc.add_paragraph().add_run().add_break(WD_BREAK.PAGE)


def paragraph_at(doc: Any, index: int) -> Paragraph:
    """Return body paragraph ``index`` (0-based, negative allowed)."""
    paragraphs = doc.paragraphs
    try:
        return paragraphs[index]  # type: ignore[no-any-return]
    except IndexError:
        raise OperationNotSupportedError(
            f"Paragraph index {index} out of range (0..{len(paragraphs) - 1})"
        ) from None


def set_paragraph_text(paragraph: Paragraph, text: str, **fmt: Any) -> None:
    """Replace a paragraph's text, keeping its style and the first run's formatting."""
    runs = paragraph.runs
    if runs:
        runs[0].text = text
        for extra in runs[1:]:
            extra._r.getparent().remove(extra._r)
        format_run(runs[0], **fmt)
    else:
        format_run(paragraph.add_run(text), **fmt)


def delete_paragraph(paragraph: Paragraph) -> None:
    """Remove a paragraph from the document."""
    element = paragraph._p
    element.getparent().remove(element)


def insert_paragraph_after(
    paragraph: Paragraph, text: str = "", style: str | None = None
) -> Paragraph:
    """Insert a new paragraph directly after ``paragraph``."""
    new_p = paragraph._p.makeelement(paragraph._p.tag, {})
    paragraph._p.addnext(new_p)
    new = Paragraph(new_p, paragraph._parent)
    if text:
        new.add_run(text)
    if style is not None:
        new.style = style
    return new
