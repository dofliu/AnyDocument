"""Tests for DXF layer management (T081)."""

from __future__ import annotations

from typing import Any

import ezdxf
import pytest

from anydoc.core.model import Capability
from anydoc.formats.dxf import DxfDocument, DxfHandler, LayerInfo
from anydoc.formats.dxf import layers as L


@pytest.fixture
def doc() -> Any:
    """A drawing with layers 牆 (model, block, insert, attrib), 標註 (text) and 空 (unused)."""
    d = ezdxf.new("R2018")
    d.layers.add("牆", color=1)
    d.layers.add("標註", color=3)
    d.layers.add("空")
    blk = d.blocks.new("DOOR")
    blk.add_line((0, 0), (1, 0), dxfattribs={"layer": "牆"})
    blk.add_attdef("TAG", (0, 0), dxfattribs={"layer": "牆"})
    msp = d.modelspace()
    msp.add_line((0, 0), (10, 0), dxfattribs={"layer": "牆"})
    msp.add_circle((5, 5), 2, dxfattribs={"layer": "牆"})
    msp.add_text("尺寸說明", dxfattribs={"layer": "標註"})
    ins = msp.add_blockref("DOOR", (5, 5), dxfattribs={"layer": "牆"})
    ins.add_attrib("TAG", "門甲", (5, 5), dxfattribs={"layer": "牆"})
    d.layout("Layout1").add_line((0, 0), (1, 1), dxfattribs={"layer": "標註"})
    return d


def _names(d: Any) -> list[str]:
    return [info.name for info in L.list_layers(d)]


# -- colours -------------------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (1, (1, None)),
        (255, (255, None)),
        ("5", (5, None)),
        ("Red", (1, None)),
        (" cyan ", (4, None)),
        ("#FF8000", (None, (255, 128, 0))),
        ("#0a0B0c", (None, (10, 11, 12))),
    ],
)
def test_parse_color(value: int | str, expected: tuple[Any, Any]) -> None:
    assert L.parse_color(value) == expected


@pytest.mark.parametrize("value", [0, 256, -1, "0", "300", "紅", "FF8000", "#FFF", "", True])
def test_parse_color_invalid(value: Any) -> None:
    with pytest.raises(ValueError, match="colour"):
        L.parse_color(value)


# -- add -----------------------------------------------------------------------


def test_add_layer_defaults(doc: Any) -> None:
    info = L.add_layer(doc, "新圖層")
    assert info == LayerInfo("新圖層", 7, "Continuous", True, False, False, None)
    assert L.get_layer(doc, "新圖層") == info
    assert _names(doc)[-1] == "新圖層"


def test_add_layer_with_properties(doc: Any) -> None:
    info = L.add_layer(
        doc, "隱藏線", color="blue", linetype="dashed", on=False, frozen=True, locked=True
    )
    assert info == LayerInfo("隱藏線", 5, "DASHED", False, True, True, None)
    assert doc.linetypes.has_entry("DASHED")  # standard linetype loaded on demand


def test_add_layer_true_color(doc: Any) -> None:
    info = L.add_layer(doc, "彩色", color="#102030")
    assert info.true_color == "#102030"
    assert doc.layers.get("彩色").rgb == (16, 32, 48)


@pytest.mark.parametrize("name", ["", "   ", "a/b", "a*b", "a<b", "x" * 256])
def test_add_layer_invalid_name(doc: Any, name: str) -> None:
    with pytest.raises(ValueError, match="name"):
        L.add_layer(doc, name)


def test_add_layer_duplicate_is_case_insensitive(doc: Any) -> None:
    L.add_layer(doc, "Wall")
    with pytest.raises(ValueError, match="already exists"):
        L.add_layer(doc, "WALL")
    with pytest.raises(ValueError, match="already exists"):
        L.add_layer(doc, "牆")


def test_add_layer_bad_arguments_leave_drawing_unchanged(doc: Any) -> None:
    before = _names(doc)
    with pytest.raises(ValueError, match="Unknown linetype"):
        L.add_layer(doc, "X", linetype="不存在")
    with pytest.raises(ValueError, match="colour"):
        L.add_layer(doc, "X", color=999)
    assert _names(doc) == before


# -- set -----------------------------------------------------------------------


def test_set_layer_color_and_linetype(doc: Any) -> None:
    info = L.set_layer(doc, "牆", color=6, linetype="CENTER")
    assert (info.color, info.linetype) == (6, "CENTER")
    assert L.set_layer(doc, "牆", color="yellow").color == 2


def test_set_layer_true_color_then_aci_clears_it(doc: Any) -> None:
    assert L.set_layer(doc, "牆", color="#AABBCC").true_color == "#AABBCC"
    info = L.set_layer(doc, "牆", color=4)
    assert (info.color, info.true_color) == (4, None)


