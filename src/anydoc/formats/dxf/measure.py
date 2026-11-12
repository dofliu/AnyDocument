"""DXF measurement and spatial queries: distance, length, area, extents, picking.

All values are in drawing units (see ``DxfDocument.units``); angles are in degrees,
counter-clockwise from the +X axis. Lengths and areas of LINE, CIRCLE, ARC, ELLIPSE,
LWPOLYLINE and POLYLINE (including bulge arcs) are exact; SPLINE values are computed
from a fine flattening of the curve. Spatial queries (``entities_at`` and
``entities_in_window``) work in the XY plane of the world coordinate system.
"""

from __future__ import annotations

import math
from collections.abc import Collection, Iterable, Sequence
from dataclasses import dataclass
from typing import Any

from ezdxf import bbox as _bbox
from ezdxf import path as _path

from anydoc.formats.dxf.entities import (
    _get,
    _layout,
    _matches,
    _number,
    _point,
    _type_set,
)

__all__ = [
    "AREA_TYPES",
    "LENGTH_TYPES",
    "BoundingBox",
    "Distance",
    "Measurement",
    "PickHit",
    "bounding_box",
    "distance",
    "entities_at",
    "entities_in_window",
    "entity_area",
    "entity_length",
    "measure_entities",
    "measure_entity",
    "path_length",
    "polygon_area",
]

Point = Sequence[float]
_P3 = tuple[float, float, float]
_P2 = tuple[float, float]

LENGTH_TYPES: tuple[str, ...] = (
    "LINE",
    "CIRCLE",
    "ARC",
    "ELLIPSE",
    "LWPOLYLINE",
    "POLYLINE",
    "SPLINE",
)
AREA_TYPES: tuple[str, ...] = ("CIRCLE", "ARC", "ELLIPSE", "LWPOLYLINE", "POLYLINE", "SPLINE")

_TAU = 2.0 * math.pi
_FULL_TURN_TOL = 1e-9
# Relative flattening tolerance (fraction of the entity size) for curves.
_CURVE_TOL = 1e-6
_QUERY_TOL = 1e-4
_SPLINE_SEGMENTS = 4
_SPLINE_MAX_DEPTH = 12


@dataclass(frozen=True)
class Distance:
    """The distance between two points with its components.

    ``angle`` is the direction of the XY projection in degrees (0-360, ``0.0`` when
    the points coincide in XY).
    """

    distance: float
    dx: float
    dy: float
    dz: float
    angle: float

    def to_dict(self) -> dict[str, float]:
        """Return a JSON-serialisable dict."""
        return {
            "distance": self.distance,
            "dx": self.dx,
            "dy": self.dy,
            "dz": self.dz,
            "angle": self.angle,
        }


@dataclass(frozen=True)
class BoundingBox:
    """An axis-aligned bounding box in world coordinates."""

    min: _P3
    max: _P3

    @property
    def size(self) -> _P3:
        """Return the extent along X, Y and Z."""
        return (
            self.max[0] - self.min[0],
            self.max[1] - self.min[1],
            self.max[2] - self.min[2],
        )

    @property
    def width(self) -> float:
        """Return the extent along X."""
        return self.max[0] - self.min[0]

    @property
    def height(self) -> float:
        """Return the extent along Y."""
        return self.max[1] - self.min[1]

    @property
    def center(self) -> _P3:
        """Return the centre point."""
        return (
            (self.min[0] + self.max[0]) / 2.0,
            (self.min[1] + self.max[1]) / 2.0,
            (self.min[2] + self.max[2]) / 2.0,
        )

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serialisable dict (points become lists)."""
        return {
            "min": list(self.min),
            "max": list(self.max),
            "size": list(self.size),
            "center": list(self.center),
        }


@dataclass(frozen=True)
class Measurement:
    """Length, area and extents of one entity.

    ``length`` is the perimeter for closed shapes; it is ``None`` for entity types
    without a length (e.g. TEXT). ``area`` is ``None`` unless the entity is closed.
    ``bbox`` is ``None`` for entities without extents.
    """

    handle: str
    type: str
    layer: str
    closed: bool
    length: float | None
    area: float | None
    bbox: BoundingBox | None

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serialisable dict."""
        return {
            "handle": self.handle,
            "type": self.type,
            "layer": self.layer,
            "closed": self.closed,
            "length": self.length,
            "area": self.area,
            "bbox": None if self.bbox is None else self.bbox.to_dict(),
        }


