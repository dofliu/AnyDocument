"""Text boxes, text formatting, pictures, basic shapes and geometry for PPTX (T071).

Shapes are addressed by ``(slide index, shape id)``; the id is the one reported by
``ShapeInfo.shape_id``. Lengths are EMU when numeric, or a string with a unit
(``"2.5cm"``, ``"30mm"``, ``"1in"``, ``"12pt"``, ``"914400emu"``).
"""

from __future__ import annotations

import io
import re
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pptx.dml.color import RGBColor
from pptx.enum.shapes import MSO_CONNECTOR, MSO_SHAPE, MSO_SHAPE_TYPE
from pptx.enum.text import MSO_ANCHOR, MSO_AUTO_SIZE, PP_ALIGN
from pptx.oxml.ns import qn
from pptx.util import Emu, Pt

from anydoc.core.errors import AnyDocError

Length = int | float | str

_EMU_PER_UNIT = {"emu": 1, "pt": 12700, "mm": 36000, "cm": 360000, "in": 914400}
_LENGTH_RE = re.compile(r"^\s*(-?\d+(?:\.\d+)?)\s*([a-zA-Z]+)\s*$")
_COLOR_RE = re.compile(r"^#?([0-9a-fA-F]{6})$")

SHAPE_KINDS: dict[str, Any] = {
    "rectangle": MSO_SHAPE.RECTANGLE,
    "rounded_rectangle": MSO_SHAPE.ROUNDED_RECTANGLE,
    "oval": MSO_SHAPE.OVAL,
    "triangle": MSO_SHAPE.ISOSCELES_TRIANGLE,
    "right_triangle": MSO_SHAPE.RIGHT_TRIANGLE,
    "diamond": MSO_SHAPE.DIAMOND,
    "parallelogram": MSO_SHAPE.PARALLELOGRAM,
    "trapezoid": MSO_SHAPE.TRAPEZOID,
    "pentagon": MSO_SHAPE.REGULAR_PENTAGON,
    "hexagon": MSO_SHAPE.HEXAGON,
    "octagon": MSO_SHAPE.OCTAGON,
    "star": MSO_SHAPE.STAR_5_POINT,
    "right_arrow": MSO_SHAPE.RIGHT_ARROW,
    "left_arrow": MSO_SHAPE.LEFT_ARROW,
    "up_arrow": MSO_SHAPE.UP_ARROW,
    "down_arrow": MSO_SHAPE.DOWN_ARROW,
    "chevron": MSO_SHAPE.CHEVRON,
    "heart": MSO_SHAPE.HEART,
    "cloud": MSO_SHAPE.CLOUD,
    "callout": MSO_SHAPE.RECTANGULAR_CALLOUT,
}

_ALIGN = {
    "left": PP_ALIGN.LEFT,
    "center": PP_ALIGN.CENTER,
    "right": PP_ALIGN.RIGHT,
    "justify": PP_ALIGN.JUSTIFY,
}
_ANCHOR = {"top": MSO_ANCHOR.TOP, "middle": MSO_ANCHOR.MIDDLE, "bottom": MSO_ANCHOR.BOTTOM}
_AUTO_SIZE = {
    "none": MSO_AUTO_SIZE.NONE,
    "shape": MSO_AUTO_SIZE.SHAPE_TO_FIT_TEXT,
    "text": MSO_AUTO_SIZE.TEXT_TO_FIT_SHAPE,
}


@dataclass(frozen=True)
class RunFormat:
    """Formatting of one text run; ``None`` means inherited from the theme/layout."""

    text: str
    bold: bool | None = None
    italic: bool | None = None
    underline: bool | None = None
    size: float | None = None
    color: str | None = None
    font: str | None = None


@dataclass(frozen=True)
class ParagraphFormat:
    """One paragraph of a shape: alignment, indent level and its runs."""

    text: str
    align: str | None
    level: int
    runs: list[RunFormat]


@dataclass(frozen=True)
class ShapeFormat:
    """Geometry and appearance of a shape as read back from the presentation."""

    shape_id: int
    name: str
    left: int | None
    top: int | None
    width: int | None
    height: int | None
    rotation: float
    fill: str | None
    line_color: str | None
    line_width: float | None
    anchor: str | None
    word_wrap: bool | None
    paragraphs: list[ParagraphFormat]


