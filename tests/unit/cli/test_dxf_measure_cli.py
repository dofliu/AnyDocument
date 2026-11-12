"""Tests for the ``anydoc dxf`` measurement commands (T083)."""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

import ezdxf
import pytest
from typer.testing import CliRunner

from anydoc.cli import app

runner = CliRunner()


@pytest.fixture
def drawing(tmp_path: Path) -> tuple[Path, dict[str, str]]:
    doc = ezdxf.new("R2018", units=4)  # millimetres
    doc.layers.add("牆", color=1)
    doc.layers.add("隱藏").off()
    msp = doc.modelspace()
    handles = {
        "rect": msp.add_lwpolyline(
            [(0, 0), (40, 0), (40, 30), (0, 30)], close=True, dxfattribs={"layer": "牆"}
        ),
        "line": msp.add_line((50, 0), (53, 4)),
        "circle": msp.add_circle((100, 100), 10),
        "text": msp.add_text("說明", height=5, dxfattribs={"insert": (0, 50)}),
        "hidden": msp.add_line((-500, -500), (-400, -400), dxfattribs={"layer": "隱藏"}),
    }
    path = tmp_path / "in.dxf"
    doc.saveas(path)
    return path, {name: str(e.dxf.handle) for name, e in handles.items()}


def _run(*args: str) -> Any:
    result = runner.invoke(app, ["dxf", *args])
    assert result.exit_code == 0, result.output
    return json.loads(result.stdout)


def _fail(*args: str) -> str:
    result = runner.invoke(app, ["dxf", *args])
    assert result.exit_code != 0, result.output
    return result.output


def test_distance_two_points() -> None:
    data = _run("distance", "-p", "0,0", "-p", "3,4")
    assert data["length"] == pytest.approx(5.0)
    assert len(data["segments"]) == 1 and "area" not in data
    seg = data["segments"][0]
    assert (seg["dx"], seg["dy"], seg["dz"]) == (3.0, 4.0, 0.0)
    assert seg["angle"] == pytest.approx(53.13010235)


def test_distance_negative_and_3d_points() -> None:
    data = _run("distance", "-p", "-1,-1,0", "-p", "-1,-1,7")
    assert data["length"] == pytest.approx(7.0)


def test_distance_path_and_closed_area() -> None:
    data = _run("distance", "-p", "0,0", "-p", "4,0", "-p", "4,3")
    assert data["length"] == pytest.approx(7.0) and len(data["segments"]) == 2
    data = _run("distance", "-p", "0,0", "-p", "4,0", "-p", "4,3", "--closed")
    assert data["length"] == pytest.approx(12.0) and len(data["segments"]) == 3
    assert data["area"] == pytest.approx(6.0)


def test_distance_errors() -> None:
    assert "distance needs 2 or more --point, got 1" in _fail("distance", "-p", "0,0")
    assert "Invalid point" in _fail("distance", "-p", "0,0", "-p", "abc")
    assert "At least 3 points" in _fail("distance", "-p", "0,0", "-p", "1,1", "--closed")


def test_measure_handles(drawing: tuple[Path, dict[str, str]]) -> None:
    path, h = drawing
    data = _run("measure", str(path), h["rect"], h["line"], h["text"])
    assert data["units"] == "mm"
    rect, line, text = data["entities"]
    assert rect["handle"] == h["rect"] and rect["layer"] == "牆" and rect["closed"] is True
    assert rect["length"] == pytest.approx(140.0) and rect["area"] == pytest.approx(1200.0)
    assert rect["bbox"]["min"] == [0.0, 0.0, 0.0] and rect["bbox"]["size"] == [40.0, 30.0, 0.0]
    assert line["length"] == pytest.approx(5.0) and line["area"] is None
    assert text["length"] is None and text["area"] is None and text["bbox"] is not None
    assert data["total_length"] == pytest.approx(145.0)
    assert data["total_area"] == pytest.approx(1200.0)
    assert data["bbox"]["min"][0] == 0.0 and data["bbox"]["max"][0] >= 53.0


def test_measure_selection(drawing: tuple[Path, dict[str, str]]) -> None:
    path, h = drawing
    data = _run("measure", str(path), "-t", "circle")
    assert [e["handle"] for e in data["entities"]] == [h["circle"]]
    assert data["total_length"] == pytest.approx(20 * math.pi)
    assert data["total_area"] == pytest.approx(100 * math.pi)
    assert len(_run("measure", str(path), "--all")["entities"]) == 5
    assert [e["handle"] for e in _run("measure", str(path), "-l", "牆")["entities"]] == [h["rect"]]