@dataclass(frozen=True)
class PickHit:
    """An entity found near a picked point, with its XY ``distance`` to the point."""

    handle: str
    type: str
    layer: str
    distance: float

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serialisable dict."""
        return {
            "handle": self.handle,
            "type": self.type,
            "layer": self.layer,
            "distance": self.distance,
        }


# -- pure geometry -------------------------------------------------------------------


def distance(p1: Point, p2: Point) -> Distance:
    """Return the distance from ``p1`` to ``p2`` (``(x, y)`` or ``(x, y, z)`` points)."""
    a = _point(p1, "First point")
    b = _point(p2, "Second point")
    dx, dy, dz = b[0] - a[0], b[1] - a[1], b[2] - a[2]
    angle = math.degrees(math.atan2(dy, dx)) % 360.0 if (dx or dy) else 0.0
    return Distance(math.sqrt(dx * dx + dy * dy + dz * dz), dx, dy, dz, angle)


def _points(points: Any, minimum: int) -> list[_P3]:
    if isinstance(points, str) or not isinstance(points, Iterable):
        raise ValueError(f"Points must be a sequence of points, got {points!r}")
    out = [_point(p) for p in points]
    if len(out) < minimum:
        raise ValueError(f"At least {minimum} points are required, got {len(out)}")
    return out


def path_length(points: Iterable[Point], *, closed: bool = False) -> float:
    """Return the length of the straight path through ``points`` (at least 2).

    With ``closed`` the segment from the last point back to the first is included.
    """
    pts = _points(points, 2)
    if closed:
        pts.append(pts[0])
    return sum(math.dist(a, b) for a, b in zip(pts, pts[1:], strict=False))


def polygon_area(points: Iterable[Point]) -> float:
    """Return the area of the polygon through ``points`` (at least 3, implicitly closed).

    Points may be 3D; the polygon is assumed to be planar. The result is never
    negative, whatever the winding direction.
    """
    return _newell(_points(points, 3))


def _newell(pts: Sequence[_P3]) -> float:
    nx = ny = nz = 0.0
    for (x1, y1, z1), (x2, y2, z2) in zip(pts, [*pts[1:], pts[0]], strict=True):
        nx += (y1 - y2) * (z1 + z2)
        ny += (z1 - z2) * (x1 + x2)
        nz += (x1 - x2) * (y1 + y2)
    return math.sqrt(nx * nx + ny * ny + nz * nz) / 2.0


def _bulge_arc(chord: float, bulge: float) -> tuple[float, float]:
    """Return ``(arc length, signed segment area)`` of a bulge arc over ``chord``."""
    theta = 4.0 * math.atan(abs(bulge))
    radius = chord * (1.0 + bulge * bulge) / (4.0 * abs(bulge))
    area = radius * radius * (theta - math.sin(theta)) / 2.0
    return radius * theta, math.copysign(area, bulge)


def _bulge_segments(vertices: Sequence[_P3], closed: bool) -> Iterable[tuple[_P3, _P3, float]]:
    """Yield ``(start, end, bulge)`` for each segment of ``(x, y, bulge)`` vertices."""
    count = len(vertices)
    for i in range(count if closed else count - 1):
        yield vertices[i], vertices[(i + 1) % count], vertices[i][2]


def _bulge_length(vertices: Sequence[_P3], closed: bool) -> float:
    total = 0.0
    for a, b, bulge in _bulge_segments(vertices, closed):
        chord = math.hypot(b[0] - a[0], b[1] - a[1])
        total += _bulge_arc(chord, bulge)[0] if bulge and chord else chord
    return total


def _bulge_area(vertices: Sequence[_P3]) -> float:
    """Return the area enclosed by ``(x, y, bulge)`` vertices, closed implicitly."""
    total = 0.0
    for a, b, bulge in _bulge_segments(vertices, True):
        total += (a[0] * b[1] - b[0] * a[1]) / 2.0
        chord = math.hypot(b[0] - a[0], b[1] - a[1])
        if bulge and chord:
            total += _bulge_arc(chord, bulge)[1]
    return abs(total)


# -- entity geometry -----------------------------------------------------------------


def _arc_sweep(entity: Any) -> float:
    """Return the swept angle of an ARC in radians (0 < sweep <= 2π)."""
    sweep = (float(entity.dxf.end_angle) - float(entity.dxf.start_angle)) % 360.0
    return math.radians(sweep if sweep > 0 else 360.0)


def _ellipse_params(entity: Any) -> tuple[float, float, float, bool]:
    """Return ``(a, b, swept parameter range, is full ellipse)``."""
    a = float(entity.dxf.major_axis.magnitude)
    b = a * float(entity.dxf.ratio)
    sweep = (float(entity.dxf.end_param) - float(entity.dxf.start_param)) % _TAU
    full = sweep < _FULL_TURN_TOL or _TAU - sweep < _FULL_TURN_TOL
    return a, b, _TAU if full else sweep, full


def _ellipse_length(entity: Any) -> float:
    a, b, sweep, _ = _ellipse_params(entity)
    start = float(entity.dxf.start_param)
    steps = 2048  # Simpson's rule; the integrand is smooth, so this is ample.
    h = sweep / steps

    def speed(t: float) -> float:
        return math.hypot(a * math.sin(t), b * math.cos(t))

    total = speed(start) + speed(start + sweep)
    for i in range(1, steps):
        total += (4.0 if i % 2 else 2.0) * speed(start + i * h)
    return total * h / 3.0


def _polyline_vertices(entity: Any) -> tuple[list[_P3], bool, bool]:
    """Return ``(vertices, closed, has_bulge)`` of an LWPOLYLINE or POLYLINE.

    2D vertices are ``(x, y, bulge)``; 3D polyline vertices are ``(x, y, z)``.
    """
    kind = entity.dxftype()
    if kind == "LWPOLYLINE":
        pts = [(float(x), float(y), float(b)) for x, y, b in entity.get_points("xyb")]
        return pts, bool(entity.closed), True
    if entity.is_2d_polyline:
        pts = [
            (
                float(v.dxf.location.x),
                float(v.dxf.location.y),
                float(v.dxf.get("bulge", 0.0)),
            )
            for v in entity.vertices
        ]
        return pts, bool(entity.is_closed), True
    if entity.is_3d_polyline:
        pts = [
            (float(v.dxf.location.x), float(v.dxf.location.y), float(v.dxf.location.z))
            for v in entity.vertices
        ]
        return pts, bool(entity.is_closed), False
    raise ValueError("Polygon meshes and polyface meshes cannot be measured")


def _spline_points(entity: Any, tolerance: float = _CURVE_TOL) -> list[_P3]:
    """Flatten a SPLINE over its valid parameter range.

    ``tolerance`` is relative to the size of the control polygon. Unlike ezdxf's own
    flattening this evaluates unclamped (periodic) splines only between the knots
    ``u[degree]`` and ``u[count]``, where the curve is defined.
    """
    tool = entity.construction_tool()
    control = [(float(p.x), float(p.y), float(p.z)) for p in tool.control_points]
    size = max(
        (max(p[i] for p in control) - min(p[i] for p in control) for i in range(3)),
        default=0.0,
    )
    if size <= 0.0:
        return control[:1]
    limit = size * tolerance
    knots = [float(k) for k in tool.knots()]
    spans = sorted(set(knots[tool.degree : tool.count + 1]))

    def at(t: float) -> _P3:
        point = tool.point(t)
        return float(point.x), float(point.y), float(point.z)

    def refine(t0: float, p0: _P3, t1: float, p1: _P3, depth: int) -> None:
        tm = (t0 + t1) / 2.0
        pm = at(tm)
        chord_mid = ((p0[0] + p1[0]) / 2.0, (p0[1] + p1[1]) / 2.0, (p0[2] + p1[2]) / 2.0)
        if depth < _SPLINE_MAX_DEPTH and math.dist(pm, chord_mid) > limit:
            refine(t0, p0, tm, pm, depth + 1)
            refine(tm, pm, t1, p1, depth + 1)
        else:
            out.append(pm)
            out.append(p1)

    out: list[_P3] = [at(spans[0])]
    for lo, hi in zip(spans, spans[1:], strict=False):
        step = (hi - lo) / _SPLINE_SEGMENTS
        for i in range(_SPLINE_SEGMENTS):
            t0 = lo + i * step
            t1 = hi if i == _SPLINE_SEGMENTS - 1 else t0 + step
            refine(t0, out[-1], t1, at(t1), 0)
    return out


def _is_closed(entity: Any) -> bool:
    kind = entity.dxftype()
    if kind == "CIRCLE":
        return True
    if kind == "ELLIPSE":
        return _ellipse_params(entity)[3]
    if kind == "LWPOLYLINE":
        return bool(entity.closed)
    if kind == "POLYLINE":
        return bool(entity.is_closed) and not (entity.is_poly_face_mesh or entity.is_polygon_mesh)
    if kind == "SPLINE":
        return bool(entity.closed)
    return False


def _length(entity: Any) -> float:
    kind = entity.dxftype()
    dxf = entity.dxf
    if kind == "LINE":
        return float(dxf.start.distance(dxf.end))
    if kind == "CIRCLE":
        return _TAU * float(dxf.radius)
    if kind == "ARC":
        return float(dxf.radius) * _arc_sweep(entity)
    if kind == "ELLIPSE":
        return _ellipse_length(entity)
    if kind in ("LWPOLYLINE", "POLYLINE"):
        pts, closed, has_bulge = _polyline_vertices(entity)
        if len(pts) < 2:
            return 0.0
        if has_bulge:
            return _bulge_length(pts, closed)
        return path_length(pts, closed=closed)
    if kind == "SPLINE":
        pts = _spline_points(entity)
        return path_length(pts) if len(pts) > 1 else 0.0
    raise ValueError(f"{kind} has no length (supported: {', '.join(LENGTH_TYPES)})")


def _area(entity: Any, assume_closed: bool) -> float:
    kind = entity.dxftype()
    dxf = entity.dxf
    if kind not in AREA_TYPES:
        raise ValueError(f"{kind} has no area (supported: {', '.join(AREA_TYPES)})")
    if not assume_closed and not _is_closed(entity):
        raise ValueError(
            f"{kind} {dxf.handle} is not closed; pass assume_closed to close it "
            "with a straight segment"
        )
    if kind == "CIRCLE":
        return math.pi * float(dxf.radius) ** 2
    if kind == "ARC":
        sweep = _arc_sweep(entity)
        return float(dxf.radius) ** 2 * (sweep - math.sin(sweep)) / 2.0
    if kind == "ELLIPSE":
        a, b, sweep, full = _ellipse_params(entity)
        return math.pi * a * b if full else a * b * (sweep - math.sin(sweep)) / 2.0
    if kind == "SPLINE":
        pts = _spline_points(entity)
        return _newell(pts) if len(pts) > 2 else 0.0
    pts, _, has_bulge = _polyline_vertices(entity)
    if len(pts) < 2:
        return 0.0
    return _bulge_area(pts) if has_bulge else _newell(pts)


def entity_length(doc: Any, handle: str) -> float:
    """Return the length (perimeter for closed shapes) of an entity.

    Supports the types in ``LENGTH_TYPES``; raises ``ValueError`` for other types and
    ``KeyError`` when the entity does not exist.
    """
    return _length(_get(doc, handle))


def entity_area(doc: Any, handle: str, *, assume_closed: bool = False) -> float:
    """Return the area enclosed by a closed entity.

    Supports the types in ``AREA_TYPES``. Open entities (arcs, open polylines and
    splines) raise ``ValueError`` unless ``assume_closed`` is true, in which case
    the shape is closed with a straight segment from its end to its start.
    """
    return _area(_get(doc, handle), assume_closed)


def _extents(entities: Iterable[Any]) -> BoundingBox | None:
    """Return the bounding box of ``entities`` (splines use their own flattening)."""
    others: list[Any] = []
    points: list[_P3] = []
    for entity in entities:
        if entity.dxftype() == "SPLINE":
            points.extend(_spline_points(entity, _QUERY_TOL))
        else:
            others.append(entity)
    box = _bbox.extents(others, fast=False)
    if box.has_data:
        points.append((float(box.extmin.x), float(box.extmin.y), float(box.extmin.z)))
        points.append((float(box.extmax.x), float(box.extmax.y), float(box.extmax.z)))
    if not points:
        return None
    lo = (min(p[0] for p in points), min(p[1] for p in points), min(p[2] for p in points))
    hi = (max(p[0] for p in points), max(p[1] for p in points), max(p[2] for p in points))
    return BoundingBox(lo, hi)


def _entity_bbox(entity: Any) -> BoundingBox | None:
    return _extents([entity])


def _measure(entity: Any) -> Measurement:
    kind = str(entity.dxftype())
    length: float | None = None
    area: float | None = None
    closed = False
    try:
        closed = _is_closed(entity)
        if kind in LENGTH_TYPES:
            length = _length(entity)
        if closed:
            area = _area(entity, False)
    except ValueError:  # polygon / polyface meshes
        length = area = None
    return Measurement(
        handle=str(entity.dxf.handle),
        type=kind,
        layer=str(entity.dxf.get("layer", "0")),
        closed=closed,
        length=length,
        area=area,
        bbox=_entity_bbox(entity),
    )


def measure_entity(doc: Any, handle: str) -> Measurement:
    """Return length, area and extents of one entity (``KeyError`` when missing).

    Never fails for an unsupported entity type: the values that do not apply are
    ``None``.
    """
    return _measure(_get(doc, handle))


def _handle_list(handles: Iterable[str] | str) -> list[str]:
    return [handles] if isinstance(handles, str) else list(handles)


def measure_entities(doc: Any, handles: Iterable[str] | str) -> list[Measurement]:
    """Return the measurements of several entities, in the given order."""
    return [_measure(entity) for entity in [_get(doc, h) for h in _handle_list(handles)]]


# -- extents and spatial queries ---------------------------------------------------------


def _visible(doc: Any, entity: Any) -> bool:
    name = str(entity.dxf.get("layer", "0"))
    if not doc.layers.has_entry(name):
        return True
    layer = doc.layers.get(name)
    return not (layer.is_off() or layer.is_frozen())


def _candidates(
    doc: Any,
    layout: str,
    layer: str | None,
    types: Collection[str] | str | None,
    visible_only: bool,
) -> list[Any]:
    wanted = _type_set(types)
    return [
        e
        for e in _layout(doc, layout)
        if _matches(e, layer, wanted) and (not visible_only or _visible(doc, e))
    ]


def bounding_box(
    doc: Any,
    handles: Iterable[str] | str | None = None,
    *,
    layout: str = "Model",
    layer: str | None = None,
    types: Collection[str] | str | None = None,
    visible_only: bool = False,
) -> BoundingBox | None:
    """Return the bounding box of the given entities, or of a whole layout.

    With ``handles`` the box covers exactly those entities; otherwise it covers the
    entities of ``layout`` matching the ``layer``/``types`` filters (``visible_only``
    skips layers that are off or frozen). Returns ``None`` when nothing has extents.
    """
    if handles is not None:
        entities = [_get(doc, h) for h in _handle_list(handles)]
    else:
        entities = _candidates(doc, layout, layer, types, visible_only)
    return _extents(entities)


def _outline(entity: Any, box: BoundingBox) -> list[_P2] | None:
    """Return the entity as an XY polyline, or ``None`` when it has no curve geometry."""
    kind = entity.dxftype()
    if kind == "POINT":
        loc = entity.dxf.location
        return [(float(loc.x), float(loc.y))]
    if kind == "SPLINE":
        return [(x, y) for x, y, _ in _spline_points(entity, _QUERY_TOL)]
    if kind not in ("LINE", "CIRCLE", "ARC", "ELLIPSE", "LWPOLYLINE", "POLYLINE"):
        return None
    try:
        path = _path.make_path(entity)
    except (TypeError, ValueError):
        return None
    size = max(box.width, box.height)
    if size <= 0.0:
        return [(box.min[0], box.min[1])]
    points = [(float(p.x), float(p.y)) for p in path.flattening(size * _QUERY_TOL, segments=8)]
    return points or None


def _point_segment(p: _P2, a: _P2, b: _P2) -> float:
    vx, vy = b[0] - a[0], b[1] - a[1]
    wx, wy = p[0] - a[0], p[1] - a[1]
    norm = vx * vx + vy * vy
    t = 0.0 if norm == 0.0 else max(0.0, min(1.0, (wx * vx + wy * vy) / norm))
    return math.hypot(wx - t * vx, wy - t * vy)


def _point_rect(p: _P2, lo: _P2, hi: _P2) -> float:
    dx = max(lo[0] - p[0], 0.0, p[0] - hi[0])
    dy = max(lo[1] - p[1], 0.0, p[1] - hi[1])
    return math.hypot(dx, dy)


def _segment_hits_rect(a: _P2, b: _P2, lo: _P2, hi: _P2) -> bool:
    """Liang-Barsky test: does segment ``a``-``b`` touch the rectangle ``lo``-``hi``?"""
    t0, t1 = 0.0, 1.0
    dx, dy = b[0] - a[0], b[1] - a[1]
    for p, q in (
        (-dx, a[0] - lo[0]),
        (dx, hi[0] - a[0]),
        (-dy, a[1] - lo[1]),
        (dy, hi[1] - a[1]),
    ):
        if p == 0.0:
            if q < 0.0:
                return False
            continue
        t = q / p
        if p < 0.0:
            t0 = max(t0, t)
        else:
            t1 = min(t1, t)
        if t0 > t1:
            return False
    return True


def entities_at(
    doc: Any,
    point: Point,
    *,
    tolerance: float | None = None,
    layout: str = "Model",
    layer: str | None = None,
    types: Collection[str] | str | None = None,
    visible_only: bool = False,
) -> list[PickHit]:
    """Return the entities within ``tolerance`` of ``point`` (XY), nearest first.

    Curves (lines, arcs, circles, ellipses, polylines, splines) are hit on their
    outline, not their interior; other entities (text, block references, hatches...)
    are hit anywhere inside their bounding box. ``tolerance`` defaults to 1 % of the
    diagonal of the layout extents.
    """
    px, py, _ = _point(point, "Pick point")
    entities = _candidates(doc, layout, layer, types, visible_only)
    if tolerance is None:
        extents = _extents(entities)
        tolerance = 0.0 if extents is None else math.hypot(extents.width, extents.height) * 0.01
    else:
        tolerance = _number(tolerance, "Tolerance")
        if tolerance < 0:
            raise ValueError(f"Tolerance must not be negative, got {tolerance!r}")
    hits: list[PickHit] = []
    for entity in entities:
        box = _entity_bbox(entity)
        if box is None:
            continue
        lo, hi = (box.min[0], box.min[1]), (box.max[0], box.max[1])
        if _point_rect((px, py), lo, hi) > tolerance:
            continue
        outline = _outline(entity, box)
        if outline is None:
            dist = _point_rect((px, py), lo, hi)
        elif len(outline) == 1:
            dist = math.dist((px, py), outline[0])
        else:
            dist = min(
                _point_segment((px, py), a, b) for a, b in zip(outline, outline[1:], strict=False)
            )
        if dist <= tolerance:
            hits.append(
                PickHit(
                    handle=str(entity.dxf.handle),
                    type=str(entity.dxftype()),
                    layer=str(entity.dxf.get("layer", "0")),
                    distance=dist,
                )
            )
    hits.sort(key=lambda hit: hit.distance)
    return hits


def entities_in_window(
    doc: Any,
    corner1: Point,
    corner2: Point,
    *,
    crossing: bool = False,
    layout: str = "Model",
    layer: str | None = None,
    types: Collection[str] | str | None = None,
    visible_only: bool = False,
) -> list[str]:
    """Return the handles of the entities selected by a rectangular window (XY).

    By default only entities lying completely inside the window are returned
    (window selection). With ``crossing`` entities that merely touch or cross the
    window are returned as well (crossing selection); entities without curve
    geometry are tested by their bounding box.
    """
    x1, y1, _ = _point(corner1, "First corner")
    x2, y2, _ = _point(corner2, "Second corner")
    lo, hi = (min(x1, x2), min(y1, y2)), (max(x1, x2), max(y1, y2))
    selected: list[str] = []
    for entity in _candidates(doc, layout, layer, types, visible_only):
        box = _entity_bbox(entity)
        if box is None:
            continue
        inside = (
            box.min[0] >= lo[0]
            and box.min[1] >= lo[1]
            and box.max[0] <= hi[0]
            and box.max[1] <= hi[1]
        )
        if not inside:
            if not crossing:
                continue
            if box.min[0] > hi[0] or box.max[0] < lo[0] or box.min[1] > hi[1] or box.max[1] < lo[1]:
                continue
            outline = _outline(entity, box)
            if (
                outline is not None
                and len(outline) > 1
                and not any(
                    _segment_hits_rect(a, b, lo, hi)
                    for a, b in zip(outline, outline[1:], strict=False)
                )
            ):
                continue
        selected.append(str(entity.dxf.handle))
    return selected
