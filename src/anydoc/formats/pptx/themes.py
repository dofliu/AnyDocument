"""Layouts, slide masters and themes for PPTX (T073).

Covers: listing layouts/masters, switching a slide to another layout, renaming and
pruning layouts, backgrounds (slide/layout/master), master text styles, theme colours
and fonts, copying the theme of another presentation and resizing the slide.
"""

from __future__ import annotations

import copy
import io
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pptx import Presentation
from pptx.enum.shapes import PP_PLACEHOLDER
from pptx.opc.constants import RELATIONSHIP_TYPE as RT
from pptx.opc.oxml import serialize_part_xml
from pptx.oxml import parse_xml
from pptx.oxml.ns import qn

from anydoc.core.errors import AnyDocError
from anydoc.formats.pptx.shapes import Length, _rgb, _slide, to_emu

THEME_COLORS: tuple[str, ...] = (
    "dk1",
    "lt1",
    "dk2",
    "lt2",
    "accent1",
    "accent2",
    "accent3",
    "accent4",
    "accent5",
    "accent6",
    "hlink",
    "folHlink",
)

SLIDE_SIZES: dict[str, tuple[int, int]] = {
    "16:9": (12192000, 6858000),
    "4:3": (9144000, 6858000),
    "16:10": (10972800, 6858000),
    "a4": (10692000, 7560000),
}

_TITLE_TYPES = {
    PP_PLACEHOLDER.TITLE,
    PP_PLACEHOLDER.CENTER_TITLE,
    PP_PLACEHOLDER.VERTICAL_TITLE,
}
_BODY_TYPES = {
    PP_PLACEHOLDER.BODY,
    PP_PLACEHOLDER.OBJECT,
    PP_PLACEHOLDER.SUBTITLE,
    PP_PLACEHOLDER.VERTICAL_BODY,
    PP_PLACEHOLDER.VERTICAL_OBJECT,
}
_LATENT_TYPES = {
    PP_PLACEHOLDER.DATE,
    PP_PLACEHOLDER.FOOTER,
    PP_PLACEHOLDER.SLIDE_NUMBER,
}
_STYLE_TAGS = {"title": "p:titleStyle", "body": "p:bodyStyle", "other": "p:otherStyle"}


@dataclass(frozen=True)
class PlaceholderInfo:
    """A placeholder of a layout or master: its ``idx``, type name and shape name."""

    idx: int
    type: str
    name: str


@dataclass(frozen=True)
class LayoutInfo:
    """A slide layout: global index, name, owning master, placeholders and users."""

    index: int
    name: str
    master: int
    placeholders: list[PlaceholderInfo]
    used_by: list[int] = field(default_factory=list)


@dataclass(frozen=True)
class MasterInfo:
    """A slide master with the names of its layouts and its placeholders."""

    index: int
    name: str
    theme: str
    layouts: list[str]
    placeholders: list[PlaceholderInfo]


@dataclass(frozen=True)
class ThemeInfo:
    """Theme name, the 12 scheme colours (``RRGGBB``) and heading/body fonts."""

    name: str
    colors: dict[str, str]
    major_font: str
    minor_font: str
    major_east_asian: str
    minor_east_asian: str


@dataclass(frozen=True)
class TextStyle:
    """Default run formatting of a master text style level (``None`` = not set)."""

    size: float | None
    bold: bool | None
    italic: bool | None
    color: str | None
    font: str | None
    east_asian_font: str | None


# -- helpers -----------------------------------------------------------------------


def _type_name(ph_type: Any) -> str:
    return str(ph_type).split(" ")[0].lower()


def _placeholders(owner: Any) -> list[PlaceholderInfo]:
    return [
        PlaceholderInfo(
            idx=int(ph.placeholder_format.idx),
            type=_type_name(ph.placeholder_format.type),
            name=str(ph.name),
        )
        for ph in owner.placeholders
    ]


