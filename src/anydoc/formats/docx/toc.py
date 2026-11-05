"""Table of contents helpers for DOCX (python-docx based).

Two flavours are supported:

* a *field* TOC (``TOC \\o "1-3" \\h \\z \\u``) whose cached result is pre-filled from the headings,
  so it displays correctly even before Word/LibreOffice refreshes it;
* a *static* TOC made of plain paragraphs (optionally hyperlinked to heading bookmarks).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.text.paragraph import Paragraph

from anydoc.core.errors import OperationNotSupportedError

_HEADING_RE = re.compile(r"^heading\s*(\d)$", re.IGNORECASE)
_LEVELS_RE = re.compile(r"^(\d)(?:-(\d))?$")
_BOOKMARK_PREFIX = "_Toc"


@dataclass(frozen=True)
class TocEntry:
    """One heading that appears in a table of contents."""

    level: int
    text: str
    bookmark: str | None = None


def parse_levels(levels: str | tuple[int, int]) -> tuple[int, int]:
    """Parse ``"1-3"`` / ``"2"`` / ``(1, 3)`` into an inclusive ``(low, high)`` pair."""
    if isinstance(levels, tuple):
        low, high = levels
    else:
        m = _LEVELS_RE.match(levels.strip())
        if not m:
            raise ValueError(f"Invalid heading levels: {levels!r} (use e.g. '1-3')")
        low = int(m.group(1))
        high = int(m.group(2) or m.group(1))
    if not 1 <= low <= high <= 9:
        raise ValueError(f"Heading levels must satisfy 1 <= low <= high <= 9: {levels!r}")
    return low, high


def _heading_level(paragraph: Paragraph) -> int | None:
    try:
        name = paragraph.style.name if paragraph.style is not None else ""
    except KeyError:  # dangling style reference
        return None
    m = _HEADING_RE.match(name or "")
    return int(m.group(1)) if m else None


def _is_toc_paragraph(paragraph: Paragraph) -> bool:
    ppr = paragraph._p.pPr
    if ppr is None or ppr.pStyle is None:
        return False
    style_id = str(ppr.pStyle.val).lower()
    return style_id.startswith("toc") and style_id != "tocheading"


def _is_toc_title(paragraph: Paragraph) -> bool:
    ppr = paragraph._p.pPr
    return ppr is not None and ppr.pStyle is not None and ppr.pStyle.val == "TOCHeading"


def toc_entries(doc: Any, levels: str | tuple[int, int] = "1-3") -> list[TocEntry]:
    """List the body headings in ``levels`` in document order (empty headings are skipped)."""
    low, high = parse_levels(levels)
    entries: list[TocEntry] = []
    for paragraph in doc.paragraphs:
        level = _heading_level(paragraph)
        text = paragraph.text.strip()
        if level is None or not low <= level <= high or not text:
            continue
        entries.append(TocEntry(level, text, _existing_bookmark(paragraph)))
    return entries


def _existing_bookmark(paragraph: Paragraph) -> str | None:
    for el in paragraph._p.findall(qn("w:bookmarkStart")):
        name = el.get(qn("w:name"))
        if name and name.startswith(_BOOKMARK_PREFIX):
            return str(name)
    return None


def _next_bookmark_id(doc: Any) -> int:
    ids = [
        int(el.get(qn("w:id")) or 0)
        for el in doc.element.body.iter(qn("w:bookmarkStart"))
        if (el.get(qn("w:id")) or "").isdigit()
    ]
    return max(ids, default=0) + 1


def _ensure_bookmarks(doc: Any, levels: str | tuple[int, int]) -> list[TocEntry]:
    """Give every heading in range a ``_Toc`` bookmark and return the entries."""
    low, high = parse_levels(levels)
    next_id = _next_bookmark_id(doc)
    entries: list[TocEntry] = []
    counter = 0
    for paragraph in doc.paragraphs:
        level = _heading_level(paragraph)
        text = paragraph.text.strip()
        if level is None or not low <= level <= high or not text:
            continue
        counter += 1
        name = _existing_bookmark(paragraph)
        if name is None:
            name = f"{_BOOKMARK_PREFIX}{900000000 + counter:09d}"
            start = OxmlElement("w:bookmarkStart")
            start.set(qn("w:id"), str(next_id))
            start.set(qn("w:name"), name)
            end = OxmlElement("w:bookmarkEnd")
            end.set(qn("w:id"), str(next_id))
            next_id += 1
            ppr = paragraph._p.pPr
            if ppr is not None:
                ppr.addnext(start)
            else:
                paragraph._p.insert(0, start)
            paragraph._p.append(end)
        entries.append(TocEntry(level, text, name))
    return entries


def _ensure_toc_styles(doc: Any, low: int, high: int) -> None:
    """Make sure paragraph styles ``toc 1``…``toc n`` exist (indent grows per level)."""
    from docx.enum.style import WD_STYLE_TYPE
    from docx.shared import Mm

    existing = {s.style_id for s in doc.styles}
    for level in range(low, high + 1):
        style_id = f"TOC{level}"
        if style_id in existing:
            continue
        style = doc.styles.add_style(f"toc {level}", WD_STYLE_TYPE.PARAGRAPH)
        style.element.set(qn("w:styleId"), style_id)
        style.base_style = doc.styles["Normal"]
        style.paragraph_format.left_indent = Mm(5 * (level - 1))
        style.paragraph_format.space_after = Mm(1)
        style.quick_style = False


def _set_style_id(paragraph: Paragraph, style_id: str) -> None:
    ppr = paragraph._p.get_or_add_pPr()
    pstyle = ppr.get_or_add_pStyle()
    pstyle.val = style_id


def _fld_char(kind: str) -> Any:
    run = OxmlElement("w:r")
    fld = OxmlElement("w:fldChar")
    fld.set(qn("w:fldCharType"), kind)
    run.append(fld)
    return run


def _instr_run(instruction: str) -> Any:
    run = OxmlElement("w:r")
    instr = OxmlElement("w:instrText")
    instr.set("{http://www.w3.org/XML/1998/namespace}space", "preserve")
    instr.text = f" {instruction} "
    run.append(instr)
    return run


def _text_run(text: str) -> Any:
    run = OxmlElement("w:r")
    t = OxmlElement("w:t")
    t.set("{http://www.w3.org/XML/1998/namespace}space", "preserve")
    t.text = text
    run.append(t)
    return run


def _entry_content(entry: TocEntry, *, hyperlink: bool) -> list[Any]:
    run = _text_run(entry.text)
    if hyperlink and entry.bookmark:
        link = OxmlElement("w:hyperlink")
        link.set(qn("w:anchor"), entry.bookmark)
        link.set(qn("w:history"), "1")
        link.append(run)
        return [link]
    return [run]


def _make_paragraph(doc: Any, style_id: str) -> Paragraph:
    p = OxmlElement("w:p")
    paragraph = Paragraph(p, doc._body)
    _set_style_id(paragraph, style_id)
    return paragraph


def _title_paragraph(doc: Any, title: str) -> Paragraph:
    from docx.enum.style import WD_STYLE_TYPE
    from docx.shared import Pt

    if "TOCHeading" not in {s.style_id for s in doc.styles}:
        style = doc.styles.add_style("TOC Heading", WD_STYLE_TYPE.PARAGRAPH)
        style.element.set(qn("w:styleId"), "TOCHeading")
        style.base_style = doc.styles["Normal"]
        style.font.bold = True
        style.font.size = Pt(16)
        style.paragraph_format.space_after = Pt(8)
    paragraph = Paragraph(OxmlElement("w:p"), doc._body)
    _set_style_id(paragraph, "TOCHeading")
    paragraph.add_run(title)
    return paragraph


def _insert_block(doc: Any, block: list[Paragraph], index: int | None) -> None:
    """Insert ``block`` at body-paragraph ``index`` (``None`` = before the final sectPr/end)."""
    body = doc.element.body
    if index is None:
        anchor = body.find(qn("w:sectPr"))
        for paragraph in block:
            if anchor is not None:
                anchor.addprevious(paragraph._p)
            else:
                body.append(paragraph._p)
        return
    paragraphs = doc.paragraphs
    if not -len(paragraphs) <= index <= len(paragraphs):
        raise IndexError(f"Paragraph index out of range: {index}")
    if index == len(paragraphs):
        _insert_block(doc, block, None)
        return
    target = paragraphs[index]._p
    for paragraph in block:
        target.addprevious(paragraph._p)


def _build(
    doc: Any,
    *,
    field: bool,
    levels: str | tuple[int, int],
    title: str | None,
    index: int | None,
    hyperlinks: bool,
    page_break_after: bool,
) -> list[TocEntry]:
    if has_toc(doc):
        raise OperationNotSupportedError(
            "Document already has a table of contents; remove it first"
        )
    low, high = parse_levels(levels)
    entries = _ensure_bookmarks(doc, (low, high))
    _ensure_toc_styles(doc, low, high)
    block: list[Paragraph] = []
    if title:
        block.append(_title_paragraph(doc, title))
    paragraphs = [_make_paragraph(doc, f"TOC{e.level}") for e in entries]
    if field:
        instruction = f'TOC \\o "{low}-{high}" \\h \\z \\u'
        if not paragraphs:
            paragraphs = [_make_paragraph(doc, f"TOC{low}")]
        first, last = paragraphs[0], paragraphs[-1]
        begin = [_fld_char("begin"), _instr_run(instruction), _fld_char("separate")]
        for el in begin:
            first._p.append(el)
    for paragraph, entry in zip(paragraphs, entries, strict=False):
        for el in _entry_content(entry, hyperlink=hyperlinks):
            paragraph._p.append(el)
    if field:
        last._p.append(_fld_char("end"))
    block.extend(paragraphs)
    if page_break_after:
        from docx.enum.text import WD_BREAK

        brk = _make_paragraph(doc, "Normal")
        brk.add_run().add_break(WD_BREAK.PAGE)
        block.append(brk)
    _insert_block(doc, block, index)
    if field:
        _set_update_fields(doc)
    return entries


def _set_update_fields(doc: Any) -> None:
    """Ask Word to refresh fields (and page numbers) when the file is opened."""
    settings = doc.settings.element
    el = settings.find(qn("w:updateFields"))
    if el is None:
        el = OxmlElement("w:updateFields")
        settings.append(el)
    el.set(qn("w:val"), "true")


def add_toc(
    doc: Any,
    *,
    levels: str | tuple[int, int] = "1-3",
    title: str | None = "目錄",
    index: int | None = None,
    hyperlinks: bool = True,
    page_break_after: bool = False,
    update_on_open: bool = True,
) -> list[TocEntry]:
    """Insert a TOC *field* pre-filled with the current headings.

    ``index`` is the body-paragraph position (``None`` = end of document). With
    ``update_on_open`` Word refreshes the field (adding page numbers) when the file is opened.
    """
    entries = _build(
        doc,
        field=True,
        levels=levels,
        title=title,
        index=index,
        hyperlinks=hyperlinks,
        page_break_after=page_break_after,
    )
    if not update_on_open:
        el = doc.settings.element.find(qn("w:updateFields"))
        if el is not None:
            el.getparent().remove(el)
    return entries


def add_static_toc(
    doc: Any,
    *,
    levels: str | tuple[int, int] = "1-3",
    title: str | None = "目錄",
    index: int | None = None,
    hyperlinks: bool = True,
    page_break_after: bool = False,
) -> list[TocEntry]:
    """Insert a plain-paragraph TOC generated from the headings (no field, never refreshed)."""
    return _build(
        doc,
        field=False,
        levels=levels,
        title=title,
        index=index,
        hyperlinks=hyperlinks,
        page_break_after=page_break_after,
    )


def _toc_field_paragraphs(doc: Any) -> list[Paragraph]:
    """Paragraphs spanning from a TOC field begin to its matching end."""
    result: list[Paragraph] = []
    inside = False
    for paragraph in doc.paragraphs:
        xml = paragraph._p
        instrs = [i.text or "" for i in xml.iter(qn("w:instrText"))]
        begins_toc = any(i.strip().upper().startswith("TOC") for i in instrs)
        if begins_toc and not inside:
            inside = True
        if inside:
            result.append(paragraph)
            ends = [f for f in xml.iter(qn("w:fldChar")) if f.get(qn("w:fldCharType")) == "end"]
            if ends:
                inside = False
    return result


def has_toc(doc: Any) -> bool:
    """Return whether the body contains a TOC field or static ``toc n`` paragraphs."""
    if _toc_field_paragraphs(doc):
        return True
    return any(_is_toc_paragraph(p) for p in doc.paragraphs)


def toc_info(doc: Any) -> dict[str, Any]:
    """Describe the existing TOC: kind (``field``/``static``/``None``), instruction, entries."""
    field_paras = _toc_field_paragraphs(doc)
    if field_paras:
        instr = " ".join(
            (i.text or "").strip() for p in field_paras for i in p._p.iter(qn("w:instrText"))
        )
        kind: str | None = "field"
        paras = field_paras
    else:
        paras = [p for p in doc.paragraphs if _is_toc_paragraph(p)]
        kind = "static" if paras else None
        instr = ""
    entries = [p.text.strip() for p in paras if p.text.strip()]
    return {"kind": kind, "instruction": instr, "entries": entries}


def remove_toc(doc: Any) -> int:
    """Remove the TOC (field or static paragraphs); returns the number of paragraphs removed."""
    body = _toc_field_paragraphs(doc) or [p for p in doc.paragraphs if _is_toc_paragraph(p)]
    targets = body + [p for p in doc.paragraphs if _is_toc_title(p)] if body else []
    removed = 0
    for paragraph in targets:
        parent = paragraph._p.getparent()
        if parent is not None:
            parent.remove(paragraph._p)
            removed += 1
    if removed:
        el = doc.settings.element.find(qn("w:updateFields"))
        if el is not None:
            el.getparent().remove(el)
    return removed
