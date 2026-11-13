"""DXF dimensions: read measurements and create linear, aligned and radial dimensions.

All functions operate on an ``ezdxf`` drawing; dimensions (DIMENSION entities) are
addressed by their handle. ``measurement`` is always computed from the definition
points, in drawing units, so it stays correct after a dimension was moved, rotated
or scaled; ``text`` is what the dimension displays (the measurement formatted by the
dimension style, or the text override with ``<>`` replaced by it).

A dimension is drawn by an anonymous geometry block (``*D...``). New dimensions are
rendered immediately; ``update_dimensions`` redraws dimensions whose definition
points or style changed. Only dimensions in the XY plane of the WCS are created.
"""

from __future__ import annotations

import math
from collections.abc import Collection, Iterable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any

from ezdxf.render.dim_base import format_text

from anydoc.formats.dxf.entities import (
    Point,
    _check_unlocked,
    _common_attribs,
    _get,
    _matches,
    _number,
    _owner_name,
    _point,
    _positive,
    _pt,
    _resolve,
    _space,
    decode_text,
)

__all__ = [
    "DIMENSION_KINDS",
    "DimensionInfo",
    "add_aligned_dimension",
    "add_diameter_dimension",
    "add_linear_dimension",
    "add_radius_dimension",
    "delete_dimensions",
    "dimension_styles",
    "get_dimension",
    "list_dimensions",
    "set_dimension_text",
    "update_dimensions",
]

DIMENSION_KINDS: tuple[str, ...] = (
    "linear",
    "aligned",
    "angular",
    "diameter",
    "radius",
    "angular3p",
    "ordinate",
)

_ANGULAR = ("angular", "angular3p")
_NO_ROUNDING = 1e-9
_ANGLE_NAMES = {"horizontal": 0.0, "vertical": 90.0}
_SPECIAL_CODES = (
    ("%%c", "Ø"),
    ("%%C", "Ø"),
    ("%%d", "°"),
    ("%%D", "°"),
    ("%%p", "±"),
    ("%%P", "±"),
)