# -- helpers -----------------------------------------------------------------


def to_emu(value: Length) -> int:
    """Convert a length (EMU number or ``"<n><unit>"`` string) to integer EMU."""
    if isinstance(value, bool):
        raise ValueError(f"Invalid length: {value!r}")
    if isinstance(value, int | float):
        return round(value)
    match = _LENGTH_RE.match(value)
    if match is None or match.group(2).lower() not in _EMU_PER_UNIT:
        raise ValueError(f"Invalid length: {value!r}")
    return round(float(match.group(1)) * _EMU_PER_UNIT[match.group(2).lower()])


def _rgb(color: str) -> Any:
    match = _COLOR_RE.match(color.strip())
    if match is None:
        raise ValueError(f"Invalid color (expected RRGGBB): {color!r}")
    value = match.group(1)
    make: Any = RGBColor  # python-pptx ships no type hints for this constructor
    return make(int(value[:2], 16), int(value[2:4], 16), int(value[4:], 16))


def _slide(prs: Any, index: int) -> Any:
    if not 0 <= index < len(prs.slides):
        raise IndexError(f"No such slide: {index}")
    return prs.slides[index]


def _walk(shapes: Any) -> Any:
    for shape in shapes:
        yield shape
        if shape.shape_type == MSO_SHAPE_TYPE.GROUP:
            yield from _walk(shape.shapes)


def find_shape(prs: Any, slide: int, shape_id: int) -> Any:
    """Return the python-pptx shape with ``shape_id`` on a slide (groups are searched)."""
    for shape in _walk(_slide(prs, slide).shapes):
        if shape.shape_id == shape_id:
            return shape
    raise AnyDocError(f"No shape with id {shape_id} on slide {slide}")


def _text_frame(shape: Any) -> Any:
    if not getattr(shape, "has_text_frame", False):
        raise AnyDocError(f"Shape {shape.shape_id} ({shape.name!r}) cannot hold text")
    return shape.text_frame


def _size(value: Length, what: str) -> int:
    emu = to_emu(value)
    if emu <= 0:
        raise ValueError(f"{what} must be positive")
    return emu


def _apply_font(
    font: Any,
    *,
    bold: bool | None,
    italic: bool | None,
    underline: bool | None,
    size: float | None,
    color: str | None,
    font_name: str | None,
) -> None:
    if bold is not None:
        font.bold = bold
    if italic is not None:
        font.italic = italic
    if underline is not None:
        font.underline = underline
    if size is not None:
        if size <= 0:
            raise ValueError("size must be positive")
        font.size = Pt(size)
    if color is not None:
        font.color.rgb = _rgb(color)
    if font_name is not None:
        font.name = font_name
        # python-pptx only sets <a:latin>; CJK text needs <a:ea> to pick the typeface.
        r_pr = font._element
        ea = r_pr.find(qn("a:ea"))
        if ea is None:
            ea = r_pr.makeelement(qn("a:ea"), {})
            latin = r_pr.find(qn("a:latin"))
            latin.addnext(ea)
        ea.set("typeface", font_name)


def _align(value: str) -> Any:
    try:
        return _ALIGN[value.lower()]
    except KeyError:
        raise ValueError(f"Invalid alignment: {value!r}") from None


def _write_text(frame: Any, text: str | Sequence[str]) -> None:
    lines = [text] if isinstance(text, str) else list(text)
    frame.text = lines[0] if lines else ""
    for line in lines[1:]:
        frame.add_paragraph().text = line


def _apply_fill_line(
    shape: Any,
    *,
    fill: str | None,
    line_color: str | None,
    line_width: float | None,
) -> None:
    if fill is not None:
        if not hasattr(shape, "fill"):
            raise AnyDocError(f"Shape {shape.shape_id} has no fill")
        if fill.lower() == "none":
            shape.fill.background()
        else:
            shape.fill.solid()
            shape.fill.fore_color.rgb = _rgb(fill)
    if line_color is not None:
        if line_color.lower() == "none":
            shape.line.fill.background()
        else:
            shape.line.color.rgb = _rgb(line_color)
    if line_width is not None:
        if line_width < 0:
            raise ValueError("line_width must not be negative")
        shape.line.width = Pt(line_width)


