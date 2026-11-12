"""Tests for DXF measurement and spatial queries (T083)."""

from __future__ import annotations

import json
import math
from typing import Any

import ezdxf
import pytest

from anydoc.formats.dxf import BoundingBox, DxfDocument, Measurement
from anydoc.formats.dxf import measure as M


def _approx(value: Any, tol: float = 1e-9) -> Any:
    return pytest.approx(value, abs=tol)


@pytest.fixture
def doc() -> Any:
    d = ezdxf.new("R2018")
    d.layers.add("牆", color=1)
    d.layers.add("隱藏").off()
    d.layers.add("凍結").freeze()
    return d


def _h(entity: Any) -> str:
    return str(entity.dxf.handle)


# -- distance / path / polygon -----------------------------------------------------


def test_distance_2d() -> None:
    d = M.distance((0, 0), (3, 4))
    assert d.distance == _approx(5.0)
    assert (d.dx, d.dy, d.dz) == (3.0, 4.0, 0.0)
    assert d.angle == _approx(math.degrees(math.atan2(4, 3)))
    assert d.to_dict() == {
        "distance": d.distance,
        "dx": 3.0,
        "dy": 4.0,
        "dz": 0.0,
        "angle": d.angle,
    }


def test_distance_3d_and_angle_range() -> None:
    d = M.distance((1, 1, 1), (1, -1, 3))
    assert d.distance == _approx(math.sqrt(8))
    assert d.angle == _approx(270.0)
    assert M.distance((0, 0), (-1, 0)).angle == _approx(180.0)


def test_distance_coincident_points() -> None:
    d = M.distance((2, 2), (2, 2, 5))
    assert d.distance == _approx(5.0) and d.angle == 0.0


@pytest.mark.parametrize(
    "args",
    [((0,), (1, 1)), ((0, 0), "1,1"), ((0, 0), (1, float("nan"))), ((0, 0), (1, "a"))],
)
def test_distance_invalid(args: tuple[Any, Any]) -> None:
    with pytest.raises(ValueError):
        M.distance(*args)


def test_path_length() -> None:
    pts = [(0, 0), (3, 0), (3, 4)]
    assert M.path_length(pts) == _approx(7.0)
    assert M.path_length(pts, closed=True) == _approx(12.0)
    assert M.path_length([(0, 0, 0), (0, 0, 2)]) == _approx(2.0)


def test_path_length_invalid() -> None:
    with pytest.raises(ValueError, match="At least 2"):
        M.path_length([(0, 0)])
    with pytest.raises(ValueError, match="sequence"):
        M.path_length("0,0")


def test_polygon_area_either_winding() -> None:
    square = [(0, 0), (4, 0), (4, 3), (0, 3)]
    assert M.polygon_area(square) == _approx(12.0)
    assert M.polygon_area(square[::-1]) == _approx(12.0)
    # an L-shape (concave)
    assert M.polygon_area([(0, 0), (4, 0), (4, 1), (1, 1), (1, 3), (0, 3)]) == _approx(6.0)


def test_polygon_area_3d_planar() -> None:
    # a 2 x 3 rectangle standing in the XZ plane
    assert M.polygon_area([(0, 0, 0), (2, 0, 0), (2, 0, 3), (0, 0, 3)]) == _approx(6.0)


def test_polygon_area_invalid() -> None:
    with pytest.raises(ValueError, match="At least 3"):
        M.polygon_area([(0, 0), (1, 1)])


# -- entity length -------------------------------------------------------------------


def test_length_line_circle_arc(doc: Any) -> None:
    msp = doc.modelspace()
    assert M.entity_length(doc, _h(msp.add_line((0, 0, 0), (1, 2, 2)))) == _approx(3.0)
    assert M.entity_length(doc, _h(msp.add_circle((5, 5), 2))) == _approx(4 * math.pi)
    assert M.entity_length(doc, _h(msp.add_arc((0, 0), 2, 0, 90))) == _approx(math.pi)
    # an arc crossing 0 degrees sweeps 270 -> 90 = 180 degrees
    assert M.entity_length(doc, _h(msp.add_arc((0, 0), 1, 270, 90))) == _approx(math.pi)
    # equal start and end angle is a full turn
    assert M.entity_length(doc, _h(msp.add_arc((0, 0), 1, 30, 30))) == _approx(2 * math.pi)