def _master(prs: Any, index: int) -> Any:
    masters = list(prs.slide_masters)
    if not 0 <= index < len(masters):
        raise IndexError(f"No such slide master: {index}")
    return masters[index]


def all_layouts(prs: Any) -> list[Any]:
    """Return the layouts of every slide master, in master order."""
    return [layout for master in prs.slide_masters for layout in master.slide_layouts]


def find_layout(prs: Any, layout: int | str) -> Any:
    """Return a layout by global 0-based index or (case-insensitive) name, over all masters."""
    layouts = all_layouts(prs)
    if isinstance(layout, bool):
        raise ValueError(f"No such layout: {layout!r}")
    if isinstance(layout, int):
        if not 0 <= layout < len(layouts):
            raise ValueError(f"No such layout: {layout}")
        return layouts[layout]
    for item in layouts:
        if item.name.lower() == layout.lower():
            return item
    raise ValueError(f"No such layout: {layout!r}")


def _category(ph_type: Any) -> str:
    if ph_type in _TITLE_TYPES:
        return "title"
    if ph_type in _BODY_TYPES:
        return "body"
    return _type_name(ph_type)


# -- layouts and masters --------------------------------------------------------------


def list_layouts(prs: Any) -> list[LayoutInfo]:
    """Return every layout (all masters) with its placeholders and the slides using it."""
    masters = list(prs.slide_masters)
    users: dict[int, list[int]] = {}
    for i, slide in enumerate(prs.slides):
        users.setdefault(id(slide.slide_layout.part), []).append(i)
    result: list[LayoutInfo] = []
    for m_index, master in enumerate(masters):
        for layout in master.slide_layouts:
            result.append(
                LayoutInfo(
                    index=len(result),
                    name=str(layout.name),
                    master=m_index,
                    placeholders=_placeholders(layout),
                    used_by=users.get(id(layout.part), []),
                )
            )
    return result


def list_masters(prs: Any) -> list[MasterInfo]:
    """Return every slide master with its theme name, layouts and placeholders."""
    return [
        MasterInfo(
            index=i,
            name=str(master.name),
            theme=theme_info(prs, i).name,
            layouts=[str(layout.name) for layout in master.slide_layouts],
            placeholders=_placeholders(master),
        )
        for i, master in enumerate(prs.slide_masters)
    ]


def rename_layout(prs: Any, layout: int | str, name: str) -> None:
    """Rename a layout; the new name must be non-empty and unique."""
    target = find_layout(prs, layout)
    if not name.strip():
        raise ValueError("Layout name must not be empty")
    for other in all_layouts(prs):
        if other.part is not target.part and other.name.lower() == name.lower():
            raise ValueError(f"Layout name already in use: {name!r}")
    target.name = name


def remove_unused_layouts(prs: Any) -> list[str]:
    """Delete layouts no slide uses (each master keeps at least one); return their names."""
    used = {id(slide.slide_layout.part) for slide in prs.slides}
    removed: list[str] = []
    for master in prs.slide_masters:
        for layout in list(master.slide_layouts):
            if id(layout.part) in used or len(master.slide_layouts) <= 1:
                continue
            removed.append(str(layout.name))
            master.slide_layouts.remove(layout)
    return removed


def _geometry(shape: Any) -> tuple[Any, Any, Any, Any]:
    return shape.left, shape.top, shape.width, shape.height


def _is_empty(shape: Any) -> bool:
    if getattr(shape, "has_text_frame", False):
        return not shape.text_frame.text.strip()
    return bool(shape._element.tag == qn("p:sp"))


def _clear_own_xfrm(element: Any) -> None:
    sp_pr = element.find(qn("p:spPr"))
    if sp_pr is not None:
        xfrm = sp_pr.find(qn("a:xfrm"))
        if xfrm is not None:
            sp_pr.remove(xfrm)