# -- text --------------------------------------------------------------------


def add_textbox(
    prs: Any,
    slide: int,
    text: str | Sequence[str],
    *,
    left: Length,
    top: Length,
    width: Length,
    height: Length,
    bold: bool | None = None,
    italic: bool | None = None,
    underline: bool | None = None,
    size: float | None = None,
    color: str | None = None,
    font: str | None = None,
    align: str | None = None,
    anchor: str | None = None,
    word_wrap: bool = True,
    fill: str | None = None,
    line_color: str | None = None,
    line_width: float | None = None,
) -> int:
    """Add a text box and return its shape id.

    ``text`` is a string or a list of paragraphs; font options apply to all of it.
    """
    box = _slide(prs, slide).shapes.add_textbox(
        to_emu(left), to_emu(top), _size(width, "width"), _size(height, "height")
    )
    box.text_frame.word_wrap = word_wrap
    _write_text(box.text_frame, text)
    format_text(
        prs,
        slide,
        box.shape_id,
        bold=bold,
        italic=italic,
        underline=underline,
        size=size,
        color=color,
        font=font,
        align=align,
        anchor=anchor,
    )
    _apply_fill_line(box, fill=fill, line_color=line_color, line_width=line_width)
    return int(box.shape_id)


def set_text(prs: Any, slide: int, shape_id: int, text: str | Sequence[str]) -> None:
    """Replace the text of a shape (string or list of paragraphs).

    The first run's font settings of each kept paragraph are not preserved; apply
    ``format_text`` afterwards when specific formatting is needed.
    """
    _write_text(_text_frame(find_shape(prs, slide, shape_id)), text)


def add_paragraph(
    prs: Any,
    slide: int,
    shape_id: int,
    text: str,
    *,
    level: int = 0,
    bold: bool | None = None,
    italic: bool | None = None,
    underline: bool | None = None,
    size: float | None = None,
    color: str | None = None,
    font: str | None = None,
    align: str | None = None,
) -> int:
    """Append a paragraph to a shape's text and return its 0-based index."""
    if not 0 <= level <= 8:
        raise ValueError("level must be between 0 and 8")
    frame = _text_frame(find_shape(prs, slide, shape_id))
    para = frame.add_paragraph()
    para.level = level
    run = para.add_run()
    run.text = text
    _apply_font(
        run.font,
        bold=bold,
        italic=italic,
        underline=underline,
        size=size,
        color=color,
        font_name=font,
    )
    if align is not None:
        para.alignment = _align(align)
    return len(frame.paragraphs) - 1


def format_text(
    prs: Any,
    slide: int,
    shape_id: int,
    *,
    paragraph: int | None = None,
    bold: bool | None = None,
    italic: bool | None = None,
    underline: bool | None = None,
    size: float | None = None,
    color: str | None = None,
    font: str | None = None,
    align: str | None = None,
    level: int | None = None,
    anchor: str | None = None,
    word_wrap: bool | None = None,
    auto_size: str | None = None,
) -> None:
    """Format the text of a shape (all paragraphs, or just ``paragraph``).

    Font options are applied to every run; ``align``/``level`` to the paragraphs;
    ``anchor`` (top/middle/bottom), ``word_wrap`` and ``auto_size`` (none/shape/text)
    to the whole text frame.
    """
    frame = _text_frame(find_shape(prs, slide, shape_id))
    paragraphs = list(frame.paragraphs)
    if paragraph is not None:
        if not 0 <= paragraph < len(paragraphs):
            raise IndexError(f"No such paragraph: {paragraph}")
        paragraphs = [paragraphs[paragraph]]
    if level is not None and not 0 <= level <= 8:
        raise ValueError("level must be between 0 and 8")
    alignment = _align(align) if align is not None else None
    for para in paragraphs:
        if alignment is not None:
            para.alignment = alignment
        if level is not None:
            para.level = level
        for run in para.runs:
            _apply_font(
                run.font,
                bold=bold,
                italic=italic,
                underline=underline,
                size=size,
                color=color,
                font_name=font,
            )
    if anchor is not None:
        if anchor.lower() not in _ANCHOR:
            raise ValueError(f"Invalid anchor: {anchor!r}")
        frame.vertical_anchor = _ANCHOR[anchor.lower()]
    if word_wrap is not None:
        frame.word_wrap = word_wrap
    if auto_size is not None:
        if auto_size.lower() not in _AUTO_SIZE:
            raise ValueError(f"Invalid auto_size: {auto_size!r}")
        frame.auto_size = _AUTO_SIZE[auto_size.lower()]


