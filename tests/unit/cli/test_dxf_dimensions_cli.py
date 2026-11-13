"""Tests for the ``anydoc dxf`` dimension commands (T085)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import ezdxf
import pytest
from typer.testing import CliRunner

from anydoc.cli import app
from anydoc.formats.dxf import DxfDocument, DxfHandler

runner = CliRunner()


@pytest.fixture
def drawing(tmp_path: Path) -> Path:
    doc = ezdxf.new("R2018")
    doc.layers.add("標註", color=3)
    msp = doc.modelspace()
    msp.add_line((0, 0), (100, 0))
    msp.add_circle((50, 50), 25)
    path = tmp_path / "in.dxf"
    doc.saveas(path)
    return path


def _run(*args: str) -> Any:
    result = runner.invoke(app, ["dxf", *args])
    assert result.exit_code == 0, result.output
    return result


def _fails(*args: str) -> str:
    result = runner.invoke(app, ["dxf", *args])
    assert result.exit_code != 0
    return str(result.output)


def _json(*args: str) -> Any:
    return json.loads(_run(*args).stdout)


def _open(path: Path) -> DxfDocument:
    return DxfHandler().open(path)


def _added(result: Any) -> str:
    return str(result.stderr.split("added ")[1].split()[0])


def test_dim_add_linear_and_list(drawing: Path, tmp_path: Path) -> None:
    out = tmp_path / "out.dxf"
    result = _run(
        "dim-add", str(drawing), "linear", "-o", str(out), "-p", "0,0", "-p", "100,0", "-d", "10"
    )
    assert result.stdout.strip() == str(out)
    assert "measurement 100 text 100" in result.stderr
    handle = _added(result)
    (dim,) = _json("dims", str(out))
    assert dim["handle"] == handle and dim["kind"] == "linear"
    assert dim["measurement"] == 100.0 and dim["text"] == "100" and dim["override"] is None
    assert dim["points"]["base"] == pytest.approx([0.0, 10.0, 0.0])
    assert _json("dims", str(drawing)) == []


def test_dim_add_linear_vertical_with_base_and_options(drawing: Path, tmp_path: Path) -> None:
    out = tmp_path / "out.dxf"
    result = _run(
        "dim-add",
        str(drawing),
        "LINEAR",
        "-o",
        str(out),
        "-p",
        "0,0",
        "-p",
        "30,40",
        "--base",
        "-5,0",
        "--angle",
        "vertical",
        "--text",
        "高 <> 公分",
        "--layer",
        "標註",
        "--color",
        "red",
        "--text-height",
        "3",
        "--arrow-size",
        "2",
        "--decimals",
        "1",
    )
    info = _open(out).dimension(_added(result))
    assert (info.measurement, info.angle, info.layer) == (40.0, 90.0, "標註")
    assert info.text == "高 40 公分" and info.override == "高 <> 公分"
    assert _open(out).entity(info.handle).color == 1


def test_dim_add_aligned_radius_diameter_and_filters(drawing: Path, tmp_path: Path) -> None:
    out = tmp_path / "out.dxf"
    circle = _open(drawing).select(types="CIRCLE")[0]
    _run("dim-add", str(drawing), "aligned", "-o", str(out), "-p", "0,0", "-p", "30,40", "-d", "5")
    _run("dim-add", str(out), "radius", "-o", str(out), "-e", circle, "-a", "45", "-l", "標註")
    _run("dim-add", str(out), "diameter", "-o", str(out), "-p", "0,0", "-r", "4")
    dims = _json("dims", str(out))
    assert [(d["kind"], d["measurement"], d["text"]) for d in dims] == [
        ("aligned", pytest.approx(50.0), "50"),
        ("radius", 25.0, "R25"),
        ("diameter", 8.0, "Ø8"),
    ]
    assert [d["kind"] for d in _json("dims", str(out), "-k", "radius", "-k", "diameter")] == [
        "radius",
        "diameter",
    ]
    assert [d["kind"] for d in _json("dims", str(out), "--layer", "標註")] == ["radius"]
    assert _json("dims", str(out), "--layout", "Layout1") == []


def test_dim_add_rejects_bad_arguments(drawing: Path, tmp_path: Path) -> None:
    out = tmp_path / "out.dxf"
    base = ("dim-add", str(drawing))
    two = ("-p", "0,0", "-p", "10,0")
    assert "Unknown dimension kind" in _fails(*base, "arc", "-o", str(out), *two)
    assert "exactly 2 --point" in _fails(*base, "linear", "-o", str(out), "-p", "0,0", "-d", "1")
    assert "exactly one of base and distance" in _fails(*base, "linear", "-o", str(out), *two)
    assert "Invalid angle" in _fails(*base, "linear", "-o", str(out), *two, "-d", "1", "-a", "up")
    assert "do not apply to linear" in _fails(
        *base, "linear", "-o", str(out), *two, "-d", "1", "-r", "5"
    )
    assert "aligned needs --distance" in _fails(*base, "aligned", "-o", str(out), *two)
    assert "do not apply to aligned" in _fails(
        *base, "aligned", "-o", str(out), *two, "-d", "1", "-a", "0"
    )
    assert "do not apply to radius" in _fails(
        *base, "radius", "-o", str(out), "-p", "0,0", "-r", "5", "-d", "1"
    )
    assert "at most 1 --point" in _fails(*base, "radius", "-o", str(out), *two, "-r", "5")
    assert "center and radius, or entity" in _fails(*base, "diameter", "-o", str(out))
    assert "Invalid angle" in _fails(
        *base, "radius", "-o", str(out), "-p", "0,0", "-r", "5", "-a", "vertical"
    )
    assert "No such dimension style" in _fails(
        *base, "linear", "-o", str(out), *two, "-d", "1", "--style", "無"
    )
    assert "Unknown dimension kind" in _fails("dims", str(drawing), "-k", "arc")
    assert not out.exists()


def test_dim_set_update_and_delete(drawing: Path, tmp_path: Path) -> None:
    out = tmp_path / "out.dxf"
    handle = _added(
        _run(
            "dim-add", str(drawing), "linear", "-o", str(out), "-p", "0,0", "-p", "10,0", "-d", "2"
        )
    )
    result = _run("dim-set", str(out), handle, "-o", str(out), "--text", "約 <> 公尺")
    assert f"dimension {handle} text 約 10 公尺" in result.stderr
    assert _open(out).dimension(handle).override == "約 <> 公尺"
    _run("dim-set", str(out), handle, "-o", str(out), "--reset")
    assert _open(out).dimension(handle).text == "10"
    assert "exactly one of --text and --reset" in _fails(
        "dim-set", str(out), handle, "-o", str(out)
    )
    assert "exactly one of --text and --reset" in _fails(
        "dim-set", str(out), handle, "-o", str(out), "--text", "x", "--reset"
    )
    assert "No such entity" in _fails("dim-set", str(out), "FFFF", "-o", str(out), "--reset")

    _run("entity-scale", str(out), handle, "-o", str(out), "--factor", "3")
    result = _run("dim-update", str(out), "-o", str(out))
    assert "updated 1 dimensions" in result.stderr
    doc = _open(out)
    assert doc.dimension(handle).measurement == pytest.approx(30.0)
    native = doc.native
    block = native.blocks.get(native.entitydb[handle].dxf.geometry)
    assert [e.plain_text() for e in block if e.dxftype() == "MTEXT"] == ["30"]
    assert "updated 1 dimensions" in _run("dim-update", str(out), handle, "-o", str(out)).stderr
    assert "No such entity" in _fails("dim-update", str(out), "FFFF", "-o", str(out))

    line = doc.select(types="LINE")[0]
    assert "not a DIMENSION" in _fails("dim-delete", str(out), line, "-o", str(out))
    result = _run("dim-delete", str(out), handle, "-o", str(out))
    assert "deleted 1 dimensions" in result.stderr
    final = _open(out)
    assert final.dimensions() == [] and final.entity_counts() == {"CIRCLE": 1, "LINE": 1}
    assert not ezdxf.readfile(out).audit().has_errors


def test_missing_file_fails(tmp_path: Path) -> None:
    assert "file not found" in _fails("dims", str(tmp_path / "none.dxf"))