def test_length_lwpolyline_open_and_closed(doc: Any) -> None:
    msp = doc.modelspace()
    pts = [(0, 0), (4, 0), (4, 3)]
    assert M.entity_length(doc, _h(msp.add_lwpolyline(pts))) == _approx(7.0)
    assert M.entity_length(doc, _h(msp.add_lwpolyline(pts, close=True))) == _approx(12.0)


def test_length_and_area_with_bulge(doc: Any) -> None:
    msp = doc.modelspace()
    # two semicircular bulges make a full circle of diameter 2
    circle = msp.add_lwpolyline([(0, 0, 1), (2, 0, 1)], format="xyb", close=True)
    assert M.entity_length(doc, _h(circle)) == _approx(2 * math.pi)
    assert M.entity_area(doc, _h(circle)) == _approx(math.pi)
    # a "D": straight edge plus a semicircle; clockwise bulge gives the same result
    for bulge in (1, -1):
        d_shape = msp.add_lwpolyline([(0, 0, bulge), (2, 0, 0)], format="xyb", close=True)
        assert M.entity_length(doc, _h(d_shape)) == _approx(math.pi + 2)
        assert M.entity_area(doc, _h(d_shape)) == _approx(math.pi / 2)
    # a square with one edge bulging inwards (clockwise arc on a CCW polygon)
    notch = msp.add_lwpolyline(
        [(0, 0, -1), (2, 0, 0), (2, 2, 0), (0, 2, 0)], format="xyb", close=True
    )
    assert M.entity_area(doc, _h(notch)) == _approx(4 - math.pi / 2)
    # rounded slot: 4 x 2 rectangle plus two semicircles of radius 1
    slot = msp.add_lwpolyline(
        [(0, 0, 0), (4, 0, 1), (4, 2, 0), (0, 2, 1)], format="xyb", close=True
    )
    assert M.entity_length(doc, _h(slot)) == _approx(8 + 2 * math.pi)
    assert M.entity_area(doc, _h(slot)) == _approx(8 + math.pi)


def test_length_old_style_polylines(doc: Any) -> None:
    msp = doc.modelspace()
    p2d = msp.add_polyline2d([(0, 0), (3, 0), (3, 4)], close=True)
    assert M.entity_length(doc, _h(p2d)) == _approx(12.0)
    assert M.entity_area(doc, _h(p2d)) == _approx(6.0)
    bulged = msp.add_polyline2d([(0, 0, 1), (2, 0, 0)], format="xyb", close=True)
    assert M.entity_area(doc, _h(bulged)) == _approx(math.pi / 2)
    p3d = msp.add_polyline3d([(0, 0, 0), (0, 0, 2), (0, 3, 2)])
    assert M.entity_length(doc, _h(p3d)) == _approx(5.0)
    closed3d = msp.add_polyline3d([(0, 0, 0), (2, 0, 0), (2, 0, 3), (0, 0, 3)], close=True)
    assert M.entity_length(doc, _h(closed3d)) == _approx(10.0)
    assert M.entity_area(doc, _h(closed3d)) == _approx(6.0)


def test_polymesh_is_not_measurable(doc: Any) -> None:
    mesh = doc.modelspace().add_polymesh((3, 3))
    with pytest.raises(ValueError, match="meshes"):
        M.entity_length(doc, _h(mesh))
    m = M.measure_entity(doc, _h(mesh))
    assert m.length is None and m.area is None


def test_length_and_area_ellipse(doc: Any) -> None:
    msp = doc.modelspace()
    # ratio 1 is a circle of radius 3
    round_ = msp.add_ellipse((0, 0), major_axis=(3, 0), ratio=1.0)
    assert M.entity_length(doc, _h(round_)) == _approx(6 * math.pi, 1e-9)
    assert M.entity_area(doc, _h(round_)) == _approx(9 * math.pi)
    ell = msp.add_ellipse((1, 1), major_axis=(0, 4), ratio=0.5)
    assert M.entity_area(doc, _h(ell)) == _approx(math.pi * 4 * 2)
    # perimeter of a 4 x 2 ellipse (reference value from the elliptic integral)
    assert M.entity_length(doc, _h(ell)) == _approx(19.376896441095, 1e-8)
    half = msp.add_ellipse((0, 0), major_axis=(4, 0), ratio=0.5, start_param=0, end_param=math.pi)
    assert M.entity_length(doc, _h(half)) == _approx(19.376896441095 / 2, 1e-8)
    with pytest.raises(ValueError, match="not closed"):
        M.entity_area(doc, _h(half))
    assert M.entity_area(doc, _h(half), assume_closed=True) == _approx(math.pi * 4)


