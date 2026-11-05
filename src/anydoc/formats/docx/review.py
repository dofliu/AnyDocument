"""Tracked changes (revisions) and comments for DOCX (python-docx + lxml based).

Revisions handled: inserted/deleted runs (``w:ins``/``w:del``), moves (``w:moveFrom``/``w:moveTo``),
inserted/deleted paragraph marks, inserted/deleted table rows and property changes
(``w:rPrChange``, ``w:pPrChange``, ``w:sectPrChange``, ``w:tblPrChange`` ...).
"""

from __future__ import annotations

from collections.abc import Collection, Iterator
from dataclasses import dataclass
from typing import Any

from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.text.paragraph import Paragraph

_W_P = qn("w:p")
_W_PPR = qn("w:pPr")
_W_RPR = qn("w:rPr")
_W_SECTPR = qn("w:sectPr")
_W_T = qn("w:t")
_W_DELTEXT = qn("w:delText")
_W_INS = qn("w:ins")
_W_DEL = qn("w:del")
_W_MOVE_FROM = qn("w:moveFrom")
_W_MOVE_TO = qn("w:moveTo")
_W_TR = qn("w:tr")
_W_TRPR = qn("w:trPr")

_RANGE_TAGS = (
    qn("w:moveFromRangeStart"),
    qn("w:moveFromRangeEnd"),
    qn("w:moveToRangeStart"),
    qn("w:moveToRangeEnd"),
)

#: property-change element -> (old-properties child tag, parent tags kept when rejecting)
_FORMAT_CHANGES: dict[str, tuple[str, tuple[str, ...]]] = {
    qn("w:rPrChange"): (qn("w:rPr"), ()),
    qn("w:pPrChange"): (qn("w:pPr"), (_W_RPR, _W_SECTPR)),
    qn("w:sectPrChange"): (qn("w:sectPr"), ()),
    qn("w:tblPrChange"): (qn("w:tblPr"), ()),
    qn("w:tblGridChange"): (qn("w:tblGrid"), ()),
    qn("w:trPrChange"): (qn("w:trPr"), ()),
    qn("w:tcPrChange"): (qn("w:tcPr"), ()),
}

INSERT = "insert"
DELETE = "delete"
MOVE_FROM = "move_from"
MOVE_TO = "move_to"
FORMAT = "format"
PARAGRAPH_INSERT = "paragraph_insert"
PARAGRAPH_DELETE = "paragraph_delete"
ROW_INSERT = "row_insert"
ROW_DELETE = "row_delete"


@dataclass(frozen=True)
class Revision:
    """One tracked change found in the document."""

    kind: str
    id: int | None
    author: str
    date: str | None
    text: str


@dataclass(frozen=True)
class CommentInfo:
    """One comment with the text it is anchored to."""

    id: int
    author: str
    initials: str
    date: str | None
    text: str
    anchor_text: str


# -- traversal ---------------------------------------------------------------


def _roots(doc: Any) -> list[Any]:
    """Body plus every non-inherited header/footer element."""
    roots: list[Any] = [doc.element.body]
    seen: set[int] = set()
    for section in doc.sections:
        for part in (
            section.header,
            section.footer,
            section.first_page_header,
            section.first_page_footer,
            section.even_page_header,
            section.even_page_footer,
        ):
            if part.is_linked_to_previous:
                continue
            el = part._element
            if id(el) not in seen:
                seen.add(id(el))
                roots.append(el)
    return roots


def _kind(el: Any) -> str | None:
    """Classify ``el`` as a revision marker or return ``None``."""
    tag = el.tag
    parent = el.getparent()
    ptag = parent.tag if parent is not None else None
    if tag in (_W_INS, _W_DEL):
        if ptag == _W_RPR and parent.getparent() is not None:
            if parent.getparent().tag == _W_PPR:
                return PARAGRAPH_INSERT if tag == _W_INS else PARAGRAPH_DELETE
            return None  # run-level marker inside an ordinary rPr: not a revision we resolve
        if ptag == _W_TRPR:
            return ROW_INSERT if tag == _W_INS else ROW_DELETE
        return INSERT if tag == _W_INS else DELETE
    if tag == _W_MOVE_FROM:
        return MOVE_FROM
    if tag == _W_MOVE_TO:
        return MOVE_TO
    if tag in _FORMAT_CHANGES:
        return FORMAT
    return None