# -- pictures and shapes -----------------------------------------------------


def add_picture(
    prs: Any,
    slide: int,
    image: Path | str | bytes,
    *,
    left: Length,
    top: Length,
    width: Length | None = None,
    height: Length | None = None,
) -> int:
    """Insert a picture and return its shape id.

    With only one of ``width``/``height`` the aspect ratio is kept; with neither the
    image's native size is used.
    """
    target = _slide(prs, slide)
    source: Any
    if isinstance(image, bytes):
        source = io.BytesIO(image)
    else:
        if not Path(image).is_file():
            raise AnyDocError(f"Image not found: {image}")
        source = str(image)
    try:
        pic = target.shapes.add_picture(
            source,
            to_emu(left),
            to_emu(top),
            _size(width, "width") if width is not None else None,
            _size(height, "height") if height is not None else None,
        )
    except ValueError:
        raise
    except Exception as exc:
        raise AnyDocError(f"Cannot insert picture: {exc}") from exc
    return int(pic.shape_id)


def add_shape(
    prs: Any,
    slide: int,
    kind: str,
    *,
    left: Length,
    top: Length,
    width: Length,
    height: Length,
    text: str | Sequence[str] | None = None,
    fill: str | None = None,
    line_color: str | None = None,
    line_width: float | None = None,
) -> int:
    """Add a basic auto shape (see ``SHAPE_KINDS``) and return its shape id."""
    key = kind.lower().replace("-", "_").replace(" ", "_")
    if key not in SHAPE_KINDS:
        raise ValueError(f"Unknown shape kind: {kind!r} (known: {', '.join(SHAPE_KINDS)})")
    shape = _slide(prs, slide).shapes.add_shape(
        SHAPE_KINDS[key],
        to_emu(left),
        to_emu(top),
        _size(width, "width"),
        _size(height, "height"),
    )
    if text is not None:
        _write_text(shape.text_frame, text)
    _apply_fill_line(shape, fill=fill, line_color=line_color, line_width=line_width)
    return int(shape.shape_id)


def add_line(
    prs: Any,
    slide: int,
    *,
    x1: Length,
    y1: Length,
    x2: Length,
    y2: Length,
    color: str | None = None,
    width: float | None = None,
) -> int:
    """Add a straight line from ``(x1, y1)`` to ``(x2, y2)`` and return its shape id."""
    line = _slide(prs, slide).shapes.add_connector(
        MSO_CONNECTOR.STRAIGHT, to_emu(x1), to_emu(y1), to_emu(x2), to_emu(y2)
    )
    _apply_fill_line(line, fill=None, line_color=color, line_width=width)
    return int(line.shape_id)


def style_shape(
    prs: Any,
    slide: int,
    shape_id: int,
    *,
    fill: str | None = None,
    line_color: str | None = None,
    line_width: float | None = None,
) -> None:
    """Set the fill colour, outline colour (``"none"`` removes either) and outline width."""
    _apply_fill_line(
        find_shape(prs, slide, shape_id), fill=fill, line_color=line_color, line_width=line_width
    )


def set_geometry(
    prs: Any,
    slide: int,
    shape_id: int,
    *,
    left: Length | None = None,
    top: Length | None = None,
    width: Length | None = None,
    height: Length | None = None,
    rotation: float | None = None,
) -> None:
    """Move, resize and/or rotate (degrees clockwise) a shape; omitted values are kept."""
    shape = find_shape(prs, slide, shape_id)
    if left is not None:
        shape.left = Emu(to_emu(left))
    if top is not None:
        shape.top = Emu(to_emu(top))
    if width is not None:
        shape.width = Emu(_size(width, "width"))
    if height is not None:
        shape.height = Emu(_size(height, "height"))
    if rotation is not None:
        if not hasattr(shape, "rotation"):
            raise AnyDocError(f"Shape {shape_id} cannot be rotated")
        shape.rotation = rotation % 360


