"""Round-trip tests for DXF dimensions (T085): open -> edit -> save -> reopen."""

from __future__ import annotations

import re
from collections.abc import Callable
from pathlib import Path

import ezdxf
import pytest

import anydoc
from anydoc.formats.dxf import DxfDocument
from anydoc.formats.dxf.entities import decode_text


def _open(path: Path) -> DxfDocument:
    doc = anydoc.open(path)
    assert isinstance(doc, DxfDocument)
    return doc


def _shapes(svg: bytes) -> int:
    return len(re.findall(rb"<(?:path|circle|line|polyline|text)\b", svg))


def _drawn_text(doc: DxfDocument, handle: str) -> list[str]:
    native = doc.native
    block = native.blocks.get(native.entitydb[handle].dxf.geometry)
    return [decode_text(e.plain_text()) for e in block if e.dxftype() in ("TEXT", "MTEXT")]


def test_dimensions_on_sample_survive_save_and_reopen(
    sample: Callable[[str], Path], tmp_path: Path
) -> None:
    """Dimension the sample drawing (line 0,0-100,0 and circle r=25) and reopen it."""
    doc = _open(sample("dxf"))
    counts_before = doc.entity_counts()
    circle = doc.select(types="CIRCLE")[0]
    linear = doc.add_linear_dimension((0, 0), (100, 0), distance=-15, layer="標註", text_height=4)
    aligned = doc.add_aligned_dimension(
        (0, 0), (50, 50), 8, text="斜距 <>", text_height=4, decimal_separator="."
    )
    radius = doc.add_radius_dimension(entity=circle, angle=45, text_height=4)
    diameter = doc.add_diameter_dimension(entity=circle, angle=135, text_height=4)
    out = doc.save(tmp_path / "out.dxf")

    again = _open(out)
    assert again.entity_counts() == {**counts_before, "DIMENSION": 4}
    by_handle = {d.handle: d for d in again.dimensions()}
    assert list(by_handle) == [linear, aligned, radius, diameter]
    assert by_handle[linear].kind == "linear" and by_handle[linear].layer == "標註"
    assert by_handle[linear].measurement == pytest.approx(100.0)
    assert by_handle[linear].text == "100"
    assert by_handle[aligned].kind == "aligned"
    assert by_handle[aligned].measurement == pytest.approx(50 * 2**0.5)
    assert by_handle[aligned].text == "斜距 70.71" and by_handle[aligned].override == "斜距 <>"
    assert (by_handle[radius].kind, by_handle[radius].measurement) == ("radius", 25.0)
    assert by_handle[radius].points["center"] == (50.0, 50.0, 0.0)
    assert (by_handle[diameter].kind, by_handle[diameter].text) == ("diameter", "Ø50")
    for handle, info in by_handle.items():
        assert _drawn_text(again, handle) == [info.text]
    assert not ezdxf.readfile(out).audit().has_errors


@pytest.mark.parametrize("version", ["R12", "R2000", "R2010", "R2018"])
def test_dimension_roundtrip_across_versions(version: str, tmp_path: Path) -> None:
    src = tmp_path / "in.dxf"
    ezdxf.new(version).saveas(src)
    doc = _open(src)
    circle = doc.add_circle((0, 0), 12.5)
    linear = doc.add_linear_dimension((0, 0), (40, 30), base=(0, -10), text="寬 <>")
    vertical = doc.add_linear_dimension((0, 0), (40, 30), distance=5, angle="vertical")
    aligned = doc.add_aligned_dimension((0, 0), (40, 30), 6)
    radius = doc.add_radius_dimension(entity=circle, angle=30)
    mid = doc.save(tmp_path / "mid.dxf")

    doc = _open(mid)
    values = {d.handle: (d.kind, d.measurement, d.text) for d in doc.dimensions()}
    assert values == {
        linear: ("linear", pytest.approx(40.0), "寬 40"),
        vertical: ("linear", pytest.approx(30.0), "30"),
        aligned: ("aligned", pytest.approx(50.0), "50"),
        radius: ("radius", pytest.approx(12.5), "R12,5"),  # the style's separator
    }
    assert doc.dimension(linear).override == "寬 <>"

    # Edit after reopening: scale everything, redraw, change one text, delete one.
    doc.scale_entities([circle, linear, vertical, aligned, radius], 2, base=(0, 0))
    assert sorted(doc.update_dimensions()) == sorted([linear, vertical, aligned, radius])
    doc.set_dimension_text(aligned, "斜邊 <> 單位")
    assert doc.delete_dimensions([vertical]) == 1
    out = doc.save(tmp_path / "out.dxf")

    again = _open(out)
    values = {d.handle: (d.measurement, d.text) for d in again.dimensions()}
    assert values == {
        linear: (pytest.approx(80.0), "寬 80"),
        aligned: (pytest.approx(100.0), "斜邊 100 單位"),
        radius: (pytest.approx(25.0), "R25"),
    }
    for handle, (_, text) in values.items():
        assert _drawn_text(again, handle) == [text]
    native = ezdxf.readfile(out)
    assert not native.audit().has_errors
    assert len([b for b in native.blocks if b.name.startswith("*D")]) == 3
    assert again.entity_length(circle) == pytest.approx(2 * 3.141592653589793 * 25)


def test_dimensions_affect_rendering(tmp_path: Path) -> None:
    src = tmp_path / "in.dxf"
    ezdxf.new("R2010").saveas(src)
    doc = _open(src)
    doc.add_line((0, 0), (100, 0))
    plain = _shapes(doc.render("svg"))
    handle = doc.add_linear_dimension((0, 0), (100, 0), distance=10, text_height=5)
    out = doc.save(tmp_path / "out.dxf")

    again = _open(out)
    with_dim = _shapes(again.render("svg"))
    assert with_dim > plain
    again.delete_dimensions([handle])
    assert _shapes(again.render("svg")) == plain
    assert again.render("png").startswith(b"\x89PNG")


def test_dimension_text_in_big5_free_unicode_roundtrip(tmp_path: Path) -> None:
    """Chinese override text survives in R12 (non-Unicode code page) drawings."""
    src = tmp_path / "in.dxf"
    ezdxf.new("R12").saveas(src)
    doc = _open(src)
    handle = doc.add_aligned_dimension((0, 0), (3, 4), 1, text="繁體中文 <> 公尺")
    out = doc.save(tmp_path / "out.dxf")
    info = _open(out).dimension(handle)
    assert info.text == "繁體中文 5 公尺" and info.override == "繁體中文 <> 公尺"