def test_length_and_area_spline(doc: Any) -> None:
    msp = doc.modelspace()
    straight = msp.add_open_spline([(0, 0), (1, 0), (2, 0), (3, 0)])
    assert M.entity_length(doc, _h(straight)) == _approx(3.0, 1e-6)
    with pytest.raises(ValueError, match="not closed"):
        M.entity_area(doc, _h(straight))
    bump = msp.add_open_spline([(0, 0), (1, 2), (2, 2), (3, 0)])
    # cubic Bezier x = 3t, y = 6t(1 - t): the area between curve and chord is exactly 3
    assert M.entity_area(doc, _h(bump), assume_closed=True) == _approx(3.0, 1e-4)
    assert 3.0 < M.entity_length(doc, _h(bump)) < 7.0


def test_closed_periodic_spline_uses_valid_parameter_range(doc: Any) -> None:
    # A periodic (unclamped) spline is only defined between knots u[degree] and u[count];
    # the curve stays inside the convex hull of its control points.
    closed = doc.modelspace().add_spline()
    closed.set_closed([(0, 0, 0), (4, 0, 0), (4, 4, 0), (0, 4, 0)])
    m = M.measure_entity(doc, _h(closed))
    assert m.closed and m.bbox is not None
    assert m.bbox.min == _approx((1 / 6, 1 / 6, 0), 1e-3)
    assert m.bbox.max == _approx((23 / 6, 23 / 6, 0), 1e-3)
    # a rounded square: smaller than the control polygon, larger than its inscribed diamond
    assert m.area is not None and 8.0 < m.area < 4 * math.pi
    assert m.length is not None and math.sqrt(4 * math.pi * m.area) <= m.length < 16
    # the outline can be picked and starts where it ends
    pts = M._spline_points(closed)
    assert pts[0] == _approx(pts[-1], 1e-9)
    assert [h.handle for h in M.entities_at(doc, (10 / 3, 2 / 3), tolerance=0.01)] == [_h(closed)]
    assert M.entities_in_window(doc, (0, 0), (4, 4)) == [_h(closed)]


def test_fit_point_spline(doc: Any) -> None:
    fit = doc.modelspace().add_spline(fit_points=[(0, 0), (1, 1), (2, 0), (3, 1)])
    m = M.measure_entity(doc, _h(fit))
    assert not m.closed and m.area is None
    assert m.length is not None and 3 * math.sqrt(2) < m.length < 5


def test_degenerate_spline_and_polyline(doc: Any) -> None:
    msp = doc.modelspace()
    dot = msp.add_open_spline([(1, 1), (1, 1), (1, 1), (1, 1)])
    assert M.entity_length(doc, _h(dot)) == 0.0
    assert M.entity_area(doc, _h(dot), assume_closed=True) == 0.0
    single = msp.add_lwpolyline([(1, 1)])
    assert M.entity_length(doc, _h(single)) == 0.0
    assert M.entity_area(doc, _h(single), assume_closed=True) == 0.0
    # coincident vertices with a bulge contribute nothing
    same = msp.add_lwpolyline([(0, 0, 1), (0, 0, 0), (3, 0, 0)], format="xyb")
    assert M.entity_length(doc, _h(same)) == _approx(3.0)


def test_length_unsupported_and_missing(doc: Any) -> None:
    text = doc.modelspace().add_text("說明", height=2)
    with pytest.raises(ValueError, match="TEXT has no length"):
        M.entity_length(doc, _h(text))
    with pytest.raises(ValueError, match="TEXT has no area"):
        M.entity_area(doc, _h(text))
    with pytest.raises(KeyError, match="No such entity: FFFF"):
        M.entity_length(doc, "FFFF")
    with pytest.raises(KeyError):
        M.entity_area(doc, "FFFF")


# -- entity area ---------------------------------------------------------------------


def test_area_circle_and_polygon(doc: Any) -> None:
    msp = doc.modelspace()
    assert M.entity_area(doc, _h(msp.add_circle((0, 0), 2))) == _approx(4 * math.pi)
    rect = msp.add_lwpolyline([(0, 0), (10, 0), (10, 5), (0, 5)], close=True)
    assert M.entity_area(doc, _h(rect)) == _approx(50.0)
    assert M.entity_area(doc, _h(rect).lower()) == _approx(50.0)  # handle is case-insensitive