def set_slide_layout(
    prs: Any, slide: int, layout: int | str, *, keep_positions: bool = False
) -> None:
    """Switch a slide to another layout, like PowerPoint's *Layout* command.

    Placeholders are re-bound to the new layout's placeholders of the same category
    (title, body/content, picture, ...) in order and, unless ``keep_positions`` is true,
    take the new layout's position and size. Placeholders with content that have no
    counterpart stay where they were; empty ones are removed, and placeholders the new
    layout offers in addition are created. Other shapes are untouched.
    """
    target = _slide(prs, slide)
    new_layout = find_layout(prs, layout)
    if target.slide_layout.part is new_layout.part:
        return

    existing = [(ph, _geometry(ph)) for ph in target.placeholders]
    available = list(new_layout.placeholders)
    pairs: list[tuple[Any, tuple[Any, Any, Any, Any], Any | None]] = []
    for ph, geometry in existing:
        category = _category(ph.placeholder_format.type)
        match = next(
            (c for c in available if _category(c.placeholder_format.type) == category), None
        )
        if match is not None:
            available.remove(match)
        pairs.append((ph, geometry, match))

    part = target.part
    for rid, rel in list(part.rels.items()):
        if rel.reltype == RT.SLIDE_LAYOUT:
            part.rels.pop(rid)
    part.relate_to(new_layout.part, RT.SLIDE_LAYOUT)

    taken = {int(m.placeholder_format.idx) for _, _, m in pairs if m is not None}
    taken |= {int(c.placeholder_format.idx) for c in available}
    for ph, geometry, match in pairs:
        element = ph._element
        if match is None:
            if _is_empty(ph):
                element.getparent().remove(element)
                continue
            if None not in geometry:
                ph.left, ph.top, ph.width, ph.height = geometry
            idx = int(element.ph.get("idx", "0"))
            if idx in taken:
                idx = max(taken) + 1
                element.ph.set("idx", str(idx))
            taken.add(idx)
            continue
        source = match._element.ph
        for name in ("type", "idx", "orient", "sz"):
            value = source.get(name)
            if value is None:
                element.ph.attrib.pop(name, None)
            else:
                element.ph.set(name, value)
        if keep_positions:
            if None not in geometry:
                ph.left, ph.top, ph.width, ph.height = geometry
        else:
            _clear_own_xfrm(element)

    for extra in available:
        if extra.placeholder_format.type not in _LATENT_TYPES:
            target.shapes.clone_placeholder(extra)


# -- backgrounds ------------------------------------------------------------------


def _background_owners(
    prs: Any, slide: int | None, layout: int | str | None, master: int | None
) -> list[Any]:
    given = [x is not None for x in (slide, layout, master)]
    if sum(given) > 1:
        raise ValueError("Give only one of slide, layout or master")
    if slide is not None:
        return [_slide(prs, slide)]
    if layout is not None:
        return [find_layout(prs, layout)]
    if master is not None:
        return [_master(prs, master)]
    return list(prs.slide_masters)


def set_background(
    prs: Any,
    color: str | None,
    *,
    slide: int | None = None,
    layout: int | str | None = None,
    master: int | None = None,
) -> None:
    """Set a solid background colour (``RRGGBB``) on a slide, a layout or a master.

    Without a target every slide master is changed (the whole deck, except slides or
    layouts that carry their own background). ``None`` removes the target's own
    background so that it inherits again (not allowed on a master).
    """
    owners = _background_owners(prs, slide, layout, master)
    if color is None:
        if slide is None and layout is None:
            raise ValueError("A slide master always needs a background")
        c_sld = owners[0]._element.cSld
        bg = c_sld.find(qn("p:bg"))
        if bg is not None:
            c_sld.remove(bg)
        return
    rgb = _rgb(color)
    for owner in owners:
        fill = owner.background.fill
        fill.solid()
        fill.fore_color.rgb = rgb


def get_background(
    prs: Any,
    *,
    slide: int | None = None,
    layout: int | str | None = None,
    master: int | None = None,
) -> str | None:
    """Return the target's own solid background colour, or ``None`` when inherited/other.

    Without a target the first slide master is read.
    """
    owner = _background_owners(prs, slide, layout, master)[0]
    bg = owner._element.cSld.find(qn("p:bg"))
    if bg is None:
        return None
    node = bg.find(f"{qn('p:bgPr')}/{qn('a:solidFill')}/{qn('a:srgbClr')}")
    return str(node.get("val")).upper() if node is not None else None