def _iter_revision_elements(roots: list[Any]) -> Iterator[tuple[str, Any]]:
    for root in roots:
        for el in root.iter(_W_INS, _W_DEL, _W_MOVE_FROM, _W_MOVE_TO, *_FORMAT_CHANGES):
            kind = _kind(el)
            if kind is not None:
                yield kind, el


def _text_of(el: Any) -> str:
    return "".join(node.text or "" for node in el.iter(_W_T, _W_DELTEXT))


def _row_text(row: Any) -> str:
    return " | ".join(
        "".join(t.text or "" for t in tc.iter(_W_T, _W_DELTEXT)) for tc in row.findall(qn("w:tc"))
    )


def _revision_of(kind: str, el: Any) -> Revision:
    raw_id = el.get(qn("w:id"))
    if kind in (PARAGRAPH_INSERT, PARAGRAPH_DELETE):
        text = ""
    elif kind in (ROW_INSERT, ROW_DELETE):
        row = el.getparent().getparent()
        text = _row_text(row) if row is not None and row.tag == _W_TR else ""
    elif kind == FORMAT:
        text = el.getparent().tag.split("}")[-1]
    else:
        text = _text_of(el)
    return Revision(
        kind=kind,
        id=int(raw_id) if raw_id is not None and raw_id.lstrip("-").isdigit() else None,
        author=el.get(qn("w:author")) or "",
        date=el.get(qn("w:date")),
        text=text,
    )


def list_revisions(doc: Any) -> list[Revision]:
    """List every tracked change in document order (body, then headers/footers)."""
    return [_revision_of(kind, el) for kind, el in _iter_revision_elements(_roots(doc))]


def has_revisions(doc: Any) -> bool:
    """Return whether the document contains any tracked change."""
    return next(_iter_revision_elements(_roots(doc)), None) is not None


# -- resolving ---------------------------------------------------------------


def _unwrap(el: Any, *, to_text: bool = False) -> None:
    """Replace ``el`` by its children (``w:delText`` → ``w:t`` when ``to_text``)."""
    parent = el.getparent()
    index = parent.index(el)
    if to_text:
        for node in el.iter(_W_DELTEXT):
            node.tag = _W_T
    for child in list(el):
        parent.insert(index, child)
        index += 1
    parent.remove(el)


def _drop(el: Any) -> None:
    el.getparent().remove(el)


def _merge_paragraph_into_next(p: Any) -> None:
    """Join paragraph ``p`` with the following one (``p``'s mark disappears)."""
    nxt = p.getnext()
    if nxt is None or nxt.tag != _W_P:
        return
    content = [c for c in p if c.tag != _W_PPR]
    pos = 1 if len(nxt) and nxt[0].tag == _W_PPR else 0
    for child in content:
        nxt.insert(pos, child)
        pos += 1
    p.getparent().remove(p)


def _remove_mark_marker(el: Any) -> Any:
    """Remove a paragraph-mark ``w:ins``/``w:del`` (and an empty ``w:rPr``); return its ``w:p``."""
    rpr = el.getparent()
    ppr = rpr.getparent()
    rpr.remove(el)
    if len(rpr) == 0:
        ppr.remove(rpr)
    return ppr.getparent()


def _restore_old_properties(change: Any) -> None:
    old_tag, keep = _FORMAT_CHANGES[change.tag]
    parent = change.getparent()
    old = change.find(old_tag)
    kept = [c for c in parent if c.tag in keep]
    for child in list(parent):
        parent.remove(child)
    if old is not None:
        for child in list(old):
            parent.append(child)
    for child in kept:
        parent.append(child)


def _resolve(kind: str, el: Any, accept: bool) -> None:
    if kind in (INSERT, MOVE_TO):
        _unwrap(el) if accept else _drop(el)
    elif kind in (DELETE, MOVE_FROM):
        _drop(el) if accept else _unwrap(el, to_text=True)
    elif kind == PARAGRAPH_INSERT:
        p = _remove_mark_marker(el)
        if not accept:
            _merge_paragraph_into_next(p)
    elif kind == PARAGRAPH_DELETE:
        p = _remove_mark_marker(el)
        if accept:
            _merge_paragraph_into_next(p)
    elif kind == ROW_INSERT:
        row = el.getparent().getparent()
        if accept:
            _remove_row_marker(el)
        elif row is not None:
            row.getparent().remove(row)
    elif kind == ROW_DELETE:
        row = el.getparent().getparent()
        if accept and row is not None:
            row.getparent().remove(row)
        else:
            _remove_row_marker(el)
    elif kind == FORMAT:
        if not accept:
            _restore_old_properties(el)
        else:
            _drop(el)


