r"""Round-trip tests for Chinese names in non-Unicode code page drawings (T086).

DXF R12/R2000 files written in ``ANSI_1252`` store CJK names as ``\U+XXXX``
escapes; after reopening, every API has to work with the original names.
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

import ezdxf
import pytest
from typer.testing import CliRunner

from anydoc.cli import app
from anydoc.formats.dxf import DxfDocument, DxfHandler

runner = CliRunner()

LEGACY = ["R12", "R2000"]
WALL = "牆面"
DOOR = "門塊"


def _blank(path: Path, version: str) -> Path:
    ezdxf.new(version).saveas(path)
    return path


def _build(tmp_path: Path, version: str) -> tuple[Path, dict[str, str]]:
    """Create a drawing through the editing API, save it and return its handles."""
    doc = DxfHandler().open(_blank(tmp_path / "blank.dxf", version))
    doc.add_layer(WALL, color="red", linetype="CENTER")
    doc.add_layer("標註", color="blue")
    handles = {
        "line": doc.add_line((0, 0), (30, 40), layer=WALL),
        "circle": doc.add_circle((100, 0), 10, layer=WALL),
        "other": doc.add_line((0, 0), (1, 0)),
        "text": doc.add_text("客廳", (0, 50), height=5, layer="標註"),
    }
    doc.define_block(DOOR)
    doc.add_line((0, 0), (9, 0), block=DOOR, layer=WALL)
    doc.add_attdef(DOOR, "編號", (0, 1), default="D1")
    handles["ref"] = doc.insert_block(DOOR, (200, 0), layer=WALL, attributes={"編號": "D7"})
    saved = doc.save(tmp_path / f"legacy_{version}.dxf")
    return saved, handles


def _reopen(path: Path) -> DxfDocument:
    doc = DxfHandler().open(path)
    assert isinstance(doc, DxfDocument)
    return doc


@pytest.mark.parametrize("version", LEGACY)
def test_file_stores_escapes_but_api_returns_names(tmp_path: Path, version: str) -> None:
    saved, handles = _build(tmp_path, version)
    raw = saved.read_bytes()
    assert rb"\U+7246\U+9762" in raw and WALL.encode("utf-8") not in raw

    doc = _reopen(saved)
    names = [layer.name for layer in doc.layers()]
    assert WALL in names and "標註" in names
    assert not any("\\U+" in name for name in names)
    assert doc.layer(WALL).color == 1
    assert doc.layer(WALL).linetype == "CENTER"
    # entity layer attributes
    by_handle = {e.handle: e for e in doc.entities()}
    assert by_handle[handles["line"]].layer == WALL
    assert by_handle[handles["text"]].layer == "標註"
    assert by_handle[handles["text"]].text == "客廳"
    assert doc.entity(handles["circle"]).layer == WALL
    # blocks, references and attributes
    assert [b.name for b in doc.blocks()] == [DOOR]
    detail = doc.block(DOOR)
    assert [a.tag for a in detail.attdefs] == ["編號"]
    ref = doc.insert(handles["ref"])
    assert (ref.name, ref.layer, ref.attributes) == (DOOR, WALL, {"編號": "D7"})
    assert [r.handle for r in doc.inserts(name=DOOR)] == [handles["ref"]]
    assert doc.native.audit().errors == []


@pytest.mark.parametrize("version", LEGACY)
def test_entity_filters_use_original_names(tmp_path: Path, version: str) -> None:
    saved, handles = _build(tmp_path, version)
    doc = _reopen(saved)
    on_wall = {handles["line"], handles["circle"], handles["ref"]}
    assert set(doc.select(layer=WALL)) == on_wall
    assert set(doc.select(layer=WALL, types="LINE")) == {handles["line"]}
    assert {e.handle for e in doc.entities(layer=WALL)} == on_wall
    assert doc.select(layer="標註") == [handles["text"]]
    assert doc.layer_usage()[WALL] == 4  # three in model space + one inside the block
    assert doc.select(layer=r"\U+7246\U+9762") == []
    assert [d.layer for d in doc.entity_details(block=DOOR) if d.type == "LINE"] == [WALL]


@pytest.mark.parametrize("version", LEGACY)
def test_measure_filters_use_original_names(tmp_path: Path, version: str) -> None:
    saved, handles = _build(tmp_path, version)
    doc = _reopen(saved)
    box = doc.bounding_box(layer=WALL)
    assert box is not None
    assert box.min == pytest.approx((0, -10, 0)) and box.max == pytest.approx((209, 40, 0))
    results = doc.measure(doc.select(layer=WALL, types=["LINE", "CIRCLE"]))
    assert sorted(m.length for m in results if m.length) == pytest.approx([50.0, 20 * math.pi])
    hits = doc.entities_at((15, 20), tolerance=0.5, layer=WALL)
    assert [hit.handle for hit in hits] == [handles["line"]]
    assert doc.entities_at((15, 20), tolerance=0.5, layer="標註") == []
    inside = doc.entities_in_window((-1, -11), (111, 41), layer=WALL)
    assert set(inside) == {handles["line"], handles["circle"]}


@pytest.mark.parametrize("version", LEGACY)
def test_layer_management_on_reopened_drawing(tmp_path: Path, version: str) -> None:
    """Rename, restyle, add and delete Chinese-named layers across two more round trips."""
    saved, handles = _build(tmp_path, version)
    doc = _reopen(saved)
    doc.set_layer(WALL, color="green", locked=False)
    doc.rename_layer(WALL, "外牆")
    doc.set_current_layer("外牆")
    with pytest.raises(ValueError, match="exists"):
        doc.add_layer("外牆")
    doc.add_layer("家具")
    doc.delete_layer("標註", move_to="家具")
    second = doc.save(tmp_path / "second.dxf")

    again = _reopen(second)
    names = [layer.name for layer in again.layers()]
    assert "外牆" in names and "家具" in names and WALL not in names and "標註" not in names
    assert again.current_layer == "外牆"
    assert again.layer("外牆").color == 3
    assert set(again.select(layer="外牆")) == {handles["line"], handles["circle"], handles["ref"]}
    assert again.select(layer="家具") == [handles["text"]]
    assert again.purge_layers() == []  # every remaining layer is in use or protected
    again.set_current_layer("0")
    again.delete_layer("外牆", delete_entities=True)
    third = again.save(tmp_path / "third.dxf")

    final = _reopen(third)
    assert "外牆" not in [layer.name for layer in final.layers()]
    assert set(final.select()) == {handles["other"], handles["text"]}
    assert final.native.audit().errors == []


@pytest.mark.parametrize("version", LEGACY)
def test_block_management_on_reopened_drawing(tmp_path: Path, version: str) -> None:
    saved, handles = _build(tmp_path, version)
    doc = _reopen(saved)
    doc.set_attributes(handles["ref"], {"編號": "玄關"})
    doc.rename_block(DOOR, "大門")
    extra = doc.insert_block("大門", (300, 0))
    second = doc.save(tmp_path / "blocks.dxf")

    again = _reopen(second)
    assert [b.name for b in again.blocks()] == ["大門"]
    assert {r.handle for r in again.inserts(name="大門")} == {handles["ref"], extra}
    assert again.attributes(handles["ref"]) == {"編號": "玄關"}
    assert again.attributes(extra) == {"編號": "D1"}
    created = again.explode(extra)
    assert created and all(again.entity(h).layer in {WALL, "0"} for h in created)
    assert again.native.audit().errors == []


@pytest.mark.parametrize("version", LEGACY)
def test_editing_is_stable_over_repeated_round_trips(tmp_path: Path, version: str) -> None:
    """Saving a reopened drawing writes the same escapes again (no double escaping)."""
    saved, _ = _build(tmp_path, version)
    first = _reopen(saved)
    path2 = first.save(tmp_path / "again.dxf")
    second = _reopen(path2)
    assert [layer.name for layer in second.layers()] == [layer.name for layer in first.layers()]
    assert second.to_bytes().count(rb"\U+7246\U+9762") == first.to_bytes().count(rb"\U+7246\U+9762")
    assert rb"\\U+" not in path2.read_bytes()
    assert second.metadata.extra == first.metadata.extra


@pytest.mark.parametrize("version", LEGACY)
def test_render_after_reopen_respects_layer_state(tmp_path: Path, version: str) -> None:
    saved, _ = _build(tmp_path, version)
    doc = _reopen(saved)
    before = doc.render("svg")
    doc.set_layer(WALL, on=False)
    doc.set_layer("標註", on=False)
    after = doc.render("svg")
    assert len(after) < len(before)


@pytest.mark.parametrize("version", LEGACY)
def test_cli_shows_and_accepts_original_names(tmp_path: Path, version: str) -> None:
    saved, handles = _build(tmp_path, version)
    result = runner.invoke(app, ["dxf", "layers", str(saved)])
    assert result.exit_code == 0, result.output
    layers: list[dict[str, Any]] = json.loads(result.output)
    wall = next(item for item in layers if item["name"] == WALL)
    assert wall["entities"] == 4

    result = runner.invoke(app, ["dxf", "entities", str(saved), "--layer", WALL, "--type", "LINE"])
    assert result.exit_code == 0, result.output
    assert [item["handle"] for item in json.loads(result.output)] == [handles["line"]]

    result = runner.invoke(app, ["dxf", "bbox", str(saved), "--layer", WALL])
    assert result.exit_code == 0, result.output

    out = tmp_path / "renamed.dxf"
    result = runner.invoke(app, ["dxf", "layer-rename", str(saved), WALL, "外牆", "-o", str(out)])
    assert result.exit_code == 0, result.output
    assert "外牆" in [layer.name for layer in _reopen(out).layers()]
