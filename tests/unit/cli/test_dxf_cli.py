"""Tests for the ``anydoc dxf`` layer commands (T081)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import ezdxf
import pytest
from typer.testing import CliRunner

from anydoc.cli import app
from anydoc.formats.dxf import DxfHandler

runner = CliRunner()


@pytest.fixture
def drawing(tmp_path: Path) -> Path:
    doc = ezdxf.new("R2018")
    doc.layers.add("牆", color=1)
    doc.layers.add("空")
    msp = doc.modelspace()
    msp.add_line((0, 0), (10, 0), dxfattribs={"layer": "牆"})
    msp.add_text("說明", dxfattribs={"layer": "牆"})
    path = tmp_path / "in.dxf"
    doc.saveas(path)
    return path


def _layers(path: Path) -> dict[str, Any]:
    return {info.name: info for info in DxfHandler().open(path).layers()}


def test_layers_lists_json(drawing: Path) -> None:
    result = runner.invoke(app, ["dxf", "layers", str(drawing)])
    assert result.exit_code == 0, result.output
    rows = {row["name"]: row for row in json.loads(result.stdout)}
    assert rows["牆"] == {
        "name": "牆",
        "color": 1,
        "true_color": None,
        "linetype": "Continuous",
        "on": True,
        "frozen": False,
        "locked": False,
        "current": False,
        "entities": 2,
    }
    assert rows["0"]["current"] is True and rows["空"]["entities"] == 0


def test_layer_add(drawing: Path, tmp_path: Path) -> None:
    out = tmp_path / "out.dxf"
    result = runner.invoke(
        app,
        ["dxf", "layer-add", str(drawing), "中心線", "-c", "red", "-l", "CENTER", "--locked"]
        + ["-o", str(out)],
    )
    assert result.exit_code == 0, result.output
    info = _layers(out)["中心線"]
    assert (info.color, info.linetype, info.on, info.locked) == (1, "CENTER", True, True)
    assert "中心線" not in _layers(drawing)  # source untouched


def test_layer_add_off_frozen_true_color(drawing: Path, tmp_path: Path) -> None:
    out = tmp_path / "out.dxf"
    args = ["dxf", "layer-add", str(drawing), "輔助", "--color", "#336699", "--off", "--frozen"]
    result = runner.invoke(app, [*args, "-o", str(out)])
    assert result.exit_code == 0, result.output
    info = _layers(out)["輔助"]
    assert (info.true_color, info.on, info.frozen) == ("#336699", False, True)


def test_layer_set(drawing: Path, tmp_path: Path) -> None:
    out = tmp_path / "out.dxf"
    args = ["dxf", "layer-set", str(drawing), "牆", "-c", "5", "-l", "DASHED", "--off", "--lock"]
    result = runner.invoke(app, [*args, "-o", str(out)])
    assert result.exit_code == 0, result.output
    info = _layers(out)["牆"]
    assert (info.color, info.linetype, info.on, info.frozen, info.locked) == (
        5,
        "DASHED",
        False,
        False,
        True,
    )
    result = runner.invoke(
        app, ["dxf", "layer-set", str(out), "牆", "--on", "--freeze", "--unlock", "-o", str(out)]
    )
    assert result.exit_code == 0, result.output
    info = _layers(out)["牆"]
    assert (info.color, info.on, info.frozen, info.locked) == (5, True, True, False)


def test_layer_set_current_thaws(drawing: Path, tmp_path: Path) -> None:
    out = tmp_path / "out.dxf"
    runner.invoke(app, ["dxf", "layer-set", str(drawing), "空", "--freeze", "-o", str(out)])
    result = runner.invoke(app, ["dxf", "layer-set", str(out), "空", "--current", "-o", str(out)])
    assert result.exit_code == 0, result.output
    doc = DxfHandler().open(out)
    assert doc.current_layer == "空" and doc.layer("空").frozen is False
    result = runner.invoke(
        app, ["dxf", "layer-set", str(out), "空", "--current", "--freeze", "-o", str(out)]
    )
    assert result.exit_code == 1 and "current layer" in result.output


def test_layer_rename(drawing: Path, tmp_path: Path) -> None:
    out = tmp_path / "out.dxf"
    result = runner.invoke(app, ["dxf", "layer-rename", str(drawing), "牆", "WALL", "-o", str(out)])
    assert result.exit_code == 0, result.output
    doc = DxfHandler().open(out)
    assert "牆" not in _layers(out) and doc.layer_usage()["WALL"] == 2


def test_layer_delete(drawing: Path, tmp_path: Path) -> None:
    out = tmp_path / "out.dxf"
    refused = runner.invoke(app, ["dxf", "layer-delete", str(drawing), "牆", "-o", str(out)])
    assert refused.exit_code == 1 and "in use by 2 entities" in refused.output
    assert not out.exists()

    moved = runner.invoke(
        app, ["dxf", "layer-delete", str(drawing), "牆", "--move-to", "空", "-o", str(out)]
    )
    assert moved.exit_code == 0, moved.output
    assert "moved 2 entities" in moved.stderr
    doc = DxfHandler().open(out)
    assert "牆" not in _layers(out) and doc.layer_usage()["空"] == 2

    deleted = runner.invoke(
        app, ["dxf", "layer-delete", str(drawing), "牆", "--delete-entities", "-o", str(out)]
    )
    assert deleted.exit_code == 0, deleted.output
    assert "deleted 2 entities" in deleted.stderr
    assert DxfHandler().open(out).entities() == []


def test_layer_purge(drawing: Path, tmp_path: Path) -> None:
    out = tmp_path / "out.dxf"
    result = runner.invoke(app, ["dxf", "layer-purge", str(drawing), "-o", str(out)])
    assert result.exit_code == 0, result.output
    assert "removed 1 layers: 空" in result.stderr
    assert set(_layers(out)) == {"0", "Defpoints", "牆"}


def test_errors(drawing: Path, tmp_path: Path) -> None:
    out = tmp_path / "out.dxf"
    missing = runner.invoke(app, ["dxf", "layers", str(tmp_path / "nope.dxf")])
    assert missing.exit_code == 2 and "file not found" in missing.output
    unknown = runner.invoke(
        app, ["dxf", "layer-set", str(drawing), "不存在", "-c", "1", "-o", str(out)]
    )
    assert unknown.exit_code == 1 and "No such layer: 不存在" in unknown.output
    assert "'No such layer" not in unknown.output
    bad = runner.invoke(app, ["dxf", "layer-add", str(drawing), "X", "-c", "999", "-o", str(out)])
    assert bad.exit_code == 1 and "1-255" in bad.output
    dup = runner.invoke(app, ["dxf", "layer-add", str(drawing), "牆", "-o", str(out)])
    assert dup.exit_code == 1 and "already exists" in dup.output
    assert not out.exists()

    broken = tmp_path / "broken.dxf"
    broken.write_text("not a dxf", encoding="utf-8")
    corrupt = runner.invoke(app, ["dxf", "layers", str(broken)])
    assert corrupt.exit_code == 1


# -- entity commands (T082) ----------------------------------------------------


def _entities(path: Path, *args: str) -> list[dict[str, Any]]:
    result = runner.invoke(app, ["dxf", "entities", str(path), *args])
    assert result.exit_code == 0, result.output
    rows: list[dict[str, Any]] = json.loads(result.stdout)
    return rows


def _run(*args: str) -> Any:
    result = runner.invoke(app, ["dxf", *args])
    assert result.exit_code == 0, result.output
    return result


def test_entities_lists_json(drawing: Path) -> None:
    rows = _entities(drawing)
    assert [row["type"] for row in rows] == ["LINE", "TEXT"]
    assert rows[0]["layer"] == "牆" and rows[0]["end"] == [10.0, 0.0, 0.0]
    assert rows[1]["text"] == "說明"
    assert [row["type"] for row in _entities(drawing, "--type", "text")] == ["TEXT"]
    assert _entities(drawing, "--layer", "空") == []
    assert _entities(drawing, "--layout", "Layout1") == []


def test_entity_add_each_type(drawing: Path, tmp_path: Path) -> None:
    out = tmp_path / "out.dxf"
    result = _run(
        "entity-add", str(drawing), "line", "-o", str(out), "-p", "-5,3", "-p", "5,3", "-l", "空"
    )
    assert result.stdout.strip() == str(out) and "added " in result.stderr
    _run("entity-add", str(out), "circle", "-o", str(out), "-p", "1,1", "-r", "2", "-c", "red")
    _run(
        "entity-add", str(out), "arc", "-o", str(out), "-p", "0,0", "-r", "3",
        "--start-angle", "0", "--end-angle", "90", "--linetype", "DASHED",
    )  # fmt: skip
    _run(
        "entity-add", str(out), "polyline", "-o", str(out),
        "-p", "0,0", "-p", "4,0,1", "-p", "4,3", "--closed",
    )  # fmt: skip
    _run(
        "entity-add", str(out), "text", "-o", str(out), "-p", "0,-5",
        "--text", "標題", "--height", "4", "--rotation", "15",
    )  # fmt: skip
    _run(
        "entity-add", str(out), "mtext", "-o", str(out), "-p", "0,-9",
        "--text", "一\\n二", "--width", "30", "-c", "#336699",
    )  # fmt: skip

    rows = _entities(out)[2:]
    assert [row["type"] for row in rows] == ["LINE", "CIRCLE", "ARC", "LWPOLYLINE", "TEXT", "MTEXT"]
    assert rows[0]["layer"] == "空" and rows[0]["start"] == [-5.0, 3.0, 0.0]
    assert rows[1]["color"] == 1 and rows[1]["radius"] == 2.0
    assert rows[2]["linetype"] == "DASHED" and rows[2]["end_angle"] == 90.0
    assert rows[3]["closed"] is True and rows[3]["points"][1] == [4.0, 0.0, 1.0]
    assert (rows[4]["text"], rows[4]["height"], rows[4]["rotation"]) == ("標題", 4.0, 15.0)
    assert rows[5]["text"] == "一\n二" and rows[5]["true_color"] == "#336699"
    assert not ezdxf.readfile(out).audit().has_errors


def test_entity_set(drawing: Path, tmp_path: Path) -> None:
    out = tmp_path / "out.dxf"
    line, text = (row["handle"] for row in _entities(drawing))
    _run(
        "entity-set", str(drawing), line, "-o", str(out),
        "--start", "1,1", "--end", "-2,-2", "-c", "5", "-l", "空",
    )  # fmt: skip
    _run("entity-set", str(out), text, "-o", str(out), "--text", "新", "--insert", "3,4")
    rows = _entities(out)
    assert rows[0]["start"] == [1.0, 1.0, 0.0] and rows[0]["end"] == [-2.0, -2.0, 0.0]
    assert rows[0]["color"] == 5 and rows[0]["layer"] == "空"
    assert rows[1]["text"] == "新" and rows[1]["insert"] == [3.0, 4.0, 0.0]


def test_entity_set_polyline_points(drawing: Path, tmp_path: Path) -> None:
    out = tmp_path / "out.dxf"
    _run("entity-add", str(drawing), "polyline", "-o", str(out), "-p", "0,0", "-p", "1,1")
    handle = _entities(out, "-t", "LWPOLYLINE")[0]["handle"]
    _run(
        "entity-set", str(out), handle, "-o", str(out),
        "-p", "0,0", "-p", "2,0", "-p", "2,2", "--closed",
    )  # fmt: skip
    row = _entities(out, "-t", "LWPOLYLINE")[0]
    assert len(row["points"]) == 3 and row["closed"] is True


def test_entity_delete_by_handle_and_selection(drawing: Path, tmp_path: Path) -> None:
    out = tmp_path / "out.dxf"
    line = _entities(drawing)[0]["handle"]
    result = _run("entity-delete", str(drawing), line, "-o", str(out))
    assert "deleted 1 entities" in result.stderr
    assert [row["type"] for row in _entities(out)] == ["TEXT"]
    result = _run("entity-delete", str(drawing), "--all", "-o", str(out))
    assert "deleted 2 entities" in result.stderr and _entities(out) == []
    _run("entity-delete", str(drawing), "--type", "TEXT", "-o", str(out))
    assert [row["type"] for row in _entities(out)] == ["LINE"]


def test_entity_move_copy_rotate_scale(drawing: Path, tmp_path: Path) -> None:
    out = tmp_path / "out.dxf"
    line = _entities(drawing)[0]["handle"]
    assert (
        "moved 1"
        in _run("entity-move", str(drawing), line, "-o", str(out), "--dx", "5", "--dy", "-5").stderr
    )
    assert _entities(out)[0]["start"] == [5.0, -5.0, 0.0]

    result = _run(
        "entity-copy", str(out), "--layer", "牆", "-o", str(out), "--dy", "100", "--to-layer", "空"
    )
    assert "copied 2 entities" in result.stderr
    rows = _entities(out)
    assert len(rows) == 4 and rows[2]["layer"] == "空"
    assert rows[2]["start"] == [5.0, 95.0, 0.0]

    assert (
        "rotated 1"
        in _run(
            "entity-rotate", str(out), line, "-o", str(out), "--angle", "90", "--base", "5,-5"
        ).stderr
    )
    assert _entities(out)[0]["end"] == pytest.approx([5.0, 5.0, 0.0])

    assert (
        "scaled 4"
        in _run("entity-scale", str(out), "--all", "-o", str(out), "--factor", "2").stderr
    )
    assert _entities(out)[0]["start"] == pytest.approx([10.0, -10.0, 0.0])
    assert not ezdxf.readfile(out).audit().has_errors


@pytest.mark.parametrize(
    ("args", "message"),
    [
        (["entity-add", "hexagon", "-p", "0,0"], "Unknown entity type"),
        (["entity-add", "line", "-p", "0,0"], "line needs 2 --point, got 1"),
        (["entity-add", "polyline", "-p", "0,0"], "polyline needs 2 or more --point"),
        (["entity-add", "circle", "-p", "0,0"], "circle needs --radius"),
        (["entity-add", "arc", "-p", "0,0", "-r", "1"], "arc needs --start-angle"),
        (["entity-add", "text", "-p", "0,0"], "text needs --text"),
        (["entity-add", "line", "-p", "0;0", "-p", "1,1"], "Invalid point"),
        (["entity-add", "line", "-p", "0,0", "-p", "1,1", "-l", "無"], "No such layer: 無"),
        (["entity-add", "circle", "-p", "0,0", "-r", "-1"], "Radius must be greater than 0"),
        (["entity-set", "FFFF", "-c", "1"], "No such entity: FFFF"),
        (["entity-delete"], "No entities selected"),
        (["entity-delete", "FFFF"], "No such entity: FFFF"),
        (["entity-delete", "FFFF", "--all"], "not both"),
        (["entity-move", "--layer", "空", "--dx", "1"], "No entities match"),
        (["entity-rotate", "--all", "--angle", "90", "--base", "x"], "Invalid point"),
        (["entity-scale", "--all", "--factor", "0"], "Scale factor must be greater than 0"),
        (["entities", "--layout", "無"], "No such layout: 無"),
    ],
)
def test_entity_errors(drawing: Path, tmp_path: Path, args: list[str], message: str) -> None:
    out = tmp_path / "out.dxf"
    extra = [] if args[0] == "entities" else ["-o", str(out)]
    result = runner.invoke(app, ["dxf", args[0], str(drawing), *args[1:], *extra])
    assert result.exit_code == 1, result.output
    assert message in result.stderr
    assert not out.exists()


def test_entity_set_radius_on_line_fails(drawing: Path, tmp_path: Path) -> None:
    line = _entities(drawing)[0]["handle"]
    result = runner.invoke(
        app, ["dxf", "entity-set", str(drawing), line, "-o", str(tmp_path / "o.dxf"), "-r", "2"]
    )
    assert result.exit_code == 1 and "'radius' does not apply to LINE" in result.stderr