def test_measure_errors(drawing: tuple[Path, dict[str, str]], tmp_path: Path) -> None:
    path, _ = drawing
    assert "No entities selected" in _fail("measure", str(path))
    assert "No such entity: FFFF" in _fail("measure", str(path), "FFFF")
    assert "No entities match" in _fail("measure", str(path), "-t", "ELLIPSE")
    assert "file not found" in _fail("measure", str(tmp_path / "missing.dxf"), "--all")


def test_bbox(drawing: tuple[Path, dict[str, str]]) -> None:
    path, h = drawing
    whole = _run("bbox", str(path))
    assert whole["units"] == "mm"
    assert whole["min"][:2] == [-500.0, -500.0] and whole["max"][:2] == [110.0, 110.0]
    visible = _run("bbox", str(path), "--visible-only")
    assert visible["min"][:2] == [0.0, 0.0] and visible["size"][:2] == [110.0, 110.0]
    assert visible["center"][:2] == [55.0, 55.0]
    wall = _run("bbox", str(path), "-l", "牆")
    assert wall["min"] == [0.0, 0.0, 0.0] and wall["max"] == [40.0, 30.0, 0.0]
    circle = _run("bbox", str(path), h["circle"])
    assert circle["min"] == pytest.approx([90.0, 90.0, 0.0])
    assert circle["max"] == pytest.approx([110.0, 110.0, 0.0])
    assert _run("bbox", str(path), "-t", "LINE")["max"][:2] == [53.0, 4.0]


def test_bbox_errors(drawing: tuple[Path, dict[str, str]]) -> None:
    path, h = drawing
    assert "No entities with extents" in _fail("bbox", str(path), "--layout", "Layout1")
    assert "No entities with extents" in _fail("bbox", str(path), "-t", "ELLIPSE")
    assert "No such layout: 無" in _fail("bbox", str(path), "--layout", "無")
    assert "No such entity: FFFF" in _fail("bbox", str(path), "FFFF")
    assert "not both" in _fail("bbox", str(path), h["line"], "-l", "牆")


def test_query_at(drawing: tuple[Path, dict[str, str]]) -> None:
    path, h = drawing
    rows = _run("query", str(path), "--at", "20,0.5", "--tolerance", "1")
    assert [row["handle"] for row in rows] == [h["rect"]]
    assert rows[0]["distance"] == pytest.approx(0.5)
    assert rows[0]["type"] == "LWPOLYLINE" and rows[0]["closed"] is True
    assert _run("query", str(path), "--at", "20,15", "--tolerance", "1") == []
    # default tolerance: 1 % of the visible extents diagonal (about 1.56)
    rows = _run("query", str(path), "--at", "111,100", "--visible-only")
    assert [row["handle"] for row in rows] == [h["circle"]]
    assert _run("query", str(path), "--at", "20,0", "--tolerance", "1", "-t", "LINE") == []


def test_query_window(drawing: tuple[Path, dict[str, str]]) -> None:
    path, h = drawing
    rows = _run("query", str(path), "-w", "-1,-1", "-w", "60,40")
    assert [row["handle"] for row in rows] == [h["rect"], h["line"]]
    assert all("distance" not in row for row in rows)
    rows = _run("query", str(path), "-w", "30,-10", "-w", "51,10")
    assert rows == []
    rows = _run("query", str(path), "-w", "30,-10", "-w", "51,10", "--crossing")
    assert [row["handle"] for row in rows] == [h["rect"], h["line"]]
    rows = _run("query", str(path), "-w", "-600,-600", "-w", "600,600", "--visible-only")
    assert h["hidden"] not in {row["handle"] for row in rows} and len(rows) == 4
    rows = _run("query", str(path), "-w", "-600,-600", "-w", "600,600", "-l", "隱藏")
    assert [row["handle"] for row in rows] == [h["hidden"]]


def test_query_errors(drawing: tuple[Path, dict[str, str]]) -> None:
    path, _ = drawing
    assert "Give either --at or two --window corners" in _fail("query", str(path))
    assert "Give either --at" in _fail("query", str(path), "--at", "0,0", "-w", "0,0", "-w", "1,1")
    assert "--window needs 2 corners, got 1" in _fail("query", str(path), "-w", "0,0")
    assert "Invalid point" in _fail("query", str(path), "--at", "x")
    assert "negative" in _fail("query", str(path), "--at", "0,0", "--tolerance", "-1")
    assert "No such layout: 無" in _fail("query", str(path), "--at", "0,0", "--layout", "無")
