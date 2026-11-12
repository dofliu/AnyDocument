"""DXF entity editing: add, modify, delete, move, rotate, scale and copy entities.

All functions operate on an ``ezdxf`` drawing and address entities by their handle
(a hexadecimal string, matched case-insensitively). New entities are placed on the
current layer (``$CLAYER``) unless ``layer`` is given. Points are ``(x, y)`` or
``(x, y, z)`` sequences; polyline vertices are ``(x, y)`` or ``(x, y, bulge)``.
Angles are in degrees, counter-clockwise.

Entity colours are an ACI index (1-255), ``0``/``"byblock"``, ``256``/``"bylayer"``,
a colour name (see ``anydoc.formats.dxf.layers.COLOR_NAMES``) or ``#RRGGBB``.
Entities on locked layers cannot be modified, deleted or transformed.
"""

from __future__ import annotations

import math
from collections.abc import Collection, Iterable, Sequence
from dataclasses import dataclass, field
from typing import Any

from ezdxf.entities.dxfgfx import DXFGraphic
from ezdxf.lldxf.encoding import decode_dxf_unicode, has_dxf_unicode
from ezdxf.math import Matrix44

from anydoc.formats.dxf.layers import _ensure_linetype, current_layer, parse_color

__all__ = [
    "EDITABLE_TYPES",
    "EntityDetail",
    "add_arc",
    "add_circle",
    "add_line",
    "add_mtext",
    "add_polyline",
    "add_text",
    "copy_entities",
    "decode_text",
    "delete_entities",
    "get_entity",
    "list_entities",
    "modify_entity",
    "move_entities",
    "parse_entity_color",
    "rotate_entities",
    "scale_entities",
    "select",
]

Point = Sequence[float]

EDITABLE_TYPES: tuple[str, ...] = ("LINE", "CIRCLE", "ARC", "LWPOLYLINE", "TEXT", "MTEXT")

_SUB_ENTITIES = ("ATTRIB", "VERTEX", "SEQEND")

BYBLOCK = 0
BYLAYER = 256

# Geometry properties that ``modify_entity`` accepts for each entity type.
_PROPERTIES: dict[str, frozenset[str]] = {
    "LINE": frozenset({"start", "end"}),
    "CIRCLE": frozenset({"center", "radius"}),
    "ARC": frozenset({"center", "radius", "start_angle", "end_angle"}),
    "LWPOLYLINE": frozenset({"points", "closed"}),
    "TEXT": frozenset({"text", "insert", "height", "rotation"}),
    "MTEXT": frozenset({"text", "insert", "height", "rotation", "width"}),
}