# -- master text styles -----------------------------------------------------------


def _style_level(prs: Any, kind: str, level: int, master: int, *, create: bool) -> Any | None:
    if kind not in _STYLE_TAGS:
        raise ValueError(f"Unknown text style: {kind!r} (expected title, body or other)")
    if not 1 <= level <= 9:
        raise ValueError("level must be between 1 and 9")
    root = _master(prs, master)._element
    styles = root.find(qn("p:txStyles"))
    if styles is None:
        if not create:
            return None
        styles = parse_xml(
            '<p:txStyles xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"/>'
        )
        root.append(styles)
    holder = styles.find(qn(_STYLE_TAGS[kind]))
    if holder is None:
        if not create:
            return None
        holder = styles.makeelement(qn(_STYLE_TAGS[kind]), {})
        order = list(_STYLE_TAGS.values())
        later = [styles.find(qn(t)) for t in order[order.index(_STYLE_TAGS[kind]) + 1 :]]
        anchor = next((n for n in later if n is not None), None)
        if anchor is not None:
            anchor.addprevious(holder)
        else:
            styles.insert(0, holder)
    tag = qn(f"a:lvl{level}pPr")
    node = holder.find(tag)
    if node is None and create:
        node = holder.makeelement(tag, {})
        following = [holder.find(qn(f"a:lvl{n}pPr")) for n in range(level + 1, 10)]
        anchor = next((n for n in following if n is not None), None)
        if anchor is None:
            anchor = holder.find(qn("a:extLst"))
        if anchor is not None:
            anchor.addprevious(node)
        else:
            holder.append(node)
    return node


_RPR_ORDER = ("a:ln", "a:solidFill", "a:latin", "a:ea", "a:cs", "a:sym")


def _set_rpr_child(rpr: Any, tag: str, attrs: dict[str, str], inner: Any | None = None) -> None:
    old = rpr.find(qn(tag))
    if old is not None:
        rpr.remove(old)
    if tag == "a:solidFill":
        for other in ("a:noFill", "a:gradFill", "a:blipFill", "a:pattFill", "a:grpFill"):
            node = rpr.find(qn(other))
            if node is not None:
                rpr.remove(node)
    new = rpr.makeelement(qn(tag), attrs)
    if inner is not None:
        new.append(inner)
    position = _RPR_ORDER.index(tag)
    for later in (*_RPR_ORDER[position + 1 :], "a:hlinkClick", "a:hlinkMouseOver", "a:extLst"):
        anchor = rpr.find(qn(later))
        if anchor is not None:
            anchor.addprevious(new)
            return
    rpr.append(new)


def set_master_text_style(
    prs: Any,
    kind: str,
    *,
    level: int = 1,
    master: int = 0,
    size: float | None = None,
    bold: bool | None = None,
    italic: bool | None = None,
    color: str | None = None,
    font: str | None = None,
    east_asian_font: str | None = None,
) -> None:
    """Change the default text formatting a slide master gives to titles or body text.

    ``kind`` is ``title``, ``body`` or ``other``; ``level`` (1-9) selects the outline
    level. ``font`` sets the Latin typeface and, unless ``east_asian_font`` is given,
    the East Asian one as well. Every slide that does not override the value follows.
    """
    if size is not None and size <= 0:
        raise ValueError("size must be positive")
    rgb = _rgb(color) if color is not None else None
    node = _style_level(prs, kind, level, master, create=True)
    assert node is not None
    rpr = node.find(qn("a:defRPr"))
    if rpr is None:
        rpr = node.makeelement(qn("a:defRPr"), {})
        ext = node.find(qn("a:extLst"))
        if ext is not None:
            ext.addprevious(rpr)
        else:
            node.append(rpr)
    if size is not None:
        rpr.set("sz", str(round(size * 100)))
    if bold is not None:
        rpr.set("b", "1" if bold else "0")
    if italic is not None:
        rpr.set("i", "1" if italic else "0")
    if rgb is not None:
        _set_rpr_child(
            rpr, "a:solidFill", {}, rpr.makeelement(qn("a:srgbClr"), {"val": str(rgb).upper()})
        )
    if font is not None:
        _set_rpr_child(rpr, "a:latin", {"typeface": font})
    ea = east_asian_font if east_asian_font is not None else font
    if ea is not None:
        _set_rpr_child(rpr, "a:ea", {"typeface": ea})


