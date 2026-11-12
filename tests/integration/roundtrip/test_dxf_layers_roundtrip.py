"""Round-trip tests for DXF layer management (T081): open -> edit -> save -> reopen."""

from __future__ import annotations

import re
from collections.abc import Callable
from pathlib import Path

import ezdxf
import pytest

import anydoc
from anydoc.formats.dxf import DxfDocument, LayerInfo


def _open(path: Path) -> DxfDocument:
    doc = anydoc.open(path)
    assert isinstance(doc, DxfDocument)
    return doc


def _shapes(svg: bytes) -> int:
    return len(re.findall(rb"<(?:path|circle|line|polyline|text)\b", svg))


def test_layer_edits_survive_save_and_reopen(sample: Callable[[str], Path], tmp_path: Path) -> None:
    doc = _open(sample("dxf"))
    assert {"牆", "標註"} <= {info.name for info in doc.layers()}
    text_before = doc.text()

    doc.add_layer("中心線", color="red", linetype="CENTER")
    doc.add_layer("輔助", color="#336699", on=False, frozen=True, locked=True)
    doc.set_layer("牆", color=5, linetype="DASHED", locked=True)
    doc.rename_layer("標註", "文字")
    doc.set_current_layer("中心線")
    out = doc.save(tmp_path / "out.dxf")

    again = _open(out)
    layers = {info.name: info for info in again.layers()}
    assert "標註" not in layers
    assert layers["中心線"] == LayerInfo("中心線", 1, "CENTER", True, False, False, None)
    assert layers["輔助"] == LayerInfo("輔助", 7, "Continuous", False, True, True, "#336699")
    assert layers["牆"] == LayerInfo("牆", 5, "DASHED", True, False, True, None)
    assert again.current_layer == "中心線"
    assert again.layer_usage()["文字"] == 1 and again.layer_usage()["牆"] == 2
    assert again.text() == text_before
    assert [e.layer for e in again.entities() if e.type == "TEXT"] == ["文字"]
    assert not ezdxf.readfile(out).audit().has_errors


def test_delete_layer_roundtrip(sample: Callable[[str], Path], tmp_path: Path) -> None:
    doc = _open(sample("dxf"))
    assert doc.delete_layer("標註", move_to="牆") == 1
    assert doc.delete_layer("牆", delete_entities=True) == 3
    out = doc.save(tmp_path / "out.dxf")

    again = _open(out)
    assert {"牆", "標註"}.isdisjoint(info.name for info in again.layers())
    assert again.entities() == [] and again.text() == ""
    assert not ezdxf.readfile(out).audit().has_errors


def test_visibility_changes_affect_rendering(sample: Callable[[str], Path], tmp_path: Path) -> None:
    doc = _open(sample("dxf"))
    full = _shapes(doc.render("svg"))
    doc.set_layer("牆", on=False)
    out = doc.save(tmp_path / "off.dxf")

    again = _open(out)
    assert again.layer("牆").on is False
    hidden = _shapes(again.render("svg"))
    assert 0 < hidden < full
    again.set_layer("牆", on=True, frozen=True)
    assert _shapes(again.render("svg")) == hidden
    again.set_layer("牆", frozen=False)
    assert _shapes(again.render("svg")) == full


@pytest.mark.parametrize("version", ["R12", "R2000", "R2010", "R2018"])
def test_layer_edits_across_dxf_versions(tmp_path: Path, version: str) -> None:
    native = ezdxf.new(version)
    native.layers.add("WALL", color=1)
    native.modelspace().add_line((0, 0), (10, 10), dxfattribs={"layer": "WALL"})
    src = tmp_path / "in.dxf"
    native.saveas(src)

    doc = _open(src)
    doc.add_layer("NOTES", color=3, linetype="DASHDOT", locked=True)
    doc.rename_layer("WALL", "WALLS")
    doc.set_layer("WALLS", color=2, on=False)
    out = doc.save(tmp_path / "out.dxf")

    again = _open(out)
    assert again.release == version
    assert again.layer("NOTES") == LayerInfo("NOTES", 3, "DASHDOT", True, False, True, None)
    assert again.layer("WALLS") == LayerInfo("WALLS", 2, "Continuous", False, False, False, None)
    assert [e.layer for e in again.entities()] == ["WALLS"]