def test_set_layer_on_off(doc: Any) -> None:
    assert L.set_layer(doc, "牆", on=False).on is False
    assert doc.layers.get("牆").dxf.color < 0  # off state is the negative colour
    assert L.set_layer(doc, "牆", on=True).on is True


def test_set_color_keeps_layer_off(doc: Any) -> None:
    L.set_layer(doc, "牆", on=False)
    info = L.set_layer(doc, "牆", color=5)
    assert (info.color, info.on) == (5, False)


def test_set_layer_freeze_and_lock(doc: Any) -> None:
    info = L.set_layer(doc, "牆", frozen=True, locked=True)
    assert (info.frozen, info.locked, info.on) == (True, True, True)
    info = L.set_layer(doc, "牆", frozen=False)
    assert (info.frozen, info.locked) == (False, True)
    assert L.set_layer(doc, "牆", locked=False).locked is False


def test_set_layer_none_changes_nothing(doc: Any) -> None:
    before = L.get_layer(doc, "標註")
    assert L.set_layer(doc, "標註") == before


def test_set_layer_is_case_insensitive(doc: Any) -> None:
    L.add_layer(doc, "Wall")
    assert L.set_layer(doc, "wALL", color=2).name == "Wall"


def test_set_layer_missing(doc: Any) -> None:
    with pytest.raises(KeyError, match="No such layer"):
        L.set_layer(doc, "不存在", color=1)
    with pytest.raises(KeyError, match="No such layer"):
        L.get_layer(doc, "不存在")


def test_set_layer_validates_before_changing(doc: Any) -> None:
    before = L.get_layer(doc, "牆")
    with pytest.raises(ValueError, match="Unknown linetype"):
        L.set_layer(doc, "牆", color=5, linetype="不存在", on=False)
    with pytest.raises(ValueError, match="colour"):
        L.set_layer(doc, "牆", color="nope", locked=True)
    assert L.get_layer(doc, "牆") == before


# -- current layer -------------------------------------------------------------


def test_current_layer(doc: Any) -> None:
    assert L.current_layer(doc) == "0"
    assert L.set_current_layer(doc, "牆") == "牆"
    assert L.current_layer(doc) == "牆"
    with pytest.raises(KeyError):
        L.set_current_layer(doc, "不存在")


def test_current_layer_cannot_be_frozen(doc: Any) -> None:
    L.set_current_layer(doc, "牆")
    with pytest.raises(ValueError, match="current layer"):
        L.set_layer(doc, "牆", frozen=True)
    L.set_layer(doc, "標註", frozen=True)
    with pytest.raises(ValueError, match="frozen"):
        L.set_current_layer(doc, "標註")
    assert L.current_layer(doc) == "牆"


# -- usage ---------------------------------------------------------------------


def test_layer_usage_counts_layouts_blocks_and_attribs(doc: Any) -> None:
    usage = L.layer_usage(doc)
    # 牆: 2 block entities + line + circle + insert + attrib; 標註: text + paper-space line
    assert usage == {"0": 0, "Defpoints": 0, "牆": 6, "標註": 2, "空": 0}


def test_layer_usage_reports_undefined_layers(doc: Any) -> None:
    doc.modelspace().add_point((0, 0), dxfattribs={"layer": "未定義"})
    assert L.layer_usage(doc)["未定義"] == 1


# -- rename --------------------------------------------------------------------


def test_rename_layer_updates_all_references(doc: Any) -> None:
    L.set_current_layer(doc, "牆")
    info = L.rename_layer(doc, "牆", "WALL")
    assert info.name == "WALL" and info.color == 1
    assert "牆" not in _names(doc) and not doc.layers.has_entry("牆")
    assert L.layer_usage(doc)["WALL"] == 6
    assert L.current_layer(doc) == "WALL"
    ins = doc.modelspace().query("INSERT").first
    assert ins.dxf.layer == "WALL" and ins.attribs[0].dxf.layer == "WALL"
    assert {e.dxf.layer for e in doc.blocks.get("DOOR")} == {"WALL"}


def test_rename_layer_case_only(doc: Any) -> None:
    L.add_layer(doc, "wall")
    doc.modelspace().add_line((0, 0), (1, 1), dxfattribs={"layer": "wall"})
    assert L.rename_layer(doc, "wall", "WALL").name == "WALL"
    assert "WALL" in _names(doc) and "wall" not in _names(doc)
    assert L.layer_usage(doc)["WALL"] == 1


def test_rename_layer_same_name_is_noop(doc: Any) -> None:
    assert L.rename_layer(doc, "牆", "牆") == L.get_layer(doc, "牆")


