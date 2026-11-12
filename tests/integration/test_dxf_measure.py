"""Integration tests for DXF measurement on saved and reopened drawings (T083)."""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any

import ezdxf
import pytest

import anydoc
from anydoc.formats.dxf import DxfDocument, DxfHandler


def _approx(value: Any, tol: float = 1e-6) -> Any:
    return pytest.approx(value, abs=tol)


def test_measure_generated_sample(sample: Any) -> None:
    """The fixture drawing: a 100-long line, a circle of radius 25 and a Chinese title."""
    doc = anydoc.open(sample("dxf"))
    assert isinstance(doc, DxfDocument)
    line = doc.select(types="LINE")[0]
    circle = doc.select(types="CIRCLE")[0]
    assert doc.entity_length(line) == _approx(100.0)
    assert doc.entity_length(circle) == _approx(50 * math.pi)
    assert doc.entity_area(circle) == _approx(625 * math.pi)
    wall = doc.bounding_box(layer="牆")
    assert wall is not None
    assert wall.min == _approx((0, 0, 0)) and wall.max == _approx((100, 75, 0))
    whole = doc.bounding_box()
    assert whole is not None and whole.min[1] <= -10  # the title sits below the line
    assert [hit.handle for hit in doc.entities_at((50, 0.2), tolerance=0.5)] == [line]
    assert doc.entities_in_window((20, 20), (80, 80)) == [circle]
    assert set(doc.entities_in_window((20, -1), (80, 80), crossing=True)) == {line, circle}


@pytest.mark.parametrize("version", ["R12", "R2000", "R2010", "R2018"])
def test_measure_after_edit_save_and_reopen(tmp_path: Path, version: str) -> None:
    """Entities added through the editing API measure the same after a round trip."""
    doc = DxfHandler().open(_new(tmp_path / "blank.dxf", version))
    doc.add_layer("WALL", color="red")
    line = doc.add_line((0, 0), (30, 40), layer="WALL")
    circle = doc.add_circle((100, 0), 10, layer="WALL")
    arc = doc.add_arc((0, 100), 20, 0, 90)
    handles = [line, circle, arc]
    if version != "R12":
        handles.append(
            doc.add_polyline([(0, 0, 0), (40, 0, 1), (40, 20, 0), (0, 20, 1)], closed=True)
        )
    before = doc.measure(handles)
    saved = doc.save(tmp_path / f"out_{version}.dxf")

    reopened = DxfHandler().open(saved)
    after = reopened.measure(handles)
    for old, new in zip(before, after, strict=True):
        assert (new.handle, new.type, new.closed) == (old.handle, old.type, old.closed)
        assert new.length == _approx(old.length)
        assert new.area == (None if old.area is None else _approx(old.area))
        assert new.bbox is not None and old.bbox is not None
        assert new.bbox.min == _approx(old.bbox.min) and new.bbox.max == _approx(old.bbox.max)
    assert after[0].length == _approx(50.0)
    assert after[1].length == _approx(20 * math.pi) and after[1].area == _approx(100 * math.pi)
    assert after[2].length == _approx(10 * math.pi) and after[2].area is None
    if version != "R12":
        # a 40 x 20 slot with semicircular ends of radius 10
        assert after[3].length == _approx(80 + 20 * math.pi)
        assert after[3].area == _approx(800 + 100 * math.pi)
    box = reopened.bounding_box(layer="WALL")
    assert box is not None
    assert box.min == _approx((0, -10, 0)) and box.max == _approx((110, 40, 0))
    assert [h.handle for h in reopened.entities_at((9, 12), tolerance=0.1)] == [line]


def test_measurements_follow_transformations(tmp_path: Path) -> None:
    """Scaling doubles lengths and quadruples areas; moving and rotating keep them."""
    doc = DxfHandler().open(_new(tmp_path / "blank.dxf", "R2018"))
    rect = doc.add_polyline([(0, 0), (4, 0), (4, 3), (0, 3)], closed=True)
    doc.move_entities(rect, 10, 10)
    doc.rotate_entities(rect, 30, (10, 10))
    assert doc.entity_length(rect) == _approx(14.0) and doc.entity_area(rect) == _approx(12.0)
    doc.scale_entities(rect, 2, (10, 10))
    assert doc.entity_length(rect) == _approx(28.0) and doc.entity_area(rect) == _approx(48.0)
    box = doc.bounding_box([rect])
    assert box is not None
    # rotated 8 x 6 rectangle anchored at (10, 10)
    assert box.min == _approx((10 - 6 * math.sin(math.radians(30)), 10, 0))
    assert box.width == _approx(8 * math.cos(math.radians(30)) + 3)
    assert doc.entities_in_window((0, 0), (30, 30)) == [rect]


def test_old_style_polyline_from_r12_file(tmp_path: Path) -> None:
    """R12 drawings store polylines as POLYLINE/VERTEX; they are measured too."""
    raw = ezdxf.new("R12")
    raw.modelspace().add_polyline2d([(0, 0), (30, 0), (30, 40)], close=True)
    path = tmp_path / "r12.dxf"
    raw.saveas(path)
    doc = DxfHandler().open(path)
    (handle,) = doc.select(types="POLYLINE")
    (m,) = doc.measure(handle)
    assert m.closed and m.length == _approx(120.0) and m.area == _approx(600.0)
    assert [h.handle for h in doc.entities_at((15, 20), tolerance=0.1)] == [handle]


def _new(path: Path, version: str) -> Path:
    ezdxf.new(version).saveas(path)
    return path