def _remove_row_marker(el: Any) -> None:
    trpr = el.getparent()
    trpr.remove(el)


def _attached(el: Any, root: Any) -> bool:
    return any(a is root for a in el.iterancestors())


def _resolve_all(
    doc: Any,
    accept: bool,
    *,
    author: str | None,
    ids: Collection[int] | None,
) -> int:
    count = 0
    for root in _roots(doc):
        targets = []
        for el in root.iter(_W_INS, _W_DEL, _W_MOVE_FROM, _W_MOVE_TO, *_FORMAT_CHANGES):
            kind = _kind(el)
            if kind is None:
                continue
            if author is not None and (el.get(qn("w:author")) or "") != author:
                continue
            if ids is not None:
                raw = el.get(qn("w:id"))
                if raw is None or not raw.lstrip("-").isdigit() or int(raw) not in ids:
                    continue
            targets.append((kind, el))
        for kind, el in targets:
            if not _attached(el, root):
                continue  # removed together with an ancestor (e.g. rejected insertion)
            _resolve(kind, el, accept)
            count += 1
        if not any(k in (MOVE_FROM, MOVE_TO) for k, _ in _iter_revision_elements([root])):
            for tag in _RANGE_TAGS:
                for marker in list(root.iter(tag)):
                    _drop(marker)
    return count


def accept_revisions(
    doc: Any, *, author: str | None = None, ids: Collection[int] | None = None
) -> int:
    """Accept tracked changes (all, or those by ``author`` / with ``ids``); return the count."""
    return _resolve_all(doc, True, author=author, ids=ids)


def reject_revisions(
    doc: Any, *, author: str | None = None, ids: Collection[int] | None = None
) -> int:
    """Reject tracked changes (all, or those by ``author`` / with ``ids``); return the count."""
    return _resolve_all(doc, False, author=author, ids=ids)


def is_tracking(doc: Any) -> bool:
    """Return whether *Track Changes* is switched on in the document settings."""
    return doc.settings.element.find(qn("w:trackRevisions")) is not None


def set_tracking(doc: Any, enabled: bool = True) -> None:
    """Switch the *Track Changes* setting on or off (new edits made in Word are then tracked)."""
    settings = doc.settings.element
    existing = settings.find(qn("w:trackRevisions"))
    if enabled and existing is None:
        el = OxmlElement("w:trackRevisions")
        # schema order: trackRevisions comes after the view/zoom/removePersonalInformation... group
        anchor = None
        for tag in (
            "w:zoom",
            "w:removePersonalInformation",
            "w:removeDateAndTime",
            "w:doNotDisplayPageBoundaries",
            "w:displayBackgroundShape",
            "w:printPostScriptOverText",
            "w:printFractionalCharacterWidth",
            "w:printFormsData",
            "w:embedTrueTypeFonts",
            "w:embedSystemFonts",
            "w:saveSubsetFonts",
            "w:saveFormsData",
            "w:mirrorMargins",
            "w:alignBordersAndEdges",
            "w:bordersDoNotSurroundHeader",
            "w:bordersDoNotSurroundFooter",
            "w:gutterAtTop",
            "w:hideSpellingErrors",
            "w:hideGrammaticalErrors",
            "w:activeWritingStyle",
            "w:proofState",
            "w:formsDesign",
            "w:attachedTemplate",
            "w:linkStyles",
            "w:stylePaneFormatFilter",
            "w:stylePaneSortMethod",
            "w:documentType",
            "w:mailMerge",
            "w:revisionView",
        ):
            found = settings.find(qn(tag))
            if found is not None:
                anchor = found
        if anchor is not None:
            anchor.addnext(el)
        else:
            settings.insert(0, el)
    elif not enabled and existing is not None:
        settings.remove(existing)


# -- comments ----------------------------------------------------------------