def test_rename_layer_errors(doc: Any) -> None:
    with pytest.raises(ValueError, match="already exists"):
        L.rename_layer(doc, "牆", "標註")
    with pytest.raises(ValueError, match="Cannot rename"):
        L.rename_layer(doc, "0", "零")
    with pytest.raises(ValueError, match="Cannot rename"):
        L.rename_layer(doc, "defpoints", "點")
    with pytest.raises(ValueError, match="invalid characters"):
        L.rename_layer(doc, "牆", "a?b")
    with pytest.raises(KeyError):
        L.rename_layer(doc, "不存在", "X")
    assert L.layer_usage(doc)["牆"] == 6


# -- delete --------------------------------------------------------------------


def test_delete_unused_layer(doc: Any) -> None:
    assert L.delete_layer(doc, "空") == 0
    assert "空" not in _names(doc)


def test_delete_layer_in_use_is_refused(doc: Any) -> None:
    with pytest.raises(ValueError, match="in use by 6 entities"):
        L.delete_layer(doc, "牆")
    assert "牆" in _names(doc) and L.layer_usage(doc)["牆"] == 6


def test_delete_layer_move_entities(doc: Any) -> None:
    total = sum(L.layer_usage(doc).values())
    assert L.delete_layer(doc, "牆", move_to="標註") == 6
    usage = L.layer_usage(doc)
    assert "牆" not in usage and usage["標註"] == 8
    assert sum(usage.values()) == total


def test_delete_layer_with_entities(doc: Any) -> None:
    assert L.delete_layer(doc, "牆", delete_entities=True) == 6
    assert "牆" not in L.layer_usage(doc)
    assert [e.dxftype() for e in doc.modelspace()] == ["TEXT"]
    assert len(doc.blocks.get("DOOR")) == 0
    assert L.layer_usage(doc)["標註"] == 2


def test_delete_layer_only_attrib_on_layer(doc: Any) -> None:
    ins = doc.modelspace().query("INSERT").first
    L.add_layer(doc, "屬性")
    ins.attribs[0].dxf.layer = "屬性"
    assert L.delete_layer(doc, "屬性", delete_entities=True) == 1
    assert ins.is_alive and len(ins.attribs) == 0


def test_delete_layer_errors(doc: Any) -> None:
    with pytest.raises(ValueError, match="Cannot delete layer"):
        L.delete_layer(doc, "0")
    with pytest.raises(ValueError, match="Cannot delete layer"):
        L.delete_layer(doc, "Defpoints")
    with pytest.raises(KeyError):
        L.delete_layer(doc, "不存在")
    with pytest.raises(KeyError):
        L.delete_layer(doc, "牆", move_to="不存在")
    with pytest.raises(ValueError, match="being deleted"):
        L.delete_layer(doc, "牆", move_to="牆")
    with pytest.raises(ValueError, match="not both"):
        L.delete_layer(doc, "牆", move_to="標註", delete_entities=True)
    L.set_current_layer(doc, "空")
    with pytest.raises(ValueError, match="current layer"):
        L.delete_layer(doc, "空")
    assert {"牆", "空"} <= set(_names(doc))


def test_purge_layers(doc: Any) -> None:
    L.add_layer(doc, "目前")
    L.set_current_layer(doc, "目前")
    assert L.purge_layers(doc) == ["空"]
    assert _names(doc) == ["0", "Defpoints", "牆", "標註", "目前"]
    assert L.purge_layers(doc) == []


# -- DxfDocument ---------------------------------------------------------------


def test_handler_capabilities() -> None:
    assert {Capability.EDIT, Capability.WRITE} <= DxfHandler.capabilities


def test_document_methods_and_metadata(doc: Any) -> None:
    d = DxfDocument(doc)
    assert d.metadata.extra["layer_count"] == 5
    assert d.add_layer("新", color="green").color == 3
    assert d.metadata.extra["layer_count"] == 6
    assert d.set_layer("新", locked=True).locked is True
    assert d.rename_layer("新", "新版").name == "新版"
    assert d.layer("新版").locked is True
    assert d.layer_usage()["牆"] == 6
    assert d.current_layer == "0"
    assert d.set_current_layer("標註") == "標註" and d.current_layer == "標註"
    assert d.delete_layer("新版") == 0
    assert d.purge_layers() == ["空"]
    assert d.metadata.extra["layer_count"] == 4
    assert d.delete_layer("牆", delete_entities=True) == 6
    assert d.metadata.extra["entity_counts"] == {"LINE": 1, "TEXT": 1}


def test_to_bytes_reopens(doc: Any) -> None:
    d = DxfDocument(doc)
    d.add_layer("輸出", color="#123456", linetype="DASHDOT", locked=True)
    again = DxfHandler().open(d.to_bytes())
    assert again.layer("輸出") == LayerInfo("輸出", 7, "DASHDOT", True, False, True, "#123456")
    assert again.text() == d.text()
