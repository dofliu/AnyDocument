"""Tests for DXF rendering (T020)."""

from __future__ import annotations

import io
import re
from collections.abc import Callable
from pathlib import Path

import ezdxf
import pytest
from PIL import Image

from anydoc.core.model import Capability
from anydoc.core.registry import registry
from anydoc.formats.dxf import DxfDocument, DxfHandler


@pytest.fixture(autouse=True)
def _handler() -> None:
    registry.register(DxfHandler, replace=True)


@pytest.fixture
def drawing(tmp_path: Path) -> DxfDocument:
    doc = ezdxf.new("R2018", setup=True)
    doc.layers.add("RED", color=1)
    doc.layers.add("BLUE", color=5)
    msp = doc.modelspace()
    msp.add_line((0, 0), (100, 30), dxfattribs={"layer": "RED"})
    msp.add_circle((50, 50), 20, dxfattribs={"layer": "BLUE"})
    doc.layout("Layout1").add_line((0, 0), (50, 50))
    path = tmp_path / "r.dxf"
    doc.saveas(path)
    return DxfHandler().open(path)


def _paths(svg: bytes) -> int:
    return len(re.findall(rb"<(?:path|circle|line|polyline)\b", svg))


def test_render_capability() -> None:
    assert Capability.RENDER in DxfHandler.capabilities


def test_render_svg(drawing: DxfDocument) -> None:
    data = drawing.render("svg")
    assert data.startswith(b"<?xml") and b"<svg" in data
    assert _paths(data) >= 2


def test_render_png(drawing: DxfDocument) -> None:
    img = Image.open(io.BytesIO(drawing.render("png", dpi=100)))
    assert img.format == "PNG" and img.width > 0 and img.height > 0
    bigger = Image.open(io.BytesIO(drawing.render("png", dpi=200)))
    assert bigger.width > img.width


def test_render_pdf(drawing: DxfDocument) -> None:
    assert drawing.render("pdf").startswith(b"%PDF")


def test_render_writes_path(drawing: DxfDocument, tmp_path: Path) -> None:
    out = tmp_path / "out.svg"
    data = drawing.render("svg", path=out)
    assert out.read_bytes() == data


def test_layer_filter(drawing: DxfDocument) -> None:
    both = _paths(drawing.render("svg"))
    red_only = _paths(drawing.render("svg", layers=["RED"]))
    none = _paths(drawing.render("svg", layers=[]))
    assert none == 0 and 0 < red_only < both


def test_layout_selection(drawing: DxfDocument) -> None:
    assert drawing.render("svg", layout="Layout1")
    assert drawing.render("svg", layout="Layout1") != drawing.render("svg")


def test_errors(drawing: DxfDocument) -> None:
    with pytest.raises(ValueError, match="Unsupported render format"):
        drawing.render("bmp")
    with pytest.raises(ValueError, match="dpi"):
        drawing.render("png", dpi=0)
    with pytest.raises(KeyError, match="layout"):
        drawing.render("svg", layout="Nope")
    with pytest.raises(KeyError, match="layer"):
        drawing.render("svg", layers=["Nope"])


def test_render_sample(sample: Callable[[str], Path]) -> None:
    doc = DxfHandler().open(sample("dxf"))
    assert doc.render("png").startswith(b"\x89PNG")
    assert doc.render("svg")