def master_text_style(prs: Any, kind: str, *, level: int = 1, master: int = 0) -> TextStyle:
    """Return the default run formatting of a master text style level."""
    node = _style_level(prs, kind, level, master, create=False)
    rpr = node.find(qn("a:defRPr")) if node is not None else None
    if rpr is None:
        return TextStyle(None, None, None, None, None, None)

    def flag(name: str) -> bool | None:
        value = rpr.get(name)
        return None if value is None else value in ("1", "true")

    def face(tag: str) -> str | None:
        child = rpr.find(qn(tag))
        return str(child.get("typeface")) if child is not None else None

    rgb = rpr.find(f"{qn('a:solidFill')}/{qn('a:srgbClr')}")
    size = rpr.get("sz")
    return TextStyle(
        size=int(size) / 100 if size is not None else None,
        bold=flag("b"),
        italic=flag("i"),
        color=str(rgb.get("val")).upper() if rgb is not None else None,
        font=face("a:latin"),
        east_asian_font=face("a:ea"),
    )


# -- themes -----------------------------------------------------------------------


def _theme_part(prs: Any, master: int) -> Any:
    try:
        return _master(prs, master).part.part_related_by(RT.THEME)
    except KeyError:
        raise AnyDocError(f"Slide master {master} has no theme part") from None


def _theme_root(part: Any) -> Any:
    try:
        return parse_xml(part.blob)
    except Exception as exc:
        raise AnyDocError(f"Cannot parse theme: {exc}") from exc


def _store_theme(part: Any, root: Any) -> None:
    part._blob = serialize_part_xml(root)


def _elements(root: Any) -> Any:
    elements = root.find(qn("a:themeElements"))
    if elements is None:
        raise AnyDocError("Theme has no themeElements")
    return elements


def _color_value(node: Any) -> str:
    for child in node:
        if child.tag == qn("a:srgbClr"):
            return str(child.get("val")).upper()
        if child.tag == qn("a:sysClr"):
            return str(child.get("lastClr", "000000")).upper()
    return "000000"


def _font_face(scheme: Any, which: str, tag: str) -> str:
    holder = scheme.find(qn(f"a:{which}Font")) if scheme is not None else None
    node = holder.find(qn(tag)) if holder is not None else None
    return str(node.get("typeface", "")) if node is not None else ""


def _info(root: Any) -> ThemeInfo:
    elements = _elements(root)
    scheme = elements.find(qn("a:clrScheme"))
    colors: dict[str, str] = {}
    if scheme is not None:
        for name in THEME_COLORS:
            node = scheme.find(qn(f"a:{name}"))
            if node is not None:
                colors[name] = _color_value(node)
    fonts = elements.find(qn("a:fontScheme"))
    return ThemeInfo(
        name=str(root.get("name", "")),
        colors=colors,
        major_font=_font_face(fonts, "major", "a:latin"),
        minor_font=_font_face(fonts, "minor", "a:latin"),
        major_east_asian=_font_face(fonts, "major", "a:ea"),
        minor_east_asian=_font_face(fonts, "minor", "a:ea"),
    )


def theme_info(prs: Any, master: int = 0) -> ThemeInfo:
    """Return the theme (name, scheme colours, fonts) of a slide master."""
    return _info(_theme_root(_theme_part(prs, master)))