def test_area_open_shapes_need_assume_closed(doc: Any) -> None:
    msp = doc.modelspace()
    open_tri = msp.add_lwpolyline([(0, 0), (4, 0), (4, 3)])
    with pytest.raises(ValueError, match="is not closed"):
        M.entity_area(doc, _h(open_tri))
    assert M.entity_area(doc, _h(open_tri), assume_closed=True) == _approx(6.0)
    arc = msp.add_arc((0, 0), 2, 0, 180)
    with pytest.raises(ValueError, match="is not closed"):
        M.entity_area(doc, _h(arc))
    # closed by its chord: a half disc
    assert M.entity_area(doc, _h(arc), assume_closed=True) == _approx(2 * math.pi)
    quarter = msp.add_arc((0, 0), 2, 0, 90)
    assert M.entity_area(doc, _h(quarter), assume_closed=True) == _approx(math.pi - 2)


# -- measure_entity ------------------------------------------------------------------


def test_measure_entity(doc: Any) -> None:
    msp = doc.modelspace()
    rect = msp.add_lwpolyline(
        [(1, 2), (5, 2), (5, 5), (1, 5)], close=True, dxfattribs={"layer": "牆"}
    )
    m = M.measure_entity(doc, _h(rect))
    assert isinstance(m, Measurement)
    assert (m.type, m.layer, m.closed) == ("LWPOLYLINE", "牆", True)
    assert m.length == _approx(14.0) and m.area == _approx(12.0)
    assert m.bbox == BoundingBox((1.0, 2.0, 0.0), (5.0, 5.0, 0.0))
    data = m.to_dict()
    assert data["bbox"]["size"] == [4.0, 3.0, 0.0] and data["bbox"]["center"] == [3.0, 3.5, 0.0]
    json.dumps(data, ensure_ascii=False)


def test_measure_open_and_unsupported(doc: Any) -> None:
    msp = doc.modelspace()
    line = M.measure_entity(doc, _h(msp.add_line((0, 0), (3, 4))))
    assert line.length == _approx(5.0) and line.area is None and not line.closed
    text = M.measure_entity(doc, _h(msp.add_text("說明", height=2)))
    assert text.length is None and text.area is None and text.bbox is not None
    assert text.to_dict()["length"] is None


def test_measure_entities_order_and_errors(doc: Any) -> None:
    msp = doc.modelspace()
    a = _h(msp.add_line((0, 0), (1, 0)))
    b = _h(msp.add_circle((0, 0), 1))
    assert [m.handle for m in M.measure_entities(doc, [b, a])] == [b, a]
    assert [m.handle for m in M.measure_entities(doc, a)] == [a]
    with pytest.raises(KeyError):
        M.measure_entities(doc, [a, "FFFF"])


# -- bounding box --------------------------------------------------------------------


def test_bounding_box_layout_and_filters(doc: Any) -> None:
    msp = doc.modelspace()
    line = msp.add_line((0, 0), (10, 0), dxfattribs={"layer": "牆"})
    msp.add_circle((20, 20), 5)
    msp.add_line((-100, -100), (-90, -90), dxfattribs={"layer": "隱藏"})
    msp.add_line((500, 500), (600, 600), dxfattribs={"layer": "凍結"})
    everything = M.bounding_box(doc)
    assert everything is not None
    assert everything.min == _approx((-100, -100, 0)) and everything.max == _approx((600, 600, 0))
    visible = M.bounding_box(doc, visible_only=True)
    assert visible is not None
    assert visible.min == _approx((0, 0, 0)) and visible.max == _approx((25, 25, 0))
    assert (visible.width, visible.height) == _approx((25, 25))
    assert visible.size == _approx((25, 25, 0)) and visible.center == _approx((12.5, 12.5, 0))
    wall = M.bounding_box(doc, layer="牆")
    assert wall == BoundingBox((0.0, 0.0, 0.0), (10.0, 0.0, 0.0))
    circles = M.bounding_box(doc, types="circle")
    assert circles is not None and circles.min == _approx((15, 15, 0))
    by_handle = M.bounding_box(doc, [_h(line)])
    assert by_handle == wall
    assert M.bounding_box(doc, _h(line)) == wall