def delete_shape(prs: Any, slide: int, shape_id: int) -> None:
    """Remove a shape from a slide."""
    element = find_shape(prs, slide, shape_id)._element
    element.getparent().remove(element)


def set_z_order(prs: Any, slide: int, shape_id: int, position: int | str) -> None:
    """Change stacking order: ``"front"``, ``"back"`` or a 0-based position (0 = bottom)."""
    target = _slide(prs, slide)
    shape = find_shape(prs, slide, shape_id)
    tree = target.shapes._spTree
    element = shape._element
    if element.getparent() is not tree:
        raise AnyDocError("Shapes inside a group cannot be reordered")
    siblings = [s._element for s in target.shapes if s._element is not element]
    if position == "front":
        pos = len(siblings)
    elif position == "back":
        pos = 0
    elif isinstance(position, int) and not isinstance(position, bool):
        if not 0 <= position <= len(siblings):
            raise IndexError(f"No such position: {position}")
        pos = position
    else:
        raise ValueError(f"Invalid position: {position!r}")
    tree.remove(element)
    if pos >= len(siblings):
        tree.append(element)
    else:
        siblings[pos].addprevious(element)


# -- read back ---------------------------------------------------------------


def _color_of(color_format: Any) -> str | None:
    try:
        if color_format is None or color_format.type is None:
            return None
        return str(color_format.rgb)
    except (AttributeError, TypeError):
        return None  # theme colours have no explicit RGB


def _run_format(run: Any) -> RunFormat:
    font = run.font
    return RunFormat(
        text=run.text,
        bold=font.bold,
        italic=font.italic,
        underline=bool(font.underline) if font.underline is not None else None,
        size=font.size.pt if font.size is not None else None,
        color=_color_of(font.color),
        font=font.name,
    )


def _fill_color(shape: Any) -> str | None:
    fill = getattr(shape, "fill", None)
    if fill is None or fill.type is None:
        return None
    try:
        return _color_of(fill.fore_color)
    except TypeError:
        return None  # no-fill / picture / gradient fills have no foreground colour


def _line_format(shape: Any) -> tuple[str | None, float | None]:
    if not hasattr(shape, "line"):
        return None, None
    line = shape.line
    color = _color_of(line.color) if line.fill.type is not None else None
    ln = line._ln
    width = line.width.pt if ln is not None and ln.get("w") is not None else None
    return color, width


def shape_format(prs: Any, slide: int, shape_id: int) -> ShapeFormat:
    """Return geometry, fill/outline and per-run text formatting of a shape."""
    shape = find_shape(prs, slide, shape_id)
    paragraphs: list[ParagraphFormat] = []
    anchor: str | None = None
    word_wrap: bool | None = None
    if getattr(shape, "has_text_frame", False):
        frame = shape.text_frame
        names_align = {v: k for k, v in _ALIGN.items()}
        names_anchor = {v: k for k, v in _ANCHOR.items()}
        anchor = names_anchor.get(frame.vertical_anchor)
        word_wrap = frame.word_wrap
        paragraphs = [
            ParagraphFormat(
                text=p.text,
                align=names_align.get(p.alignment),
                level=p.level,
                runs=[_run_format(r) for r in p.runs],
            )
            for p in frame.paragraphs
        ]
    line_color, line_width = _line_format(shape)
    return ShapeFormat(
        shape_id=shape.shape_id,
        name=shape.name,
        left=shape.left,
        top=shape.top,
        width=shape.width,
        height=shape.height,
        rotation=float(getattr(shape, "rotation", 0.0)),
        fill=_fill_color(shape),
        line_color=line_color,
        line_width=line_width,
        anchor=anchor,
        word_wrap=word_wrap,
        paragraphs=paragraphs,
    )
