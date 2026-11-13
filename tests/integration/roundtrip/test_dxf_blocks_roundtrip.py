"""Round-trip tests for DXF blocks (T084): open -> edit -> save -> reopen."""

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


def _build(doc: DxfDocument) -> tuple[str, str]:
    """Define block DOOR from two entities, add attributes and insert it twice."""
    line = doc.add_line((0, 0), (10, 0))
    circle = doc.add_circle((5, 5), 2)
    doc.define_block("DOOR", base_point=(0, 0), handles=[line, circle], description="door")
    doc.add_attdef("DOOR", "NO", (0, -3), default="D-00", prompt="門號?", height=2)
    doc.add_attdef("DOOR", "MAT", (0, -6), default="木", constant=True)
    first = doc.insert_block("DOOR", (100, 50), attributes={"NO": "大門"})
    second = doc.insert_block("DOOR", (200, 50), scale=(2, 2), rotation=90)
    return first, second


def test_blocks_survive_save_and_reopen(sample: Callable[[str], Path], tmp_path: Path) -> None:
    doc = _open(sample("dxf"))
    counts_before = doc.entity_counts()
    first, second = _build(doc)
    out = doc.save(tmp_path / "out.dxf")

    again = _open(out)
    block = again.block("door")
    assert (block.name, block.description, block.references) == ("DOOR", "door", 2)
    assert block.entity_counts == {"ATTDEF": 2, "CIRCLE": 1, "LINE": 1}
    number, material = block.attdefs
    assert (number.tag, number.default, number.prompt, number.constant) == (
        "NO",
        "D-00",
        "門號?",
        False,
    )
    assert (material.tag, material.default, material.constant) == ("MAT", "木", True)
    assert again.entity_counts() == {**counts_before, "INSERT": 2}
    assert again.metadata.extra["block_count"] == 1

    assert again.attributes(first) == {"NO": "大門"}
    ref = again.insert(second)
    assert (ref.name, ref.insert, ref.rotation) == ("DOOR", (200.0, 50.0, 0.0), 90.0)
    assert ref.scale[:2] == (2.0, 2.0) and ref.attributes == {"NO": "D-00"}
    assert "大門" in again.text()
    assert not ezdxf.readfile(out).audit().has_errors


def test_attribute_edit_rename_and_explode_roundtrip(tmp_path: Path) -> None:
    src = tmp_path / "in.dxf"
    ezdxf.new("R2010").saveas(src)
    doc = _open(src)
    first, second = _build(doc)
    mid = doc.save(tmp_path / "mid.dxf")

    doc = _open(mid)
    doc.set_attributes(first, {"no": "後門"})
    doc.modify_insert(first, insert=(0, 0))
    doc.rename_block("DOOR", "門")
    new = doc.explode(second)
    out = doc.save(tmp_path / "out.dxf")

    again = _open(out)
    assert [b.name for b in again.block_details()] == ["門"]
    (ref,) = again.inserts()
    assert (ref.handle, ref.name, ref.insert) == (first, "門", (0.0, 0.0, 0.0))
    assert ref.attributes == {"NO": "後門"}
    assert again.entity_counts() == {"CIRCLE": 1, "INSERT": 1, "LINE": 1, "TEXT": 1}
    line, circle, text = (again.entity(h) for h in new)
    assert line.geometry["end"] == pytest.approx((200.0, 70.0, 0.0))
    assert circle.geometry["center"] == pytest.approx((190.0, 60.0, 0.0))
    assert circle.geometry["radius"] == pytest.approx(4.0)
    assert text.geometry["text"] == "D-00"
    assert not ezdxf.readfile(out).audit().has_errors

    again.delete_block("門", delete_references=True)
    final = _open(again.save(tmp_path / "final.dxf"))
    assert final.blocks() == [] and final.inserts() == []
    assert not ezdxf.readfile(tmp_path / "final.dxf").audit().has_errors


def test_block_references_affect_rendering(tmp_path: Path) -> None:
    src = tmp_path / "in.dxf"
    ezdxf.new("R2010").saveas(src)
    doc = _open(src)
    first, second = _build(doc)
    out = doc.save(tmp_path / "out.dxf")

    again = _open(out)
    with_refs = _shapes(again.render("svg"))
    assert with_refs > 0
    again.delete_entities([second])
    fewer = _shapes(again.render("svg"))
    assert 0 < fewer < with_refs
    # Exploding keeps what is drawn (the attribute becomes a TEXT).
    again.explode(first)
    assert _shapes(again.render("svg")) == fewer
    assert again.render("png")[:4] == b"\x89PNG"


@pytest.mark.parametrize("version", ["R12", "R2000", "R2010", "R2018"])
def test_blocks_across_dxf_versions(version: str, tmp_path: Path) -> None:
    src = tmp_path / "in.dxf"
    ezdxf.new(version).saveas(src)
    doc = _open(src)
    first, second = _build(doc)
    doc.add_line((0, 0), (0, 10), block="DOOR")
    out = doc.save(tmp_path / "out.dxf")

    again = _open(out)
    assert again.release == version
    block = again.block("DOOR")
    assert block.entity_counts == {"ATTDEF": 2, "CIRCLE": 1, "LINE": 2}
    assert [a.default for a in block.attdefs] == ["D-00", "木"]
    assert block.references == 2
    assert again.attributes(first) == {"NO": "大門"}
    assert again.attributes(second) == {"NO": "D-00"}
    assert again.set_attributes(first, {"NO": "側門"}) == {"NO": "側門"}
    new = again.explode(second)
    assert sorted(again.entity(h).type for h in new) == ["CIRCLE", "LINE", "LINE", "TEXT"]
    final_path = again.save(tmp_path / "final.dxf")

    final = _open(final_path)
    assert final.attributes(first) == {"NO": "側門"}
    assert final.entity_counts() == {"CIRCLE": 1, "INSERT": 1, "LINE": 2, "TEXT": 1}
    assert "側門" in final.text()
    assert not ezdxf.readfile(final_path).audit().has_errors


def test_to_bytes_reflects_block_edits(sample: Callable[[str], Path]) -> None:
    doc = _open(sample("dxf"))
    first, _ = _build(doc)
    again = DxfHandler().open(doc.to_bytes())
    assert again.block("DOOR").references == 2
    assert again.attributes(first) == {"NO": "大門"}