def _master_indexes(prs: Any, master: int | None) -> list[int]:
    if master is None:
        return list(range(len(prs.slide_masters)))
    _master(prs, master)
    return [master]


def set_theme_colors(prs: Any, colors: Mapping[str, str], *, master: int | None = None) -> None:
    """Change theme scheme colours (keys from ``THEME_COLORS``, values ``RRGGBB``).

    Everything coloured with a theme colour (text, shapes, charts, tables) follows.
    ``master`` limits the change to one slide master (default: all).
    """
    values: dict[str, str] = {}
    for name, color in colors.items():
        if name not in THEME_COLORS:
            raise ValueError(f"Unknown theme color: {name!r} (expected one of {THEME_COLORS})")
        values[name] = str(_rgb(color)).upper()
    for index in _master_indexes(prs, master):
        part = _theme_part(prs, index)
        root = _theme_root(part)
        scheme = _elements(root).find(qn("a:clrScheme"))
        if scheme is None:
            raise AnyDocError("Theme has no color scheme")
        for name, value in values.items():
            node = scheme.find(qn(f"a:{name}"))
            if node is None:
                raise AnyDocError(f"Theme color scheme lacks {name!r}")
            for child in list(node):
                node.remove(child)
            node.append(node.makeelement(qn("a:srgbClr"), {"val": value}))
        _store_theme(part, root)


def _set_font(scheme: Any, which: str, latin: str | None, east_asian: str | None) -> None:
    holder = scheme.find(qn(f"a:{which}Font"))
    if holder is None:
        raise AnyDocError(f"Theme font scheme lacks {which}Font")
    if latin is not None:
        node = holder.find(qn("a:latin"))
        if node is None:
            node = holder.makeelement(qn("a:latin"), {})
            holder.insert(0, node)
        node.set("typeface", latin)
    if east_asian is not None:
        node = holder.find(qn("a:ea"))
        if node is None:
            node = holder.makeelement(qn("a:ea"), {})
            anchor = holder.find(qn("a:latin"))
            if anchor is not None:
                anchor.addnext(node)
            else:
                holder.insert(0, node)
        node.set("typeface", east_asian)
        for item in holder.findall(qn("a:font")):
            if item.get("script") == "Hant":
                item.set("typeface", east_asian)


def set_theme_fonts(
    prs: Any,
    *,
    major: str | None = None,
    minor: str | None = None,
    major_east_asian: str | None = None,
    minor_east_asian: str | None = None,
    master: int | None = None,
) -> None:
    """Change the theme's heading (major) and body (minor) fonts.

    The East Asian names also update the Traditional Chinese (``Hant``) script entry.
    """
    names = (major, minor, major_east_asian, minor_east_asian)
    if all(n is None for n in names):
        raise ValueError("Give at least one font")
    if any(n is not None and not n.strip() for n in names):
        raise ValueError("Font names must not be empty")
    for index in _master_indexes(prs, master):
        part = _theme_part(prs, index)
        root = _theme_root(part)
        scheme = _elements(root).find(qn("a:fontScheme"))
        if scheme is None:
            raise AnyDocError("Theme has no font scheme")
        _set_font(scheme, "major", major, major_east_asian)
        _set_font(scheme, "minor", minor, minor_east_asian)
        _store_theme(part, root)


def _open_source(source: Any) -> Any:
    if isinstance(source, str | Path):
        if not Path(source).is_file():
            raise AnyDocError(f"Presentation not found: {source}")
        data: Any = str(source)
    elif isinstance(source, bytes):
        data = io.BytesIO(source)
    else:
        return getattr(source, "native", source)
    try:
        return Presentation(data)
    except Exception as exc:
        raise AnyDocError(f"Invalid PPTX: {exc}") from exc


def _has_relationship_refs(node: Any) -> bool:
    prefix = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
    return any(key.startswith(prefix) for item in node.iter() for key in item.attrib)


