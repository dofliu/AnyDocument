"""Slide management for PPTX: add, delete, duplicate, reorder, notes (T070)."""

from __future__ import annotations

import copy
from collections.abc import Sequence
from typing import Any

from pptx.opc.constants import RELATIONSHIP_TYPE as RT

from anydoc.core.errors import AnyDocError

_R_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
_R_ATTRS = ("embed", "link", "id", "pict")


def _slides(prs: Any) -> Any:
    return prs.slides


def _check_index(prs: Any, index: int, *, allow_end: bool = False) -> None:
    limit = len(prs.slides) + (1 if allow_end else 0)
    if not 0 <= index < limit:
        raise IndexError(f"No such slide: {index}")


def find_layout(prs: Any, layout: int | str) -> Any:
    """Return a slide layout by 0-based index or by (case-insensitive) name."""
    layouts = list(prs.slide_layouts)
    if isinstance(layout, int):
        if not 0 <= layout < len(layouts):
            raise ValueError(f"No such layout: {layout}")
        return layouts[layout]
    for item in layouts:
        if item.name.lower() == layout.lower():
            return item
    raise ValueError(f"No such layout: {layout!r}")


def _move_id(prs: Any, from_index: int, to_index: int) -> None:
    id_list = prs.slides._sldIdLst
    items = list(id_list)
    item = items.pop(from_index)
    id_list.remove(item)
    id_list.insert(to_index, item)


def _fill_text_frame(frame: Any, text: str | Sequence[str]) -> None:
    lines = [text] if isinstance(text, str) else list(text)
    frame.text = lines[0] if lines else ""
    for line in lines[1:]:
        frame.add_paragraph().text = line


def _body_placeholder(slide: Any) -> Any | None:
    for ph in slide.placeholders:
        if ph.placeholder_format.idx != 0 and ph.has_text_frame:
            type_name = str(ph.placeholder_format.type)
            if any(k in type_name for k in ("BODY", "OBJECT", "SUBTITLE")):
                return ph
    return None


def add_slide(
    prs: Any,
    layout: int | str = 1,
    *,
    index: int | None = None,
    title: str | None = None,
    body: str | Sequence[str] | None = None,
    notes: str | None = None,
) -> int:
    """Add a slide based on ``layout`` (index or name) and return its 0-based index.

    ``title`` fills the title placeholder; ``body`` (text or list of lines) fills the first
    body/content/subtitle placeholder. ``index`` inserts before that position (default: end).
    """
    slide_layout = find_layout(prs, layout)
    if index is not None:
        _check_index(prs, index, allow_end=True)
    slide = prs.slides.add_slide(slide_layout)
    if title is not None:
        if slide.shapes.title is None:
            raise AnyDocError(f"Layout {slide_layout.name!r} has no title placeholder")
        slide.shapes.title.text_frame.text = title
    if body is not None:
        target = _body_placeholder(slide)
        if target is None:
            raise AnyDocError(f"Layout {slide_layout.name!r} has no body placeholder")
        _fill_text_frame(target.text_frame, body)
    if notes is not None:
        slide.notes_slide.notes_text_frame.text = notes
    new_index = len(prs.slides) - 1
    if index is not None and index != new_index:
        _move_id(prs, new_index, index)
        new_index = index
    return new_index


def delete_slide(prs: Any, index: int) -> None:
    """Delete the slide at ``index`` (its part is dropped from the package)."""
    _check_index(prs, index)
    id_list = prs.slides._sldIdLst
    sld_id = list(id_list)[index]
    prs.part.drop_rel(sld_id.rId)
    id_list.remove(sld_id)


def move_slide(prs: Any, index: int, to: int) -> None:
    """Move the slide at ``index`` so that it ends up at position ``to``."""
    _check_index(prs, index)
    _check_index(prs, to)
    if index != to:
        _move_id(prs, index, to)


def reorder_slides(prs: Any, order: Sequence[int]) -> None:
    """Reorder slides; ``order`` lists existing 0-based indexes in the new order.

    It must be a permutation of all slides.
    """
    count = len(prs.slides)
    if sorted(order) != list(range(count)):
        raise ValueError(f"order must be a permutation of 0..{count - 1}")
    id_list = prs.slides._sldIdLst
    items = list(id_list)
    for item in items:
        id_list.remove(item)
    for i in order:
        id_list.append(items[i])


def duplicate_slide(prs: Any, index: int, *, to: int | None = None) -> int:
    """Copy a slide (shapes, pictures, notes) and return the new slide's index.

    The copy is placed right after the source unless ``to`` gives another position.
    """
    _check_index(prs, index)
    if to is not None:
        _check_index(prs, to)
    source = prs.slides[index]
    dest = prs.slides.add_slide(source.slide_layout)
    for shape in list(dest.shapes):
        shape._element.getparent().remove(shape._element)

    rid_map: dict[str, str] = {}
    for rid, rel in source.part.rels.items():
        if rel.reltype in (RT.SLIDE_LAYOUT, RT.NOTES_SLIDE):
            continue
        if rel.is_external:
            rid_map[rid] = dest.part.relate_to(rel.target_ref, rel.reltype, is_external=True)
        else:
            rid_map[rid] = dest.part.relate_to(rel.target_part, rel.reltype)

    for element in source.shapes._spTree:
        if element.tag.endswith(("}nvGrpSpPr", "}grpSpPr")):
            continue
        new = copy.deepcopy(element)
        for node in new.iter():
            for name in _R_ATTRS:
                key = f"{{{_R_NS}}}{name}"
                old = node.get(key)
                if old is not None and old in rid_map:
                    node.set(key, rid_map[old])
        dest.shapes._spTree.append(new)

    if source.has_notes_slide:
        dest.notes_slide.notes_text_frame.text = source.notes_slide.notes_text_frame.text

    new_index = len(prs.slides) - 1
    target = index + 1 if to is None else to
    if target != new_index:
        _move_id(prs, new_index, target)
        new_index = target
    return new_index


def set_notes(prs: Any, index: int, text: str) -> None:
    """Set (or create) the speaker notes of a slide; an empty string clears them."""
    _check_index(prs, index)
    slide = prs.slides[index]
    if not text and not slide.has_notes_slide:
        return
    slide.notes_slide.notes_text_frame.text = text


def get_notes(prs: Any, index: int) -> str:
    """Return the speaker notes of a slide (empty string when it has none)."""
    _check_index(prs, index)
    slide = prs.slides[index]
    return str(slide.notes_slide.notes_text_frame.text) if slide.has_notes_slide else ""