def test_bounding_box_arc_is_precise(doc: Any) -> None:
    arc = doc.modelspace().add_arc((0, 0), 10, 0, 90)
    box = M.bounding_box(doc, _h(arc))
    assert box is not None
    assert box.min == _approx((0, 0, 0), 1e-6) and box.max == _approx((10, 10, 0), 1e-6)


def test_bounding_box_empty_and_errors(doc: Any) -> None:
    assert M.bounding_box(doc) is None
    assert M.bounding_box(doc, []) is None
    with pytest.raises(KeyError, match="No such layout"):
        M.bounding_box(doc, layout="無")
    with pytest.raises(KeyError, match="No such entity"):
        M.bounding_box(doc, ["FFFF"])


def test_bounding_box_paper_space(doc: Any) -> None:
    doc.layouts.get("Layout1").add_line((1, 1), (2, 3))
    box = M.bounding_box(doc, layout="Layout1")
    assert box == BoundingBox((1.0, 1.0, 0.0), (2.0, 3.0, 0.0))


# -- picking -------------------------------------------------------------------------


def test_entities_at_nearest_first(doc: Any) -> None:
    msp = doc.modelspace()
    line = _h(msp.add_line((0, 0), (10, 0)))
    circle = _h(msp.add_circle((5, 5), 4))
    far = _h(msp.add_line((0, 100), (10, 100)))
    hits = M.entities_at(doc, (5, 0.4), tolerance=1.0)
    assert [h.handle for h in hits] == [line, circle]
    assert hits[0].distance == _approx(0.4) and hits[1].distance == _approx(0.6, 1e-3)
    assert hits[0].to_dict() == {"handle": line, "type": "LINE", "layer": "0", "distance": 0.4}
    assert far not in {h.handle for h in hits}


def test_entities_at_hits_outline_not_interior(doc: Any) -> None:
    msp = doc.modelspace()
    circle = _h(msp.add_circle((0, 0), 10))
    rect = _h(msp.add_lwpolyline([(-20, -20), (20, -20), (20, 20), (-20, 20)], close=True))
    assert M.entities_at(doc, (0, 0), tolerance=1.0) == []
    assert [h.handle for h in M.entities_at(doc, (10.5, 0), tolerance=1.0)] == [circle]
    assert [h.handle for h in M.entities_at(doc, (-20, 3), tolerance=0.0)] == [rect]
    # the closing segment of a closed polyline can be picked too
    assert [h.handle for h in M.entities_at(doc, (-20.2, -5), tolerance=0.5)] == [rect]


def test_entities_at_arc_segment_end(doc: Any) -> None:
    msp = doc.modelspace()
    arc = _h(msp.add_arc((0, 0), 10, 0, 90))
    assert [h.handle for h in M.entities_at(doc, (7.0711, 7.0711), tolerance=0.01)] == [arc]
    # the missing part of the circle is not hit, although inside the bounding box corner
    assert M.entities_at(doc, (9.9, 9.9), tolerance=0.5) == []
    # beyond the end point the distance is measured to the end point
    hit = M.entities_at(doc, (10, -3), tolerance=5)[0]
    assert hit.distance == _approx(3.0, 1e-6)


def test_entities_at_text_point_and_insert_use_bbox(doc: Any) -> None:
    msp = doc.modelspace()
    text = _h(msp.add_text("說明文字", height=5, dxfattribs={"insert": (100, 100)}))
    point = _h(msp.add_point((50, 50)))
    block = doc.blocks.new("門")
    block.add_line((0, 0), (10, 0))
    block.add_line((10, 0), (10, 10))
    ins = _h(msp.add_blockref("門", (200, 200)))
    assert [h.handle for h in M.entities_at(doc, (102, 102), tolerance=0)] == [text]
    assert [h.handle for h in M.entities_at(doc, (50, 51), tolerance=1.5)] == [point]
    assert M.entities_at(doc, (50, 53), tolerance=1.5) == []
    # inside the block reference's bounding box, away from its lines
    inside = M.entities_at(doc, (203, 207), tolerance=0)
    assert [h.handle for h in inside] == [ins] and inside[0].distance == 0.0
    outside = M.entities_at(doc, (213, 205), tolerance=4)
    assert [h.handle for h in outside] == [ins] and outside[0].distance == _approx(3.0)