def apply_theme(
    prs: Any,
    source: Any,
    *,
    colors: bool = True,
    fonts: bool = True,
    effects: bool = True,
    master: int | None = None,
) -> ThemeInfo:
    """Copy the theme of another presentation (path, bytes or opened document).

    The colour scheme, font scheme and format (fill/line/effect) scheme of the source's
    first slide master replace those of this presentation's masters; layouts and
    placeholder geometry are left alone. A format scheme that references embedded
    images is skipped. Returns the resulting theme of the first changed master.
    """
    donor = _elements(_theme_root(_theme_part(_open_source(source), 0)))
    donor_name = _theme_root(_theme_part(_open_source(source), 0)).get("name", "")
    wanted = [("a:clrScheme", colors), ("a:fontScheme", fonts), ("a:fmtScheme", effects)]
    indexes = _master_indexes(prs, master)
    for index in indexes:
        part = _theme_part(prs, index)
        root = _theme_root(part)
        elements = _elements(root)
        for tag, enabled in wanted:
            new = donor.find(qn(tag))
            if not enabled or new is None:
                continue
            if tag == "a:fmtScheme" and _has_relationship_refs(new):
                continue
            old = elements.find(qn(tag))
            clone = copy.deepcopy(new)
            if old is not None:
                elements.replace(old, clone)
            else:
                elements.append(clone)
        if colors and fonts and donor_name:
            root.set("name", donor_name)
        _store_theme(part, root)
    return theme_info(prs, indexes[0])


# -- slide size -------------------------------------------------------------------


def parse_slide_size(size: str | tuple[Length, Length] | list[Any]) -> tuple[int, int]:
    """Resolve a named size (``16:9``, ``4:3``, ``16:10``, ``A4``) or ``(width, height)``."""
    if isinstance(size, str):
        try:
            return SLIDE_SIZES[size.strip().lower()]
        except KeyError:
            raise ValueError(
                f"Unknown slide size: {size!r} (expected one of {tuple(SLIDE_SIZES)})"
            ) from None
    if len(size) != 2:
        raise ValueError("Slide size must be a name or (width, height)")
    width, height = to_emu(size[0]), to_emu(size[1])
    if width <= 0 or height <= 0:
        raise ValueError("Slide size must be positive")
    return width, height


def _scale_tree(sp_tree: Any, sx: float, sy: float) -> None:
    for element in sp_tree:
        xfrm = None
        for path in ("p:spPr/a:xfrm", "p:grpSpPr/a:xfrm", "p:xfrm"):
            holder = element
            for step in path.split("/"):
                holder = holder.find(qn(step)) if holder is not None else None
            if holder is not None:
                xfrm = holder
                break
        if xfrm is None:
            continue
        off, ext = xfrm.find(qn("a:off")), xfrm.find(qn("a:ext"))
        if off is not None:
            off.set("x", str(round(int(off.get("x", "0")) * sx)))
            off.set("y", str(round(int(off.get("y", "0")) * sy)))
        if ext is not None:
            ext.set("cx", str(round(int(ext.get("cx", "0")) * sx)))
            ext.set("cy", str(round(int(ext.get("cy", "0")) * sy)))


def set_slide_size(
    prs: Any, size: str | tuple[Length, Length] | list[Any], *, scale: bool = True
) -> tuple[int, int]:
    """Change the slide size; return the new ``(width, height)`` in EMU.

    With ``scale`` (default) every shape of the masters, layouts and slides is stretched
    by the width/height ratios so that the content still fills the slide.
    """
    width, height = parse_slide_size(size)
    old_w, old_h = int(prs.slide_width), int(prs.slide_height)
    prs.slide_width, prs.slide_height = width, height
    if scale and (old_w, old_h) != (width, height) and old_w > 0 and old_h > 0:
        sx, sy = width / old_w, height / old_h
        owners = [*prs.slide_masters, *all_layouts(prs), *prs.slides]
        for owner in owners:
            _scale_tree(owner.shapes._spTree, sx, sy)
    return width, height
