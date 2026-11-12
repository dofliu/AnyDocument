"""Round-trip tests for DXF entity editing (T082): open -> edit -> save -> reopen."""

from __future__ import annotations

import re
from collections.abc import Callable
from pathlib import Path

import ezdxf
import pytest

import anydoc
from anydoc.formats.dxf import DxfDocument, DxfHandler


def _open(path: Path) -> DxfDocument:
    doc = anydoc.open(path)
    assert isinstance(doc, DxfDocument)
    return doc


def _shapes(svg: bytes) -> int:
    return len(re.findall(rb"<(?:path|circle|line|polyline|text)\b", svg))


def test_added_entities_survive_save_and_reopen(
    sample: Callable[[str], Path], tmp_path: Path
) -> None:
    doc = _open(sample("dxf"))
    counts_before = doc.entity_counts()
    doc.add_layer("新增", color="blue")
    handles = {
        "LINE": doc.add_line((0, 0), (50, 50), layer="新增"),
        "CIRCLE": doc.add_circle((10, 10), 5, layer="新增", color=2),
        "ARC": doc.add_arc((20, 20), 8, 45, 135, layer="新增", linetype="DASHED"),
        "LWPOLYLINE": doc.add_polyline(
            [(0, 0), (30, 0), (30, 20, 0.5), (0, 20)], closed=True, layer="新增"
        ),
        "TEXT": doc.add_text("新增文字", (5, 60), height=4, rotation=15, layer="新增"),
        "MTEXT": doc.add_mtext("多行\n說明", (5, 80), height=3, width=40, layer="新增"),
    }
    before = {kind: doc.entity(handle) for kind, handle in handles.items()}
    out = doc.save(tmp_path / "out.dxf")

    again = _open(out)
    for kind, handle in handles.items():
        detail = again.entity(handle)
        assert detail.type == kind and detail.layer == "新增"
        assert detail.color == before[kind].color and detail.linetype == before[kind].linetype
        assert detail.geometry.keys() == before[kind].geometry.keys()
        for key, value in before[kind].geometry.items():
            if key == "points":
                assert [pytest.approx(p) for p in value] == detail.geometry[key]
            else:
                assert detail.geometry[key] == pytest.approx(value), (kind, key)
    assert again.entity_counts() == {
        **counts_before,
        **{k: counts_before.get(k, 0) + 1 for k in handles},
    }
    assert "新增文字" in again.text() and "多行\n說明" in again.text()
    assert again.layer_usage()["新增"] == 6
    assert not ezdxf.readfile(out).audit().has_errors


def test_modify_delete_transform_copy_roundtrip(
    sample: Callable[[str], Path], tmp_path: Path
) -> None:
    doc = _open(sample("dxf"))
    (line,) = doc.select(types="LINE")
    (circle,) = doc.select(types="CIRCLE")
    (text,) = doc.select(types="TEXT")
    title = doc.entity(text).geometry["text"]

    doc.modify_entity(line, end=(200, 0), color="red")
    doc.modify_entity(text, text=title + "（修訂）", height=8)
    doc.move_entities([circle], 100, 0)
    doc.scale_entities([circle], 2, base=(150, 50))
    (copy,) = doc.copy_entities([circle], 0, 200)
    doc.rotate_entities([copy], 90, base=(0, 0))
    doc.delete_entities([circle])
    out = doc.save(tmp_path / "out.dxf")

    again = _open(out)
    assert again.entity(line).geometry["end"] == (200.0, 0.0, 0.0)
    assert again.entity(line).color == 1
    assert again.entity(text).geometry["text"] == title + "（修訂）"
    assert again.entity(text).geometry["height"] == 8.0
    with pytest.raises(KeyError):
        again.entity(circle)
    moved = again.entity(copy).geometry
    assert moved["radius"] == pytest.approx(50.0)
    assert moved["center"] == pytest.approx((-250.0, 150.0, 0.0))
    assert again.entity_counts() == {"CIRCLE": 1, "LINE": 1, "TEXT": 1}
    assert not ezdxf.readfile(out).audit().has_errors


def test_edits_affect_rendering(sample: Callable[[str], Path], tmp_path: Path) -> None:
    doc = _open(sample("dxf"))
    baseline = _shapes(doc.render("svg"))
    doc.add_polyline([(0, 0), (20, 0), (20, 20)], closed=True)
    doc.add_circle((200, 200), 10)
    out = doc.save(tmp_path / "out.dxf")
    again = _open(out)
    added = _shapes(again.render("svg"))
    assert added > baseline
    again.delete_entities(again.select())
    assert _shapes(again.render("svg")) < baseline
    assert again.render("png")[:4] == b"\x89PNG"


@pytest.mark.parametrize("version", ["R12", "R2000", "R2010", "R2018"])
def test_entity_edits_across_dxf_versions(version: str, tmp_path: Path) -> None:
    src = tmp_path / "in.dxf"
    ezdxf.new(version).saveas(src)
    doc = _open(src)
    line = doc.add_line((0, 0), (10, 0))
    circle = doc.add_circle((5, 5), 2)
    arc = doc.add_arc((0, 0), 3, 0, 90)
    text = doc.add_text("版本測試", (1, 1))
    expected = {"ARC": 1, "CIRCLE": 1, "LINE": 2, "TEXT": 1}
    if version == "R12":
        with pytest.raises(ValueError, match="requires DXF R2000"):
            doc.add_polyline([(0, 0), (1, 1)])
    else:
        doc.add_polyline([(0, 0), (1, 1), (2, 0)])
        doc.add_mtext("多行", (0, 5))
        expected |= {"LWPOLYLINE": 1, "MTEXT": 1}
    doc.move_entities([line, circle, arc, text], 1, 1)
    doc.copy_entities([line], 0, 10)
    out = doc.save(tmp_path / "out.dxf")

    again = _open(out)
    assert again.release == version
    assert again.entity_counts() == expected
    assert again.entity(circle).geometry["center"] == (6.0, 6.0, 0.0)
    assert again.entity(text).geometry["text"] == "版本測試"
    assert "版本測試" in again.text()
    assert [e.text for e in again.entities() if e.type == "TEXT"] == ["版本測試"]
    if version != "R12":
        assert "多行" in again.text()
    assert not ezdxf.readfile(out).audit().has_errors


def test_to_bytes_reflects_entity_edits(sample: Callable[[str], Path]) -> None:
    doc = _open(sample("dxf"))
    handle = doc.add_text("位元組往返", (0, 0))
    again = DxfHandler().open(doc.to_bytes())
    assert again.entity(handle).geometry["text"] == "位元組往返"