def test_entities_at_default_tolerance(doc: Any) -> None:
    msp = doc.modelspace()
    line = _h(msp.add_line((0, 0), (300, 400)))  # diagonal 500 -> tolerance 5
    assert [h.handle for h in M.entities_at(doc, (150 + 3.2, 200 - 2.4))] == [line]  # 4 away
    assert M.entities_at(doc, (150 + 4.8, 200 - 3.6)) == []  # 6 away


def test_entities_at_filters(doc: Any) -> None:
    msp = doc.modelspace()
    wall = _h(msp.add_line((0, 0), (10, 0), dxfattribs={"layer": "牆"}))
    hidden = _h(msp.add_line((0, 0), (10, 0), dxfattribs={"layer": "隱藏"}))
    frozen = _h(msp.add_circle((5, 0), 5, dxfattribs={"layer": "凍結"}))
    stray = _h(msp.add_line((0, 0), (10, 0), dxfattribs={"layer": "未定義"}))

    def pick(**kw: Any) -> set[str]:
        return {h.handle for h in M.entities_at(doc, (0, 0), tolerance=0.1, **kw)}

    assert pick() == {wall, hidden, frozen, stray}
    assert pick(visible_only=True) == {wall, stray}
    assert pick(layer="牆") == {wall}
    assert pick(types=["CIRCLE"]) == {frozen}
    assert M.entities_at(doc, (0, 0), tolerance=0.1, layout="Layout1") == []


def test_entities_at_empty_layout_and_invalid(doc: Any) -> None:
    assert M.entities_at(doc, (0, 0)) == []
    with pytest.raises(ValueError, match="Pick point"):
        M.entities_at(doc, (0,))
    with pytest.raises(ValueError, match="negative"):
        M.entities_at(doc, (0, 0), tolerance=-1)
    with pytest.raises(ValueError, match="Tolerance"):
        M.entities_at(doc, (0, 0), tolerance=float("inf"))
    with pytest.raises(KeyError, match="No such layout"):
        M.entities_at(doc, (0, 0), layout="無")


# -- window selection ----------------------------------------------------------------


@pytest.fixture
def plan(doc: Any) -> dict[str, str]:
    msp = doc.modelspace()
    return {
        "inside": _h(msp.add_line((1, 1), (4, 4))),
        "crossing": _h(msp.add_line((8, 5), (15, 5))),
        # bounding box overlaps the window corner but the line passes outside it
        "corner": _h(msp.add_line((7, 14), (14, 7))),
        "outside": _h(msp.add_line((20, 20), (30, 30))),
        "circle_in": _h(msp.add_circle((5, 5), 2)),
        # the window lies entirely inside this circle: its outline never touches it
        "around": _h(msp.add_circle((5, 5), 50)),
        "text": _h(msp.add_text("說明", height=2, dxfattribs={"insert": (9, 1)})),
    }


def test_window_selects_only_fully_inside(doc: Any, plan: dict[str, str]) -> None:
    assert set(M.entities_in_window(doc, (0, 0), (10, 10))) == {plan["inside"], plan["circle_in"]}
    # corner order does not matter
    assert set(M.entities_in_window(doc, (10, 10), (0, 0))) == {plan["inside"], plan["circle_in"]}
    assert set(M.entities_in_window(doc, (10, 0), (0, 10))) == {plan["inside"], plan["circle_in"]}


def test_crossing_selects_touching_geometry(doc: Any, plan: dict[str, str]) -> None:
    got = set(M.entities_in_window(doc, (0, 0), (10, 10), crossing=True))
    assert got == {plan["inside"], plan["circle_in"], plan["crossing"], plan["text"]}


def test_crossing_edge_cases(doc: Any) -> None:
    msp = doc.modelspace()
    through = _h(msp.add_line((-5, 5), (15, 5)))  # both ends outside, passes through
    vertical = _h(msp.add_line((5, -5), (5, 20)))
    touching = _h(msp.add_line((10, 0), (10, 20)))  # runs along the window edge
    miss_v = _h(msp.add_line((11, -5), (11, 20)))
    miss_h = _h(msp.add_line((-5, -1), (20, -1)))
    arc = _h(msp.add_arc((0, 0), 12, 0, 90))  # cuts off the far corner of the window
    got = set(M.entities_in_window(doc, (0, 0), (10, 10), crossing=True))
    assert got == {through, vertical, touching, arc}
    assert miss_v not in got and miss_h not in got


