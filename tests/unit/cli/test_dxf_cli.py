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