@dataclass(frozen=True)
class DimensionInfo:
    """A dimension with its measured value and displayed text.

    ``kind`` is one of ``DIMENSION_KINDS``. ``measurement`` is the length (radius,
    diameter) in drawing units, or the angle in degrees for angular dimensions.
    ``override`` is the user text (``None`` when the measurement is shown as is).
    ``points`` holds the definition points: ``p1``/``p2``/``base`` for linear and
    aligned dimensions, ``center``/``point`` for radius and diameter dimensions.
    ``angle`` is the direction of the dimension line of a linear dimension.
    """

    handle: str
    kind: str
    layout: str
    layer: str
    style: str
    measurement: float
    text: str
    override: str | None
    points: dict[str, tuple[float, float, float]]
    angle: float | None = None

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serialisable dict."""
        return {
            "handle": self.handle,
            "kind": self.kind,
            "layout": self.layout,
            "layer": self.layer,
            "style": self.style,
            "measurement": self.measurement,
            "text": self.text,
            "override": self.override,
            "points": {name: list(point) for name, point in self.points.items()},
            "angle": self.angle,
        }


# -- reading -----------------------------------------------------------------------


def _dimension(doc: Any, handle: str) -> Any:
    entity = _get(doc, handle)
    if entity.dxftype() != "DIMENSION":
        raise ValueError(f"Entity {entity.dxf.handle} is a {entity.dxftype()}, not a DIMENSION")
    return entity


def _kind(dim: Any) -> str:
    index = int(dim.dimtype)
    return DIMENSION_KINDS[index] if 0 <= index < len(DIMENSION_KINDS) else "linear"


def _measurement(dim: Any) -> float:
    try:
        value = dim.get_measurement()
    except Exception:  # incomplete definition points in a foreign drawing
        return 0.0
    try:
        return float(value)
    except TypeError:  # ordinate dimensions measure a point
        return float(value.magnitude)


def _style_value(style: Any, name: str, default: Any) -> Any:
    try:
        value = style.get(name, default)
    except Exception:
        return default
    return default if value is None else value


def _separator(style: Any) -> str:
    try:
        return str(style.get_decimal_separator()) or "."
    except Exception:
        return "."


def _formatted(dim: Any, kind: str, value: float) -> str:
    """Format ``value`` the way the dimension style displays it (as ezdxf renders it)."""
    style = dim.override()
    if kind in _ANGULAR:
        decimals = int(_style_value(style, "dimadec", 2))
        if decimals < 0:
            decimals = int(_style_value(style, "dimdec", 2))
        zeros = int(_style_value(style, "dimazin", 2))
        suppress = (4 if zeros & 1 else 0) | (8 if zeros & 2 else 0)
        return str(format_text(value, None, decimals, suppress, ".")) + "°"
    text = str(
        format_text(
            value * abs(float(_style_value(style, "dimlfac", 1.0))),
            float(_style_value(style, "dimrnd", 0.0)) or None,
            int(_style_value(style, "dimdec", 2)),
            int(_style_value(style, "dimzin", 8)),
            _separator(style),
        )
    )
    post = decode_text(str(_style_value(style, "dimpost", "")))
    if "<>" in post:
        return post.replace("<>", text, 1)
    if kind == "radius":
        return "R" + text
    if kind == "diameter":
        return "Ø" + text
    return text


def _display_text(dim: Any, kind: str, value: float) -> tuple[str, str | None]:
    raw = decode_text(str(dim.dxf.get("text", "")))
    if raw in ("", "<>"):
        return _formatted(dim, kind, value), None
    if raw.strip() == "":
        return "", raw  # a single space suppresses the dimension text
    text = raw.replace("<>", _formatted(dim, kind, value)) if "<>" in raw else raw
    for code, char in _SPECIAL_CODES:
        text = text.replace(code, char)
    return text, raw


def _points(dim: Any, kind: str) -> dict[str, tuple[float, float, float]]:
    def get(name: str) -> tuple[float, float, float]:
        value = dim.dxf.get(name)
        return (0.0, 0.0, 0.0) if value is None else _pt(value)

    if kind in ("linear", "aligned"):
        return {"p1": get("defpoint2"), "p2": get("defpoint3"), "base": get("defpoint")}
    if kind == "radius":
        return {"center": get("defpoint"), "point": get("defpoint4")}
    if kind == "diameter":
        a, b = get("defpoint"), get("defpoint4")
        center = ((a[0] + b[0]) / 2, (a[1] + b[1]) / 2, (a[2] + b[2]) / 2)
        return {"center": center, "point": b, "opposite": a}
    names = ("defpoint", "defpoint2", "defpoint3", "defpoint4", "defpoint5")
    return {name: _pt(dim.dxf.get(name)) for name in names if dim.dxf.get(name) is not None}


def _info(dim: Any) -> DimensionInfo:
    kind = _kind(dim)
    value = _measurement(dim)
    text, override = _display_text(dim, kind, value)
    return DimensionInfo(
        handle=str(dim.dxf.handle),
        kind=kind,
        layout=_owner_name(dim),
        layer=str(dim.dxf.get("layer", "0")),
        style=str(dim.dxf.get("dimstyle", "Standard")),
        measurement=value,
        text=text,
        override=override,
        points=_points(dim, kind),
        angle=float(dim.dxf.get("angle", 0.0)) if kind == "linear" else None,
    )


def get_dimension(doc: Any, handle: str) -> DimensionInfo:
    """Return one dimension by handle.

    Raises ``KeyError`` when the handle does not exist and ``ValueError`` when the
    entity is not a DIMENSION.
    """
    return _info(_dimension(doc, handle))


def _kind_set(kinds: Collection[str] | str | None) -> set[str] | None:
    if kinds is None:
        return None
    if isinstance(kinds, str):
        kinds = [kinds]
    wanted = {k.strip().lower() for k in kinds}
    unknown = sorted(wanted - set(DIMENSION_KINDS))
    if unknown:
        raise ValueError(
            f"Unknown dimension kind: {', '.join(unknown)} (use {', '.join(DIMENSION_KINDS)})"
        )
    return wanted


def list_dimensions(
    doc: Any,
    *,
    layout: str = "Model",
    block: str | None = None,
    layer: str | None = None,
    kinds: Collection[str] | str | None = None,
) -> list[DimensionInfo]:
    """Return the dimensions of ``layout`` (or ``block``), filtered by layer and kind(s)."""
    wanted = _kind_set(kinds)
    return [
        _info(e)
        for e in _space(doc, layout, block)
        if _matches(e, layer, {"DIMENSION"}) and (wanted is None or _kind(e) in wanted)
    ]


def dimension_styles(doc: Any) -> list[str]:
    """Return the names of the dimension styles defined in the drawing."""
    return [str(style.dxf.name) for style in doc.dimstyles]


# -- creating ----------------------------------------------------------------------


def _style_name(doc: Any, name: str | None) -> str:
    """Return the canonical name of dimension style ``name`` (default: current style)."""
    if name is None:
        current = str(doc.header.get("$DIMSTYLE", "Standard"))
        name = current if doc.dimstyles.has_entry(current) else "Standard"
        if not doc.dimstyles.has_entry(name):
            doc.dimstyles.new(name)
    elif not doc.dimstyles.has_entry(name):
        raise KeyError(f"No such dimension style: {name}")
    return str(doc.dimstyles.get(name).dxf.name)


def _overrides(
    doc: Any,
    text_height: float | None,
    arrow_size: float | None,
    decimals: int | None,
    decimal_separator: str | None,
) -> dict[str, Any]:
    override: dict[str, Any] = {}
    if text_height is not None:
        override["dimtxt"] = _positive(text_height, "Text height")
    if arrow_size is not None:
        override["dimasz"] = _positive(arrow_size, "Arrow size")
    if decimals is not None:
        if isinstance(decimals, bool) or not isinstance(decimals, int) or not 0 <= decimals <= 8:
            raise ValueError(f"Decimals must be an integer from 0 to 8, got {decimals!r}")
        override["dimdec"] = decimals
    if decimal_separator is not None:
        if decimal_separator not in (".", ","):
            raise ValueError(f"Decimal separator must be '.' or ',', got {decimal_separator!r}")
        override["dimdsep"] = ord(decimal_separator)
    if doc.dxfversion < "AC1015" and ("dimdec" in override or "dimdsep" in override):
        raise ValueError(
            f"decimals and decimal_separator require DXF R2000 or later "
            f"(drawing is {doc.dxfversion}); use a dimension style instead"
        )
    return override


def _text_arg(text: str | None) -> str:
    if text is None:
        return "<>"
    if "\n" in text or "\r" in text:
        raise ValueError("Dimension text must be a single line")
    return text


def _xy(value: Any, what: str) -> tuple[float, float, float]:
    point = _point(value, what)
    if point[2] != 0.0:
        raise ValueError(f"{what} must lie in the XY plane (z = 0)")
    return point


def _line_angle(value: float | str) -> float:
    if isinstance(value, str):
        named = _ANGLE_NAMES.get(value.strip().lower())
        if named is not None:
            return named
    return _number(value, "Angle")


@contextmanager
def _exact_measurement(doc: Any, dim: Any) -> Iterator[None]:
    """Render without rounding when the dimension style has DIMRND = 0.

    DIMRND 0 means "no rounding", but ezdxf rounds the text to an integer when the
    attribute is present with that value (as in every drawing loaded from a file),
    so the attribute is taken out of the style while the dimension is drawn.
    """
    name = str(dim.dxf.get("dimstyle", "Standard"))
    if not doc.dimstyles.has_entry(name):
        yield
        return
    style = doc.dimstyles.get(name)
    zero = style.dxf.hasattr("dimrnd") and style.dxf.dimrnd == 0
    if zero:
        style.dxf.discard("dimrnd")
    try:
        yield
    finally:
        if zero:
            style.dxf.dimrnd = 0.0


def _render(doc: Any, builder: Any, rgb: tuple[int, int, int] | None) -> str:
    dim = builder.dimension
    with _exact_measurement(doc, dim):
        builder.render()
    if rgb is not None:
        dim.rgb = rgb
    return str(dim.dxf.handle)


def add_linear_dimension(
    doc: Any,
    p1: Point,
    p2: Point,
    *,
    base: Point | None = None,
    distance: float | None = None,
    angle: float | str = 0.0,
    text: str | None = None,
    style: str | None = None,
    text_height: float | None = None,
    arrow_size: float | None = None,
    decimals: int | None = None,
    decimal_separator: str | None = None,
    layout: str = "Model",
    block: str | None = None,
    layer: str | None = None,
    color: int | str | None = None,
) -> str:
    """Add a linear (horizontal, vertical or rotated) dimension and return its handle.

    The dimension measures the distance between ``p1`` and ``p2`` along the
    direction ``angle`` (degrees; ``0``/``"horizontal"`` or ``90``/``"vertical"``).
    The dimension line passes through ``base``, or lies ``distance`` away from
    ``p1`` (positive = left of the measuring direction, i.e. above a horizontal
    dimension); give exactly one of them. ``text`` overrides the displayed text
    (``<>`` stands for the measurement). ``style`` names a dimension style (default:
    the current one); ``text_height``, ``arrow_size``, ``decimals`` and
    ``decimal_separator`` (``"."`` or ``","``) override it. DXF R12 cannot store
    overrides: ``decimals`` and ``decimal_separator`` are rejected and the sizes
    are lost when the dimension is redrawn.
    """
    start, end = _xy(p1, "p1"), _xy(p2, "p2")
    direction = _line_angle(angle)
    if (base is None) == (distance is None):
        raise ValueError("Give exactly one of base and distance")
    rad = math.radians(direction)
    ux, uy = math.cos(rad), math.sin(rad)
    if base is not None:
        location = _xy(base, "base")
    else:
        offset = _number(distance, "Distance")
        location = (start[0] - uy * offset, start[1] + ux * offset, 0.0)
    if math.isclose((end[0] - start[0]) * ux + (end[1] - start[1]) * uy, 0.0, abs_tol=1e-12):
        raise ValueError("p1 and p2 have no extent along the dimension direction")
    content = _text_arg(text)
    dimstyle = _style_name(doc, style)
    override = _overrides(doc, text_height, arrow_size, decimals, decimal_separator)
    attribs, rgb = _common_attribs(doc, layer, color, None)
    builder = _space(doc, layout, block).add_linear_dim(
        base=location,
        p1=start,
        p2=end,
        angle=direction,
        text=content,
        dimstyle=dimstyle,
        override=override,
        dxfattribs=attribs,
    )
    return _render(doc, builder, rgb)


def add_aligned_dimension(
    doc: Any,
    p1: Point,
    p2: Point,
    distance: float,
    *,
    text: str | None = None,
    style: str | None = None,
    text_height: float | None = None,
    arrow_size: float | None = None,
    decimals: int | None = None,
    decimal_separator: str | None = None,
    layout: str = "Model",
    block: str | None = None,
    layer: str | None = None,
    color: int | str | None = None,
) -> str:
    """Add an aligned dimension (parallel to ``p1``-``p2``) and return its handle.

    The dimension line lies ``distance`` away from the measured line (positive =
    left of the direction ``p1`` to ``p2``). See ``add_linear_dimension`` for the
    other options.
    """
    start, end = _xy(p1, "p1"), _xy(p2, "p2")
    if math.isclose(math.dist(start, end), 0.0, abs_tol=1e-12):
        raise ValueError("p1 and p2 must be different points")
    offset = _number(distance, "Distance")
    content = _text_arg(text)
    dimstyle = _style_name(doc, style)
    override = _overrides(doc, text_height, arrow_size, decimals, decimal_separator)
    attribs, rgb = _common_attribs(doc, layer, color, None)
    builder = _space(doc, layout, block).add_aligned_dim(
        p1=start,
        p2=end,
        distance=offset,
        text=content,
        dimstyle=dimstyle,
        override=override,
        dxfattribs=attribs,
    )
    # ezdxf builds a rotated linear dimension; mark it as a real aligned dimension.
    builder.dimension.dxf.dimtype = (int(builder.dimension.dxf.dimtype) & ~7) | 1
    return _render(doc, builder, rgb)


def _circle(
    doc: Any, center: Point | None, radius: float | None, entity: str | None
) -> tuple[tuple[float, float, float], float]:
    if entity is not None:
        if center is not None or radius is not None:
            raise ValueError("Give either entity or center and radius, not both")
        source = _get(doc, entity)
        if source.dxftype() not in ("CIRCLE", "ARC"):
            raise ValueError(
                f"Entity {source.dxf.handle} is a {source.dxftype()}, not a CIRCLE or ARC"
            )
        extrusion = source.dxf.get("extrusion")
        if extrusion is not None and not extrusion.isclose((0, 0, 1)):
            raise ValueError(f"Entity {source.dxf.handle} is not in the XY plane")
        return _xy(_pt(source.dxf.center), "Center"), float(source.dxf.radius)
    if center is None or radius is None:
        raise ValueError("Give center and radius, or entity")
    return _xy(center, "Center"), _positive(radius, "Radius")


def _add_radial(
    doc: Any,
    diameter: bool,
    center: Point | None,
    radius: float | None,
    entity: str | None,
    angle: float,
    text: str | None,
    style: str | None,
    options: tuple[float | None, float | None, int | None, str | None],
    layout: str,
    block: str | None,
    layer: str | None,
    color: int | str | None,
) -> str:
    middle, size = _circle(doc, center, radius, entity)
    direction = _number(angle, "Angle")
    content = _text_arg(text)
    dimstyle = _style_name(doc, style)
    override = _overrides(doc, *options)
    attribs, rgb = _common_attribs(doc, layer, color, None)
    space = _space(doc, layout, block)
    add = space.add_diameter_dim if diameter else space.add_radius_dim
    builder = add(
        center=middle,
        radius=size,
        angle=direction,
        text=content,
        dimstyle=dimstyle,
        override=override,
        dxfattribs=attribs,
    )
    return _render(doc, builder, rgb)


def add_radius_dimension(
    doc: Any,
    center: Point | None = None,
    radius: float | None = None,
    *,
    entity: str | None = None,
    angle: float = 0.0,
    text: str | None = None,
    style: str | None = None,
    text_height: float | None = None,
    arrow_size: float | None = None,
    decimals: int | None = None,
    decimal_separator: str | None = None,
    layout: str = "Model",
    block: str | None = None,
    layer: str | None = None,
    color: int | str | None = None,
) -> str:
    """Add a radius dimension and return its handle.

    The circle is given by ``center`` and ``radius``, or taken from the CIRCLE/ARC
    with handle ``entity``. ``angle`` (degrees) is the direction from the centre to
    the dimensioned point on the circle. See ``add_linear_dimension`` for the other
    options.
    """
    options = (text_height, arrow_size, decimals, decimal_separator)
    return _add_radial(
        doc,
        False,
        center,
        radius,
        entity,
        angle,
        text,
        style,
        options,
        layout,
        block,
        layer,
        color,
    )


def add_diameter_dimension(
    doc: Any,
    center: Point | None = None,
    radius: float | None = None,
    *,
    entity: str | None = None,
    angle: float = 0.0,
    text: str | None = None,
    style: str | None = None,
    text_height: float | None = None,
    arrow_size: float | None = None,
    decimals: int | None = None,
    decimal_separator: str | None = None,
    layout: str = "Model",
    block: str | None = None,
    layer: str | None = None,
    color: int | str | None = None,
) -> str:
    """Add a diameter dimension and return its handle; see ``add_radius_dimension``."""
    options = (text_height, arrow_size, decimals, decimal_separator)
    return _add_radial(
        doc, True, center, radius, entity, angle, text, style, options, layout, block, layer, color
    )


# -- editing -----------------------------------------------------------------------


def _drop_geometry(doc: Any, name: str | None, *, keep: Any = None) -> None:
    """Delete geometry block ``name`` unless another dimension still uses it."""
    if not name or not str(name).startswith("*") or name not in doc.blocks:
        return
    for entity in doc.entitydb.values():
        if (
            entity is not keep
            and entity.is_alive
            and entity.dxftype() == "DIMENSION"
            and str(entity.dxf.get("geometry", "")).lower() == str(name).lower()
        ):
            return
    doc.blocks.delete_block(name, safe=False)


def _rerender(doc: Any, dim: Any) -> None:
    old = dim.dxf.get("geometry")
    if _kind(dim) == "aligned":
        # The renderer takes the direction of an aligned dimension from ``angle``.
        start, end = dim.dxf.get("defpoint2"), dim.dxf.get("defpoint3")
        if start is not None and end is not None and not start.isclose(end):
            dim.dxf.angle = math.degrees(math.atan2(end.y - start.y, end.x - start.x))
    override = dim.override()
    if override.dimstyle_attribs.get("dimrnd") == 0:
        # An explicit "no rounding" override: keep it, in a form ezdxf does not
        # mistake for rounding to integers.
        override.dimstyle_attribs["dimrnd"] = _NO_ROUNDING
    with _exact_measurement(doc, dim):
        override.render()
    if dim.dxf.get("geometry") != old:
        _drop_geometry(doc, old, keep=dim)


def _dimensions(doc: Any, handles: Iterable[str] | str) -> list[Any]:
    entities = _resolve(doc, handles)
    for entity in entities:
        if entity.dxftype() != "DIMENSION":
            raise ValueError(f"Entity {entity.dxf.handle} is a {entity.dxftype()}, not a DIMENSION")
    return entities


def set_dimension_text(doc: Any, handle: str, text: str | None) -> DimensionInfo:
    """Override the displayed text of a dimension and redraw it.

    ``<>`` in ``text`` stands for the measurement; ``None`` (or ``"<>"``) restores
    the plain measurement and a single space hides the text. Dimensions on locked
    layers cannot be changed.
    """
    dim = _dimension(doc, handle)
    _check_unlocked(doc, dim)
    dim.dxf.text = _text_arg(text)
    _rerender(doc, dim)
    return _info(dim)


def update_dimensions(
    doc: Any, handles: Iterable[str] | str | None = None, *, layout: str = "Model"
) -> list[str]:
    """Redraw dimensions from their definition points and return their handles.

    Use this after dimensions were moved, rotated or scaled: the measurement
    follows the definition points, but the drawn text does not until the dimension
    is redrawn. ``handles=None`` redraws every dimension of ``layout`` that is not
    on a locked layer. Nothing is changed when a handle is invalid.
    """
    if handles is None:
        dims = [
            e for e in _space(doc, layout) if e.dxftype() == "DIMENSION" and not _is_locked(doc, e)
        ]
    else:
        dims = _dimensions(doc, handles)
    for dim in dims:
        _rerender(doc, dim)
    return [str(dim.dxf.handle) for dim in dims]


def _is_locked(doc: Any, entity: Any) -> bool:
    name = str(entity.dxf.get("layer", "0"))
    return bool(doc.layers.has_entry(name) and doc.layers.get(name).is_locked())


def delete_dimensions(doc: Any, handles: Iterable[str] | str) -> int:
    """Delete dimensions together with their geometry blocks; returns the count.

    All handles are checked first (existing, DIMENSION entities, not on a locked
    layer), so nothing is deleted when one of them is invalid.
    """
    dims = _dimensions(doc, handles)
    for dim in dims:
        geometry = dim.dxf.get("geometry")
        dim.get_layout().delete_entity(dim)
        _drop_geometry(doc, geometry)
    return len(dims)