def test_window_filters_and_errors(doc: Any, plan: dict[str, str]) -> None:
    assert M.entities_in_window(doc, (0, 0), (10, 10), types="CIRCLE") == [plan["circle_in"]]
    assert M.entities_in_window(doc, (0, 0), (10, 10), layer="牆") == []
    doc.entitydb[plan["inside"]].dxf.layer = "隱藏"
    assert M.entities_in_window(doc, (0, 0), (10, 10), visible_only=True) == [plan["circle_in"]]
    with pytest.raises(ValueError, match="First corner"):
        M.entities_in_window(doc, "0,0", (1, 1))
    with pytest.raises(ValueError, match="Second corner"):
        M.entities_in_window(doc, (0, 0), (1,))
    with pytest.raises(KeyError, match="No such layout"):
        M.entities_in_window(doc, (0, 0), (1, 1), layout="無")


def test_window_returns_layout_order(doc: Any) -> None:
    msp = doc.modelspace()
    handles = [_h(msp.add_point((i, i))) for i in range(5)]
    assert M.entities_in_window(doc, (-1, -1), (10, 10)) == handles
    assert M.entities_in_window(doc, (0.5, 0.5), (2.5, 2.5), crossing=True) == handles[1:3]


# -- DxfDocument ---------------------------------------------------------------------


def test_document_methods(doc: Any) -> None:
    msp = doc.modelspace()
    rect = _h(msp.add_lwpolyline([(0, 0), (4, 0), (4, 3), (0, 3)], close=True))
    line = _h(msp.add_line((10, 0), (10, 5), dxfattribs={"layer": "隱藏"}))
    d = DxfDocument(doc)
    assert d.distance((0, 0), (3, 4)).distance == _approx(5.0)
    assert DxfDocument.distance((0, 0), (0, 2)).angle == _approx(90.0)
    assert d.entity_length(rect) == _approx(14.0)
    assert d.entity_area(rect) == _approx(12.0)
    with pytest.raises(ValueError):
        d.entity_area(line)
    assert [m.handle for m in d.measure([rect, line])] == [rect, line]
    assert d.bounding_box() == BoundingBox((0.0, 0.0, 0.0), (10.0, 5.0, 0.0))
    assert d.bounding_box(visible_only=True) == BoundingBox((0.0, 0.0, 0.0), (4.0, 3.0, 0.0))
    assert d.bounding_box([line]) == BoundingBox((10.0, 0.0, 0.0), (10.0, 5.0, 0.0))
    assert [h.handle for h in d.entities_at((10, 2), tolerance=0.1)] == [line]
    assert d.entities_at((10, 2), tolerance=0.1, visible_only=True) == []
    assert d.entities_in_window((-1, -1), (5, 5)) == [rect]
    assert d.entities_in_window((-1, -1), (10, 1), crossing=True) == [rect, line]
    assert d.entities_in_window((-1, -1), (10, 1), crossing=True, types="LINE") == [line]


def test_entities_without_extents_are_skipped(doc: Any) -> None:
    msp = doc.modelspace()
    doc.blocks.new("空")
    msp.add_blockref("空", (5, 5))  # a reference to an empty block has no extents
    line = _h(msp.add_line((0, 0), (10, 10)))
    assert [h.handle for h in M.entities_at(doc, (5, 5), tolerance=1)] == [line]
    assert M.entities_in_window(doc, (-1, -1), (11, 11)) == [line]
    assert M.entities_in_window(doc, (-1, -1), (11, 11), crossing=True) == [line]


def test_zero_length_curve_can_be_picked(doc: Any) -> None:
    dot = _h(doc.modelspace().add_line((3, 3), (3, 3)))
    hits = M.entities_at(doc, (3, 4), tolerance=2)
    assert [h.handle for h in hits] == [dot] and hits[0].distance == _approx(1.0)
    assert M.entities_in_window(doc, (0, 0), (5, 5), crossing=True) == [dot]


def test_polymesh_is_queried_by_bounding_box(doc: Any) -> None:
    mesh = doc.modelspace().add_polymesh((2, 2))
    for (m, n), loc in {(0, 0): (0, 0), (0, 1): (10, 0), (1, 0): (0, 10), (1, 1): (10, 10)}.items():
        mesh.set_mesh_vertex((m, n), loc)
    assert [h.handle for h in M.entities_at(doc, (5, 5), tolerance=0)] == [_h(mesh)]
    assert M.entities_in_window(doc, (4, 4), (20, 20), crossing=True) == [_h(mesh)]