@dataclass(frozen=True)
class EntityDetail:
    """An entity with its common properties and type-specific ``geometry``.

    ``color`` is the ACI index (``256`` = BYLAYER, ``0`` = BYBLOCK); ``true_color`` is
    ``#RRGGBB`` when set. ``layout`` is the name of the layout or block that owns the
    entity. ``geometry`` holds e.g. ``start``/``end`` for a LINE (empty for entity
    types this module cannot edit geometrically).
    """

    handle: str
    type: str
    layer: str
    layout: str
    color: int
    linetype: str
    true_color: str | None = None
    geometry: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serialisable dict (points become lists)."""
        return {
            "handle": self.handle,
            "type": self.type,
            "layer": self.layer,
            "layout": self.layout,
            "color": self.color,
            "true_color": self.true_color,
            "linetype": self.linetype,
            **{k: _jsonable(v) for k, v in self.geometry.items()},
        }


def _jsonable(value: Any) -> Any:
    if isinstance(value, tuple):
        return [_jsonable(v) for v in value]
    if isinstance(value, list):
        return [_jsonable(v) for v in value]
    return value


# -- validation helpers ------------------------------------------------------------


def _number(value: Any, what: str) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ValueError(f"{what} must be a number, got {value!r}")
    number = float(value)
    if not math.isfinite(number):
        raise ValueError(f"{what} must be finite, got {value!r}")
    return number


def _positive(value: Any, what: str) -> float:
    number = _number(value, what)
    if number <= 0:
        raise ValueError(f"{what} must be greater than 0, got {value!r}")
    return number


def _point(value: Any, what: str = "Point") -> tuple[float, float, float]:
    if isinstance(value, str) or not isinstance(value, Sequence) or len(value) not in (2, 3):
        raise ValueError(f"{what} must be (x, y) or (x, y, z), got {value!r}")
    coords = [_number(v, what) for v in value]
    return coords[0], coords[1], coords[2] if len(coords) == 3 else 0.0


def _vertices(points: Any) -> list[tuple[float, float, float]]:
    """Validate polyline vertices and return them as ``(x, y, bulge)`` tuples."""
    if isinstance(points, str) or not isinstance(points, Iterable):
        raise ValueError(f"Polyline points must be a sequence of points, got {points!r}")
    out: list[tuple[float, float, float]] = []
    for value in points:
        if isinstance(value, str) or not isinstance(value, Sequence) or len(value) not in (2, 3):
            raise ValueError(f"Polyline vertex must be (x, y) or (x, y, bulge), got {value!r}")
        coords = [_number(v, "Polyline vertex") for v in value]
        out.append((coords[0], coords[1], coords[2] if len(coords) == 3 else 0.0))
    if len(out) < 2:
        raise ValueError("A polyline needs at least 2 points")
    return out


def parse_entity_color(value: int | str) -> tuple[int | None, tuple[int, int, int] | None]:
    """Parse an entity colour into ``(aci, rgb)``; exactly one is not ``None``.

    Like ``layers.parse_color`` but also accepts ``0``/``"byblock"`` and
    ``256``/``"bylayer"``.
    """
    if isinstance(value, str):
        text = value.strip().lower()
        if text == "bylayer":
            return BYLAYER, None
        if text == "byblock":
            return BYBLOCK, None
        if text in ("0", "256"):
            return int(text), None
    elif not isinstance(value, bool) and value in (BYBLOCK, BYLAYER):
        return int(value), None
    try:
        return parse_color(value)
    except ValueError as exc:
        raise ValueError(str(exc).replace("Layer colour", "Colour").replace("layer ", "")) from None


def _color(doc: Any, value: int | str) -> tuple[int | None, tuple[int, int, int] | None]:
    aci, rgb = parse_entity_color(value)
    if rgb is not None and doc.dxfversion < "AC1018":
        raise ValueError(f"True colour requires DXF R2004 or later (drawing is {doc.dxfversion})")
    return aci, rgb


def _layer_name(doc: Any, name: str | None) -> str:
    """Return the canonical name of layer ``name`` (default: the current layer)."""
    if name is None:
        name = current_layer(doc)
        if not doc.layers.has_entry(name):
            return name
    if not doc.layers.has_entry(name):
        raise KeyError(f"No such layer: {name}")
    return str(doc.layers.get(name).dxf.name)


def _layout(doc: Any, name: str) -> Any:
    if name not in doc.layouts:
        raise KeyError(f"No such layout: {name}")
    return doc.layouts.get(name)


def _common_attribs(
    doc: Any, layer: str | None, color: int | str | None, linetype: str | None
) -> tuple[dict[str, Any], tuple[int, int, int] | None]:
    attribs: dict[str, Any] = {"layer": _layer_name(doc, layer)}
    rgb: tuple[int, int, int] | None = None
    if color is not None:
        aci, rgb = _color(doc, color)
        if aci is not None:
            attribs["color"] = aci
    if linetype is not None:
        attribs["linetype"] = _linetype_name(doc, linetype)
    return attribs, rgb


def _linetype_name(doc: Any, name: str) -> str:
    if name.lower() in ("bylayer", "byblock"):
        return name.upper()
    return _ensure_linetype(doc, name)


def _requires_r2000(doc: Any, kind: str) -> None:
    if doc.dxfversion < "AC1015":
        raise ValueError(f"{kind} requires DXF R2000 or later (drawing is {doc.dxfversion})")


def _finish(entity: Any, rgb: tuple[int, int, int] | None) -> str:
    if rgb is not None:
        entity.rgb = rgb
    return str(entity.dxf.handle)


# -- lookup ------------------------------------------------------------------------


def _get(doc: Any, handle: str) -> Any:
    entity = doc.entitydb.get(str(handle).strip().upper())
    if entity is None or not isinstance(entity, DXFGraphic) or not entity.is_alive:
        raise KeyError(f"No such entity: {handle}")
    return entity


def _owner_name(entity: Any) -> str:
    layout = entity.get_layout()
    return "" if layout is None else str(layout.name)


def decode_text(text: str) -> str:
    """Decode ``\\U+XXXX`` escapes, which drawings in a non-Unicode code page use for CJK."""
    return str(decode_dxf_unicode(text)) if has_dxf_unicode(text) else text


def _pt(vec: Any) -> tuple[float, float, float]:
    return float(vec.x), float(vec.y), float(vec.z)


def _geometry(entity: Any) -> dict[str, Any]:
    kind = entity.dxftype()
    dxf = entity.dxf
    if kind == "LINE":
        return {"start": _pt(dxf.start), "end": _pt(dxf.end)}
    if kind == "CIRCLE":
        return {"center": _pt(dxf.center), "radius": float(dxf.radius)}
    if kind == "ARC":
        return {
            "center": _pt(dxf.center),
            "radius": float(dxf.radius),
            "start_angle": float(dxf.start_angle),
            "end_angle": float(dxf.end_angle),
        }
    if kind == "LWPOLYLINE":
        return {
            "points": [(float(x), float(y), float(b)) for x, y, b in entity.get_points("xyb")],
            "closed": bool(entity.closed),
        }
    if kind == "TEXT":
        return {
            "text": decode_text(str(dxf.text)),
            "insert": _pt(entity.get_placement()[1]),
            "height": float(dxf.height),
            "rotation": float(dxf.get("rotation", 0.0)),
        }
    if kind == "MTEXT":
        return {
            "text": decode_text(str(entity.plain_text())),
            "insert": _pt(dxf.insert),
            "height": float(dxf.char_height),
            "rotation": float(entity.get_rotation()),
            "width": float(dxf.get("width", 0.0)),
        }
    return {}


def _detail(entity: Any) -> EntityDetail:
    rgb = entity.rgb
    return EntityDetail(
        handle=str(entity.dxf.handle),
        type=str(entity.dxftype()),
        layer=str(entity.dxf.get("layer", "0")),
        layout=_owner_name(entity),
        color=int(entity.dxf.get("color", BYLAYER)),
        linetype=str(entity.dxf.get("linetype", "BYLAYER")),
        true_color=None if rgb is None else "#{:02X}{:02X}{:02X}".format(*rgb),
        geometry=_geometry(entity),
    )


def get_entity(doc: Any, handle: str) -> EntityDetail:
    """Return one entity by handle; raises ``KeyError`` when it does not exist."""
    return _detail(_get(doc, handle))


def _matches(entity: Any, layer: str | None, types: Collection[str] | None) -> bool:
    if layer is not None and str(entity.dxf.get("layer", "0")).lower() != layer.lower():
        return False
    return types is None or entity.dxftype() in types


def _type_set(types: Collection[str] | str | None) -> set[str] | None:
    if types is None:
        return None
    if isinstance(types, str):
        types = [types]
    return {t.strip().upper() for t in types}


def list_entities(
    doc: Any,
    *,
    layout: str = "Model",
    layer: str | None = None,
    types: Collection[str] | str | None = None,
) -> list[EntityDetail]:
    """Return the entities of ``layout``, optionally filtered by layer and DXF type(s)."""
    wanted = _type_set(types)
    return [_detail(e) for e in _layout(doc, layout) if _matches(e, layer, wanted)]


def select(
    doc: Any,
    *,
    layout: str = "Model",
    layer: str | None = None,
    types: Collection[str] | str | None = None,
) -> list[str]:
    """Return the handles of the entities in ``layout`` matching the filters."""
    wanted = _type_set(types)
    return [str(e.dxf.handle) for e in _layout(doc, layout) if _matches(e, layer, wanted)]


# -- add ---------------------------------------------------------------------------


def add_line(
    doc: Any,
    start: Point,
    end: Point,
    *,
    layout: str = "Model",
    layer: str | None = None,
    color: int | str | None = None,
    linetype: str | None = None,
) -> str:
    """Add a LINE from ``start`` to ``end`` and return its handle."""
    p1, p2 = _point(start, "Start point"), _point(end, "End point")
    attribs, rgb = _common_attribs(doc, layer, color, linetype)
    return _finish(_layout(doc, layout).add_line(p1, p2, dxfattribs=attribs), rgb)


def add_circle(
    doc: Any,
    center: Point,
    radius: float,
    *,
    layout: str = "Model",
    layer: str | None = None,
    color: int | str | None = None,
    linetype: str | None = None,
) -> str:
    """Add a CIRCLE and return its handle; ``radius`` must be positive."""
    c, r = _point(center, "Center"), _positive(radius, "Radius")
    attribs, rgb = _common_attribs(doc, layer, color, linetype)
    return _finish(_layout(doc, layout).add_circle(c, r, dxfattribs=attribs), rgb)


def add_arc(
    doc: Any,
    center: Point,
    radius: float,
    start_angle: float,
    end_angle: float,
    *,
    layout: str = "Model",
    layer: str | None = None,
    color: int | str | None = None,
    linetype: str | None = None,
) -> str:
    """Add a counter-clockwise ARC from ``start_angle`` to ``end_angle`` (degrees)."""
    c, r = _point(center, "Center"), _positive(radius, "Radius")
    a1, a2 = _number(start_angle, "Start angle"), _number(end_angle, "End angle")
    attribs, rgb = _common_attribs(doc, layer, color, linetype)
    return _finish(_layout(doc, layout).add_arc(c, r, a1, a2, dxfattribs=attribs), rgb)


def add_polyline(
    doc: Any,
    points: Iterable[Point],
    *,
    closed: bool = False,
    layout: str = "Model",
    layer: str | None = None,
    color: int | str | None = None,
    linetype: str | None = None,
) -> str:
    """Add an LWPOLYLINE through ``points`` (``(x, y)`` or ``(x, y, bulge)``).

    Requires DXF R2000 or later.
    """
    vertices = _vertices(points)
    _requires_r2000(doc, "LWPOLYLINE")
    attribs, rgb = _common_attribs(doc, layer, color, linetype)
    entity = _layout(doc, layout).add_lwpolyline(
        vertices, format="xyb", close=bool(closed), dxfattribs=attribs
    )
    return _finish(entity, rgb)


def add_text(
    doc: Any,
    text: str,
    insert: Point,
    *,
    height: float = 2.5,
    rotation: float = 0.0,
    layout: str = "Model",
    layer: str | None = None,
    color: int | str | None = None,
    linetype: str | None = None,
) -> str:
    """Add a single-line, left-aligned TEXT at ``insert`` and return its handle."""
    p, h = _point(insert, "Insert point"), _positive(height, "Text height")
    angle = _number(rotation, "Rotation")
    if "\n" in text:
        raise ValueError("TEXT is single-line; use add_mtext for multi-line text")
    attribs, rgb = _common_attribs(doc, layer, color, linetype)
    attribs["insert"] = p
    entity = _layout(doc, layout).add_text(text, height=h, rotation=angle, dxfattribs=attribs)
    return _finish(entity, rgb)


def add_mtext(
    doc: Any,
    text: str,
    insert: Point,
    *,
    height: float = 2.5,
    width: float | None = None,
    rotation: float = 0.0,
    layout: str = "Model",
    layer: str | None = None,
    color: int | str | None = None,
    linetype: str | None = None,
) -> str:
    """Add a multi-line MTEXT (top-left attached) at ``insert`` and return its handle.

    ``\\n`` in ``text`` starts a new paragraph; ``width`` is the column width for
    word wrapping (``None`` = no wrapping). Requires DXF R2000 or later.
    """
    p, h = _point(insert, "Insert point"), _positive(height, "Text height")
    angle = _number(rotation, "Rotation")
    w = None if width is None else _positive(width, "Text width")
    _requires_r2000(doc, "MTEXT")
    attribs, rgb = _common_attribs(doc, layer, color, linetype)
    attribs.update({"insert": p, "char_height": h, "attachment_point": 1})
    if w is not None:
        attribs["width"] = w
    entity = _layout(doc, layout).add_mtext(_mtext_content(text), dxfattribs=attribs)
    if angle:
        entity.set_rotation(angle)
    return _finish(entity, rgb)


def _mtext_content(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\n", "\\P")


# -- modify ------------------------------------------------------------------------


def _check_unlocked(doc: Any, entity: Any) -> None:
    name = str(entity.dxf.get("layer", "0"))
    if doc.layers.has_entry(name) and doc.layers.get(name).is_locked():
        raise ValueError(f"Entity {entity.dxf.handle} is on locked layer: {name}")


def modify_entity(
    doc: Any,
    handle: str,
    *,
    layer: str | None = None,
    color: int | str | None = None,
    linetype: str | None = None,
    start: Point | None = None,
    end: Point | None = None,
    center: Point | None = None,
    radius: float | None = None,
    start_angle: float | None = None,
    end_angle: float | None = None,
    points: Iterable[Point] | None = None,
    closed: bool | None = None,
    text: str | None = None,
    insert: Point | None = None,
    height: float | None = None,
    rotation: float | None = None,
    width: float | None = None,
) -> EntityDetail:
    """Change properties of one entity; ``None`` leaves a property untouched.

    ``layer``, ``color`` and ``linetype`` apply to every entity; the geometry
    properties must fit the entity type (e.g. ``radius`` for CIRCLE/ARC only),
    otherwise ``ValueError`` is raised. All values are validated before anything
    is changed.
    """
    entity = _get(doc, handle)
    _check_unlocked(doc, entity)
    kind = entity.dxftype()
    given: dict[str, Any] = {
        key: value
        for key, value in {
            "start": start,
            "end": end,
            "center": center,
            "radius": radius,
            "start_angle": start_angle,
            "end_angle": end_angle,
            "points": points,
            "closed": closed,
            "text": text,
            "insert": insert,
            "height": height,
            "rotation": rotation,
            "width": width,
        }.items()
        if value is not None
    }
    allowed = _PROPERTIES.get(kind, frozenset())
    for key in given:
        if key not in allowed:
            raise ValueError(f"Property '{key}' does not apply to {kind}")

    # Validate everything first so a bad argument leaves the entity unchanged.
    values: dict[str, Any] = {}
    for key, value in given.items():
        if key in ("start", "end", "center", "insert"):
            values[key] = _point(value, key.capitalize())
        elif key in ("radius", "height", "width"):
            values[key] = _positive(value, key.capitalize())
        elif key in ("start_angle", "end_angle", "rotation"):
            values[key] = _number(value, key.replace("_", " ").capitalize())
        elif key == "points":
            values[key] = _vertices(value)
        elif key == "closed":
            values[key] = bool(value)
        else:  # text
            if kind == "TEXT" and "\n" in value:
                raise ValueError("TEXT is single-line; newlines are not allowed")
            values[key] = str(value)
    new_layer = None if layer is None else _layer_name(doc, layer)
    parsed_color = None if color is None else _color(doc, color)
    new_linetype = None if linetype is None else _linetype_name(doc, linetype)

    dxf = entity.dxf
    if new_layer is not None:
        dxf.layer = new_layer
    if parsed_color is not None:
        aci, rgb = parsed_color
        if rgb is not None:
            entity.rgb = rgb
        else:
            dxf.color = aci
            dxf.discard("true_color")
    if new_linetype is not None:
        dxf.linetype = new_linetype
    _apply_geometry(entity, kind, values)
    return _detail(entity)


def _apply_geometry(entity: Any, kind: str, values: dict[str, Any]) -> None:
    dxf = entity.dxf
    for key in ("start", "end", "center", "radius", "start_angle", "end_angle"):
        if key in values:
            dxf.set(key, values[key])
    if kind == "LWPOLYLINE":
        if "points" in values:
            entity.set_points(values["points"], format="xyb")
        if "closed" in values:
            entity.closed = values["closed"]
    elif kind == "TEXT":
        if "text" in values:
            dxf.text = values["text"]
        if "height" in values:
            dxf.height = values["height"]
        if "rotation" in values:
            dxf.rotation = values["rotation"]
        if "insert" in values:
            # Translate so aligned/fitted text keeps its second alignment point.
            old = entity.get_placement()[1]
            new = values["insert"]
            entity.translate(new[0] - old.x, new[1] - old.y, new[2] - old.z)
    elif kind == "MTEXT":
        if "text" in values:
            entity.text = _mtext_content(values["text"])
        if "insert" in values:
            dxf.insert = values["insert"]
        if "height" in values:
            dxf.char_height = values["height"]
        if "width" in values:
            dxf.width = values["width"]
        if "rotation" in values:
            entity.set_rotation(values["rotation"])


# -- delete / transform / copy -------------------------------------------------------


def _resolve(doc: Any, handles: Iterable[str] | str, *, check_locked: bool = True) -> list[Any]:
    """Look up every handle (duplicates dropped) before anything is changed."""
    if isinstance(handles, str):
        handles = [handles]
    entities: list[Any] = []
    seen: set[str] = set()
    for handle in handles:
        entity = _get(doc, handle)
        key = str(entity.dxf.handle)
        if key in seen:
            continue
        seen.add(key)
        if entity.dxftype() in _SUB_ENTITIES or entity.get_layout() is None:
            raise ValueError(
                f"Entity {key} ({entity.dxftype()}) belongs to another entity; edit its parent"
            )
        if check_locked:
            _check_unlocked(doc, entity)
        entities.append(entity)
    return entities


def delete_entities(doc: Any, handles: Iterable[str] | str) -> int:
    """Delete entities by handle and return how many were removed.

    Unknown handles raise ``KeyError`` and entities on locked layers raise
    ``ValueError``; in both cases nothing is deleted.
    """
    entities = _resolve(doc, handles)
    for entity in entities:
        entity.get_layout().delete_entity(entity)
    return len(entities)


def _transform(doc: Any, handles: Iterable[str] | str, matrix: Matrix44) -> list[str]:
    entities = _resolve(doc, handles)
    for entity in entities:
        try:
            entity.transform(matrix)
        except NotImplementedError:
            raise ValueError(
                f"Entity {entity.dxf.handle} ({entity.dxftype()}) cannot be transformed"
            ) from None
    return [str(e.dxf.handle) for e in entities]


def move_entities(
    doc: Any, handles: Iterable[str] | str, dx: float, dy: float, dz: float = 0.0
) -> list[str]:
    """Translate entities by ``(dx, dy, dz)`` and return their handles."""
    offset = (_number(dx, "dx"), _number(dy, "dy"), _number(dz, "dz"))
    return _transform(doc, handles, Matrix44.translate(*offset))


def _about(base: Point, matrix: Matrix44) -> Matrix44:
    bx, by, bz = _point(base, "Base point")
    return Matrix44.translate(-bx, -by, -bz) @ matrix @ Matrix44.translate(bx, by, bz)


def rotate_entities(
    doc: Any, handles: Iterable[str] | str, angle: float, base: Point = (0.0, 0.0)
) -> list[str]:
    """Rotate entities counter-clockwise by ``angle`` degrees around ``base``."""
    radians = math.radians(_number(angle, "Angle"))
    return _transform(doc, handles, _about(base, Matrix44.z_rotate(radians)))


def scale_entities(
    doc: Any, handles: Iterable[str] | str, factor: float, base: Point = (0.0, 0.0)
) -> list[str]:
    """Scale entities uniformly by ``factor`` (> 0) relative to ``base``."""
    f = _positive(factor, "Scale factor")
    return _transform(doc, handles, _about(base, Matrix44.scale(f, f, f)))


def copy_entities(
    doc: Any,
    handles: Iterable[str] | str,
    dx: float = 0.0,
    dy: float = 0.0,
    dz: float = 0.0,
    *,
    layer: str | None = None,
) -> list[str]:
    """Copy entities, offset the copies by ``(dx, dy, dz)`` and return the new handles.

    Copies stay in the layout (or block) of their source and keep its properties;
    ``layer`` moves the copies to another layer. Sources on locked layers may be copied.
    """
    offset = (_number(dx, "dx"), _number(dy, "dy"), _number(dz, "dz"))
    target_layer = None if layer is None else _layer_name(doc, layer)
    sources = _resolve(doc, handles, check_locked=False)
    new_handles: list[str] = []
    for source in sources:
        try:
            clone = doc.entitydb.duplicate_entity(source)
        except Exception as exc:  # ezdxf raises DXFTypeError for non-copyable entities
            raise ValueError(
                f"Entity {source.dxf.handle} ({source.dxftype()}) cannot be copied"
            ) from exc
        source.get_layout().add_entity(clone)
        if target_layer is not None:
            clone.dxf.layer = target_layer
        if any(offset):
            clone.translate(*offset)
        new_handles.append(str(clone.dxf.handle))
    return new_handles