def _anchor_texts(doc: Any) -> dict[int, str]:
    """Collect the text between each ``commentRangeStart``/``commentRangeEnd`` pair."""
    active: dict[int, list[str]] = {}
    done: dict[int, str] = {}
    for el in doc.element.body.iter(
        qn("w:commentRangeStart"), qn("w:commentRangeEnd"), _W_T, _W_DELTEXT
    ):
        if el.tag == qn("w:commentRangeStart"):
            raw = el.get(qn("w:id"))
            if raw is not None and raw.isdigit():
                active[int(raw)] = []
        elif el.tag == qn("w:commentRangeEnd"):
            raw = el.get(qn("w:id"))
            if raw is not None and raw.isdigit() and int(raw) in active:
                done[int(raw)] = "".join(active.pop(int(raw)))
        else:
            for buf in active.values():
                buf.append(el.text or "")
    for cid, buf in active.items():  # unterminated range: keep what we have
        done[cid] = "".join(buf)
    return done


def list_comments(doc: Any) -> list[CommentInfo]:
    """List the comments (id, author, initials, date, text and the text they annotate)."""
    anchors = _anchor_texts(doc)
    result: list[CommentInfo] = []
    for comment in doc.comments:
        ts = comment.timestamp
        result.append(
            CommentInfo(
                id=comment.comment_id,
                author=comment.author or "",
                initials=comment.initials or "",
                date=ts.isoformat() if ts is not None else None,
                text=comment.text,
                anchor_text=anchors.get(comment.comment_id, ""),
            )
        )
    return result


def add_comment(
    doc: Any,
    text: str,
    *,
    paragraph: int | None = None,
    match: str | None = None,
    author: str = "AnyDocument",
    initials: str | None = None,
) -> CommentInfo:
    """Add a comment anchored to a paragraph or to the first occurrence of ``match``.

    ``paragraph`` is an index into the body paragraphs (default: the first paragraph containing
    ``match``, or the first paragraph when neither is given). Whole runs overlapping the match are
    anchored. Raises ``ValueError`` for a bad index, an unmatched text or a paragraph without runs.
    """
    paragraphs = doc.paragraphs
    if paragraph is not None:
        if not 0 <= paragraph < len(paragraphs):
            raise ValueError(f"Paragraph index {paragraph} out of range (0..{len(paragraphs) - 1})")
        target = paragraphs[paragraph]
        if match is not None and match not in target.text:
            raise ValueError(f"Text {match!r} not found in paragraph {paragraph}")
    elif match is not None:
        found = next((p for p in paragraphs if match in p.text), None)
        if found is None:
            raise ValueError(f"Text {match!r} not found in the document")
        target = found
    else:
        if not paragraphs:
            raise ValueError("The document has no paragraphs to comment on")
        target = paragraphs[0]
    runs = _runs_for(target, match)
    if not runs:
        raise ValueError("The paragraph has no text runs to anchor a comment to")
    comment = doc.add_comment(
        runs, text=text, author=author, initials=initials or _initials(author)
    )
    return next(c for c in list_comments(doc) if c.id == comment.comment_id)


def _initials(author: str) -> str:
    words = author.split()
    return "".join(w[0] for w in words)[:3].upper() or "AD"


def _runs_for(paragraph: Paragraph, match: str | None) -> list[Any]:
    runs = [r for r in paragraph.runs]
    if match is None:
        return runs
    start = paragraph.text.index(match)
    end = start + len(match)
    pos = 0
    chosen = []
    for run in runs:
        length = len(run.text)
        if length and pos < end and pos + length > start:
            chosen.append(run)
        pos += length
    return chosen


def delete_comment(doc: Any, comment_id: int) -> None:
    """Remove a comment and its anchors; raises ``KeyError`` if it does not exist."""
    comment = doc.comments.get(comment_id)
    if comment is None:
        raise KeyError(f"No comment with id {comment_id}")
    cid = str(comment_id)
    body = doc.element.body
    for tag in ("w:commentRangeStart", "w:commentRangeEnd"):
        for el in list(body.iter(qn(tag))):
            if el.get(qn("w:id")) == cid:
                _drop(el)
    for ref in list(body.iter(qn("w:commentReference"))):
        if ref.get(qn("w:id")) == cid:
            run = ref.getparent()
            run.getparent().remove(run)
    part_el = doc.comments._comments_elm
    for c in list(part_el):
        if c.get(qn("w:id")) == cid:
            part_el.remove(c)
