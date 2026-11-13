"""Tests for DXF blocks: define, insert, attributes and explode (T084)."""

from __future__ import annotations

from typing import Any

import ezdxf
import pytest

from anydoc.formats.dxf import AttDef, BlockDetail, BlockRef, DxfDocument
from anydoc.formats.dxf import blocks as B
from anydoc.formats.dxf import entities as E


@pytest.fixture
def doc() -> Any:
    """An R2018 drawing with block 門 (line, circle, attributes 編號 and constant 材質)."""
    d = ezdxf.new("R2018")
    d.layers.add("牆", color=1)
    d.layers.add("鎖").lock()
    block = d.blocks.new("門", base_point=(0, 0))
    block.add_line((0, 0), (10, 0))
    block.add_circle((5, 5), 2)
    block.add_attdef("編號", (0, -3), "D-00", dxfattribs={"height": 2, "prompt": "門號?"})
    const = block.add_attdef("材質", (0, -6), "木", dxfattribs={"height": 2})
    const.is_const = True
    return d


def _approx(value: Any) -> Any:
    return pytest.approx(value, abs=1e-9)


def _types(d: Any, handles: list[str]) -> list[str]:
    return [E.get_entity(d, h).type for h in handles]


# -- listing ---------------------------------------------------------------------


def test_list_blocks_excludes_layout_and_anonymous_blocks(doc: Any) -> None:
    doc.blocks.new_anonymous_block()
    blocks = B.list_blocks(doc)
    assert [b.name for b in blocks] == ["門"]
    block = blocks[0]
    assert isinstance(block, BlockDetail)
    assert block.base_point == (0.0, 0.0, 0.0)
    assert block.entity_count == 4
    assert block.entity_counts == {"ATTDEF": 2, "CIRCLE": 1, "LINE": 1}
    assert block.references == 0


def test_get_block_attdefs(doc: Any) -> None:
    block = B.get_block(doc, "門")
    number, material = block.attdefs
    assert isinstance(number, AttDef)
    assert (number.tag, number.default, number.prompt) == ("編號", "D-00", "門號?")
    assert number.insert == (0.0, -3.0, 0.0) and number.height == 2.0
    assert not number.constant and not number.invisible
    assert material.constant and material.default == "木"
    data = block.to_dict()
    assert data["name"] == "門" and data["attdefs"][0]["tag"] == "編號"
    assert data["base_point"] == [0.0, 0.0, 0.0]


def test_get_block_is_case_insensitive_and_missing_raises(doc: Any) -> None:
    doc.blocks.new("Door")
    assert B.get_block(doc, "DOOR").name == "Door"
    for name in ("沒有", "*Model_Space", ""):
        with pytest.raises(KeyError, match="No such block"):
            B.get_block(doc, name)


# -- define ----------------------------------------------------------------------


def test_define_empty_block_and_fill_it(doc: Any) -> None:
    block = B.define_block(doc, "窗", base_point=(1, 2), description="推拉窗")
    assert (block.name, block.base_point, block.entity_count) == ("窗", (1.0, 2.0, 0.0), 0)
    assert block.description == "推拉窗"
    line = E.add_line(doc, (0, 0), (20, 0), block="窗", layer="牆")
    circle = E.add_circle(doc, (10, 0), 3, block="窗")
    assert E.get_entity(doc, line).layout == "窗"
    assert E.select(doc, block="窗") == [line, circle]
    assert [e.type for e in E.list_entities(doc, block="窗", types="CIRCLE")] == ["CIRCLE"]
    assert B.get_block(doc, "窗").entity_counts == {"CIRCLE": 1, "LINE": 1}
    assert len(doc.modelspace()) == 0


def test_add_entity_to_missing_block_raises(doc: Any) -> None:
    for name in ("沒有", "*Model_Space"):
        with pytest.raises(KeyError, match="No such block"):
            E.add_line(doc, (0, 0), (1, 1), block=name)


def test_define_block_moves_entities(doc: Any) -> None:
    line = E.add_line(doc, (0, 0), (10, 0), layer="牆")
    circle = E.add_circle(doc, (5, 5), 2)
    other = E.add_line(doc, (0, 0), (0, 9))
    block = B.define_block(doc, "組合", base_point=(5, 0), handles=[line, circle])
    assert block.entity_counts == {"CIRCLE": 1, "LINE": 1} and block.references == 0
    assert E.select(doc) == [other]
    # The entities keep their handle, coordinates and layer inside the block.
    moved = E.get_entity(doc, line)
    assert (moved.layout, moved.layer) == ("組合", "牆")
    assert moved.geometry["end"] == (10.0, 0.0, 0.0)


def test_define_block_replace_inserts_reference(doc: Any) -> None:
    line = E.add_line(doc, (0, 0), (10, 0))
    block = B.define_block(doc, "線", base_point=(3, 4), handles=line, replace=True)
    assert block.references == 1
    (ref,) = B.list_inserts(doc)
    assert (ref.name, ref.insert, ref.scale, ref.rotation) == (
        "線",
        (3.0, 4.0, 0.0),
        (1.0, 1.0, 1.0),
        0.0,
    )
    # Exploding the reference restores the original geometry.
    (restored,) = B.explode(doc, ref.handle)
    assert E.get_entity(doc, restored).geometry == {
        "start": (0.0, 0.0, 0.0),
        "end": (10.0, 0.0, 0.0),
    }


def test_define_block_keep_copies_entities(doc: Any) -> None:
    line = E.add_line(doc, (0, 0), (10, 0), layer="鎖")
    block = B.define_block(doc, "副本", handles=[line], keep=True)
    assert block.entity_count == 1
    assert E.select(doc) == [line]
    assert E.select(doc, block="副本") != [line]


@pytest.mark.parametrize(
    ("name", "message"),
    [
        ("", "must not be empty"),
        ("   ", "must not be empty"),
        ("*U1", "reserved"),
        ("a<b", "invalid characters"),
        (" 門2", "invalid characters"),
        ("x" * 256, "at most 255"),
        ("門", "already exists"),
    ],
)
def test_define_block_invalid_name(doc: Any, name: str, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        B.define_block(doc, name)
    assert [b.name for b in B.list_blocks(doc)] == ["門"]


def test_define_block_argument_errors(doc: Any) -> None:
    line = E.add_line(doc, (0, 0), (1, 1))
    locked = E.add_line(doc, (0, 0), (1, 1), layer="鎖")
    paper = E.add_line(doc, (0, 0), (1, 1), layout="Layout1")
    cases: list[tuple[dict[str, Any], type[Exception], str]] = [
        ({"handles": [line], "keep": True, "replace": True}, ValueError, "either keep or replace"),
        ({"replace": True}, ValueError, "needs the handles"),
        ({"handles": []}, ValueError, "No entities given"),
        ({"handles": ["FFFF"]}, KeyError, "No such entity"),
        ({"handles": [locked]}, ValueError, "locked layer"),
        ({"handles": [line, paper]}, ValueError, "single layout"),
        ({"base_point": (1,)}, ValueError, "Base point"),
        ({"description": "a\nb"}, ValueError, "single line"),
    ]
    for options, error, message in cases:
        with pytest.raises(error, match=message):
            B.define_block(doc, "新", **options)
        assert [b.name for b in B.list_blocks(doc)] == ["門"], options
    assert E.select(doc) == [line, locked]


def test_block_names_with_spaces_need_r2000() -> None:
    d = ezdxf.new("R12")
    with pytest.raises(ValueError, match="R2000"):
        B.define_block(d, "my block")
    assert B.define_block(ezdxf.new("R2000"), "my block").name == "my block"


# -- attribute definitions ---------------------------------------------------------


def test_add_attdef(doc: Any) -> None:
    handle = B.add_attdef(
        doc,
        "門",
        "寬度",
        (1, 2),
        default="90",
        prompt="寬度?",
        height=3,
        rotation=15,
        invisible=True,
        layer="牆",
        color="blue",
    )
    attdef = B.get_block(doc, "門").attdefs[-1]
    assert attdef == AttDef(
        tag="寬度",
        default="90",
        prompt="寬度?",
        insert=(1.0, 2.0, 0.0),
        height=3.0,
        rotation=15.0,
        invisible=True,
        constant=False,
        layer="牆",
        handle=handle,
    )
    assert doc.entitydb[handle].dxf.color == 5
    assert attdef.to_dict()["insert"] == [1.0, 2.0, 0.0]


def test_add_attdef_defaults_to_layer_0_and_constant(doc: Any) -> None:
    doc.header["$CLAYER"] = "牆"
    B.add_attdef(doc, "門", "K", (0, 0), default="v", constant=True)
    attdef = B.get_block(doc, "門").attdefs[-1]
    assert attdef.layer == "0" and attdef.constant and not attdef.invisible


@pytest.mark.parametrize(
    ("options", "error", "message"),
    [
        ({"tag": ""}, ValueError, "tag must be non-empty"),
        ({"tag": "A B"}, ValueError, "without whitespace"),
        ({"tag": "編號"}, ValueError, "already has an attribute"),
        ({"block": "沒有"}, KeyError, "No such block"),
        ({"height": 0}, ValueError, "Text height"),
        ({"default": "a\nb"}, ValueError, "single line"),
        ({"prompt": 3}, ValueError, "must be a string"),
        ({"insert": "1,2"}, ValueError, "Insert point"),
        ({"layer": "沒有"}, KeyError, "No such layer"),
    ],
)
def test_add_attdef_errors(
    doc: Any, options: dict[str, Any], error: type[Exception], message: str
) -> None:
    args: dict[str, Any] = {"block": "門", "tag": "新", "insert": (0, 0)} | options
    block, tag, insert = args.pop("block"), args.pop("tag"), args.pop("insert")
    with pytest.raises(error, match=message):
        B.add_attdef(doc, block, tag, insert, **args)
    assert len(B.get_block(doc, "門").attdefs) == 2


# -- insert ------------------------------------------------------------------------


def test_insert_block_with_defaults(doc: Any) -> None:
    doc.header["$CLAYER"] = "牆"
    handle = B.insert_block(doc, "門", (100, 50))
    ref = B.get_insert(doc, handle)
    assert ref == BlockRef(
        handle=handle,
        name="門",
        layout="Model",
        layer="牆",
        insert=(100.0, 50.0, 0.0),
        scale=(1.0, 1.0, 1.0),
        rotation=0.0,
        attributes={"編號": "D-00"},  # default value; the constant attribute gets no ATTRIB
    )
    (attrib,) = doc.entitydb[handle].attribs
    assert tuple(attrib.dxf.insert) == _approx((100.0, 47.0, 0.0))
    assert attrib.dxf.height == 2.0 and attrib.dxf.layer == "0"
    assert B.get_block(doc, "門").references == 1
    assert ref.to_dict()["attributes"] == {"編號": "D-00"}


def test_insert_block_scale_rotation_attributes(doc: Any) -> None:
    handle = B.insert_block(
        doc,
        "門",
        (10, 10),
        scale=2,
        rotation=90,
        attributes={"編號": "D-01"},
        layer="牆",
        color="#102030",
    )
    ref = B.get_insert(doc, handle)
    assert (ref.scale, ref.rotation, ref.layer) == ((2.0, 2.0, 2.0), 90.0, "牆")
    assert ref.attributes == {"編號": "D-01"}
    entity = doc.entitydb[handle]
    assert entity.rgb == (16, 32, 48)
    (attrib,) = entity.attribs
    # (0, -3) scaled by 2 and rotated by 90 degrees around the insertion point.
    assert tuple(attrib.dxf.insert) == _approx((16.0, 10.0, 0.0))
    assert attrib.dxf.height == _approx(4.0) and attrib.dxf.rotation == _approx(90.0)


def test_insert_block_non_uniform_scale_and_mirror(doc: Any) -> None:
    ref = B.get_insert(doc, B.insert_block(doc, "門", (0, 0), scale=(-1, 2)))
    assert ref.scale == (-1.0, 2.0, 1.0)
    ref = B.get_insert(doc, B.insert_block(doc, "門", (0, 0), scale=(1, 2, 3)))
    assert ref.scale == (1.0, 2.0, 3.0)


def test_insert_block_attribute_tags_are_case_insensitive(doc: Any) -> None:
    B.add_attdef(doc, "門", "Width", (0, 3))
    handle = B.insert_block(doc, "門", (0, 0), attributes={"WIDTH": "90"})
    assert B.get_attributes(doc, handle) == {"編號": "D-00", "Width": "90"}


def test_insert_into_layout_and_block(doc: Any) -> None:
    paper = B.insert_block(doc, "門", (1, 1), layout="Layout1")
    assert B.get_insert(doc, paper).layout == "Layout1"
    assert [r.handle for r in B.list_inserts(doc, layout="Layout1")] == [paper]
    B.define_block(doc, "房間")
    nested = B.insert_block(doc, "門", (5, 5), block="房間")
    assert B.get_insert(doc, nested).layout == "房間"
    assert [r.handle for r in B.list_inserts(doc, block="房間")] == [nested]
    assert B.get_block(doc, "門").references == 2
    assert B.list_inserts(doc) == []


def test_insert_block_rejects_cycles(doc: Any) -> None:
    B.define_block(doc, "房間")
    B.insert_block(doc, "門", (0, 0), block="房間")
    with pytest.raises(ValueError, match="cannot contain a reference to itself"):
        B.insert_block(doc, "門", (0, 0), block="門")
    with pytest.raises(ValueError, match="cannot contain a reference to itself"):
        B.insert_block(doc, "房間", (0, 0), block="門")
    assert B.get_block(doc, "房間").references == 0


@pytest.mark.parametrize(
    ("options", "error", "message"),
    [
        ({"name": "沒有"}, KeyError, "No such block"),
        ({"insert": (1,)}, ValueError, "Insert point"),
        ({"scale": 0}, ValueError, "must not be 0"),
        ({"scale": (1, 0)}, ValueError, "must not be 0"),
        ({"scale": (1,)}, ValueError, "Scale must be"),
        ({"scale": "2"}, ValueError, "Scale must be"),
        ({"scale": float("nan")}, ValueError, "finite"),
        ({"rotation": "x"}, ValueError, "Rotation"),
        ({"attributes": {"沒有": "x"}}, KeyError, "no attribute tagged"),
        ({"attributes": {"材質": "鐵"}}, ValueError, "is constant"),
        ({"attributes": {"編號": 5}}, ValueError, "must be a string"),
        ({"attributes": {"編號": "a\nb"}}, ValueError, "single line"),
        ({"layout": "沒有"}, KeyError, "No such layout"),
        ({"block": "沒有"}, KeyError, "No such block"),
        ({"layer": "沒有"}, KeyError, "No such layer"),
        ({"color": 999}, ValueError, "olour"),
    ],
)
def test_insert_block_errors(
    doc: Any, options: dict[str, Any], error: type[Exception], message: str
) -> None:
    args: dict[str, Any] = {"name": "門", "insert": (0, 0)} | options
    name, insert = args.pop("name"), args.pop("insert")
    with pytest.raises(error, match=message):
        B.insert_block(doc, name, insert, **args)
    assert B.list_inserts(doc) == []


def test_list_inserts_filters_by_block_name(doc: Any) -> None:
    B.define_block(doc, "窗")
    door = B.insert_block(doc, "門", (0, 0))
    window = B.insert_block(doc, "窗", (5, 0))
    assert [r.handle for r in B.list_inserts(doc)] == [door, window]
    assert [r.handle for r in B.list_inserts(doc, name="窗")] == [window]
    with pytest.raises(KeyError, match="No such block"):
        B.list_inserts(doc, name="沒有")


def test_get_insert_rejects_other_entities(doc: Any) -> None:
    line = E.add_line(doc, (0, 0), (1, 1))
    with pytest.raises(ValueError, match="is a LINE, not an INSERT"):
        B.get_insert(doc, line)
    with pytest.raises(KeyError, match="No such entity"):
        B.get_insert(doc, "FFFF")


# -- attributes --------------------------------------------------------------------


def test_set_attributes(doc: Any) -> None:
    B.add_attdef(doc, "門", "Width", (0, 3), default="80")
    handle = B.insert_block(doc, "門", (0, 0))
    assert B.set_attributes(doc, handle, {"width": "100"}) == {"編號": "D-00", "Width": "100"}
    assert B.set_attributes(doc, handle, {"編號": "大門", "WIDTH": ""}) == {
        "編號": "大門",
        "Width": "",
    }
    assert B.get_attributes(doc, handle) == {"編號": "大門", "Width": ""}
    # Other references and the definition keep their values.
    assert B.get_attributes(doc, B.insert_block(doc, "門", (9, 9)))["編號"] == "D-00"
    assert B.get_block(doc, "門").attdefs[0].default == "D-00"


def test_set_attributes_validates_before_changing(doc: Any) -> None:
    handle = B.insert_block(doc, "門", (0, 0))
    with pytest.raises(KeyError, match="no attribute tagged 沒有"):
        B.set_attributes(doc, handle, {"編號": "X", "沒有": "Y"})
    with pytest.raises(ValueError, match="single line"):
        B.set_attributes(doc, handle, {"編號": "a\nb"})
    with pytest.raises(ValueError, match="must be a string"):
        B.set_attributes(doc, handle, {"編號": 1})  # type: ignore[dict-item]
    assert B.get_attributes(doc, handle) == {"編號": "D-00"}


def test_attributes_on_locked_layer(doc: Any) -> None:
    handle = B.insert_block(doc, "門", (0, 0), layer="鎖")
    assert B.get_attributes(doc, handle) == {"編號": "D-00"}
    with pytest.raises(ValueError, match="locked layer"):
        B.set_attributes(doc, handle, {"編號": "X"})
    with pytest.raises(ValueError, match="locked layer"):
        B.modify_insert(doc, handle, insert=(1, 1))
    with pytest.raises(ValueError, match="locked layer"):
        B.explode(doc, handle)
    assert B.get_attributes(doc, handle) == {"編號": "D-00"}


def test_attributes_of_non_insert(doc: Any) -> None:
    line = E.add_line(doc, (0, 0), (1, 1))
    with pytest.raises(ValueError, match="not an INSERT"):
        B.get_attributes(doc, line)
    with pytest.raises(ValueError, match="not an INSERT"):
        B.set_attributes(doc, line, {"a": "b"})


def test_attribute_escapes_are_decoded(doc: Any) -> None:
    handle = B.insert_block(doc, "門", (0, 0))
    doc.entitydb[handle].attribs[0].dxf.text = r"\U+9580"
    assert B.get_attributes(doc, handle) == {"編號": "門"}


# -- modify insert -----------------------------------------------------------------


def test_modify_insert_moves_attributes(doc: Any) -> None:
    handle = B.insert_block(doc, "門", (10, 10))
    ref = B.modify_insert(doc, handle, insert=(20, 30))
    assert ref.insert == (20.0, 30.0, 0.0) and ref.scale == (1.0, 1.0, 1.0)
    (attrib,) = doc.entitydb[handle].attribs
    assert tuple(attrib.dxf.insert) == _approx((20.0, 27.0, 0.0))


def test_modify_insert_scale_and_rotation_match_fresh_insert(doc: Any) -> None:
    handle = B.insert_block(doc, "門", (10, 10), scale=2, rotation=30)
    ref = B.modify_insert(doc, handle, insert=(5, 5), scale=(3, 3), rotation=90)
    assert (ref.insert, ref.scale, ref.rotation) == ((5.0, 5.0, 0.0), (3.0, 3.0, 1.0), 90.0)
    fresh = B.insert_block(doc, "門", (5, 5), scale=(3, 3), rotation=90)
    (changed,), (expected,) = doc.entitydb[handle].attribs, doc.entitydb[fresh].attribs
    assert tuple(changed.dxf.insert) == _approx(tuple(expected.dxf.insert))
    assert changed.dxf.height == _approx(expected.dxf.height)
    assert changed.dxf.rotation % 360 == _approx(expected.dxf.rotation % 360)


def test_modify_insert_without_changes_is_a_no_op(doc: Any) -> None:
    handle = B.insert_block(doc, "門", (10, 10), scale=2, rotation=45)
    before = tuple(doc.entitydb[handle].attribs[0].dxf.insert)
    assert B.modify_insert(doc, handle) == B.get_insert(doc, handle)
    assert tuple(doc.entitydb[handle].attribs[0].dxf.insert) == _approx(before)


def test_modify_insert_errors(doc: Any) -> None:
    handle = B.insert_block(doc, "門", (10, 10))
    before = B.get_insert(doc, handle)
    with pytest.raises(ValueError, match="must not be 0"):
        B.modify_insert(doc, handle, insert=(1, 1), scale=0)
    with pytest.raises(ValueError, match="Rotation"):
        B.modify_insert(doc, handle, rotation=float("inf"))
    with pytest.raises(ValueError, match="not an INSERT"):
        B.modify_insert(doc, E.add_line(doc, (0, 0), (1, 1)), insert=(0, 0))
    assert B.get_insert(doc, handle) == before
    doc.entitydb[handle].dxf.extrusion = (0, 0, -1)
    with pytest.raises(ValueError, match="XY plane"):
        B.modify_insert(doc, handle, rotation=10)


# -- rename / delete / purge ---------------------------------------------------------


def test_rename_block_updates_references(doc: Any) -> None:
    B.define_block(doc, "房間")
    top = B.insert_block(doc, "門", (0, 0))
    nested = B.insert_block(doc, "門", (5, 5), block="房間")
    block = B.rename_block(doc, "門", "大門")
    assert (block.name, block.references) == ("大門", 2)
    assert {b.name for b in B.list_blocks(doc)} == {"大門", "房間"}
    assert B.get_insert(doc, top).name == "大門" and B.get_insert(doc, nested).name == "大門"
    assert B.get_attributes(doc, top) == {"編號": "D-00"}
    with pytest.raises(KeyError):
        B.get_block(doc, "門")
    assert not doc.audit().has_errors


def test_rename_block_case_only_and_same_name(doc: Any) -> None:
    B.define_block(doc, "door")
    handle = B.insert_block(doc, "door", (0, 0))
    assert B.rename_block(doc, "door", "door").name == "door"
    assert B.rename_block(doc, "DOOR", "Door").name == "Door"
    assert B.get_insert(doc, handle).name == "Door"
    assert [b.name for b in B.list_blocks(doc)].count("Door") == 1


def test_rename_block_errors(doc: Any) -> None:
    B.define_block(doc, "窗")
    for new, message in (("窗", "already exists"), ("", "must not be empty"), ("*x", "reserved")):
        with pytest.raises(ValueError, match=message):
            B.rename_block(doc, "門", new)
    with pytest.raises(KeyError, match="No such block"):
        B.rename_block(doc, "沒有", "新")
    assert {b.name for b in B.list_blocks(doc)} == {"門", "窗"}


def test_delete_block(doc: Any) -> None:
    assert B.delete_block(doc, "門") == 0
    assert B.list_blocks(doc) == []
    with pytest.raises(KeyError, match="No such block"):
        B.delete_block(doc, "門")


def test_delete_referenced_block(doc: Any) -> None:
    B.define_block(doc, "房間")
    B.insert_block(doc, "門", (0, 0))
    B.insert_block(doc, "門", (5, 5), block="房間")
    line = E.add_line(doc, (0, 0), (1, 1))
    with pytest.raises(ValueError, match="has 2 references"):
        B.delete_block(doc, "門")
    assert B.get_block(doc, "門").references == 2
    assert B.delete_block(doc, "門", delete_references=True) == 2
    assert [b.name for b in B.list_blocks(doc)] == ["房間"]
    assert E.select(doc) == [line] and E.select(doc, block="房間") == []
    assert not doc.audit().has_errors


def test_delete_block_with_locked_reference_changes_nothing(doc: Any) -> None:
    B.insert_block(doc, "門", (0, 0))
    B.insert_block(doc, "門", (5, 5), layer="鎖")
    with pytest.raises(ValueError, match="locked layer"):
        B.delete_block(doc, "門", delete_references=True)
    assert B.get_block(doc, "門").references == 2


def test_delete_block_used_by_dimstyle(doc: Any) -> None:
    B.define_block(doc, "箭頭")
    doc.dimstyles.get("Standard").dxf.dimblk = "箭頭"
    with pytest.raises(ValueError, match="dimension style"):
        B.delete_block(doc, "箭頭")
    assert B.purge_blocks(doc) == ["門"]


def test_purge_blocks_removes_unused_recursively(doc: Any) -> None:
    B.define_block(doc, "房間")
    B.insert_block(doc, "門", (0, 0), block="房間")  # 門 is only used by the unused 房間
    B.define_block(doc, "使用中")
    B.insert_block(doc, "使用中", (0, 0))
    doc.blocks.new("_ARROW")
    assert sorted(B.purge_blocks(doc)) == ["房間", "門"]
    assert {b.name for b in B.list_blocks(doc)} == {"使用中", "_ARROW"}
    assert B.purge_blocks(doc) == []
    assert not doc.audit().has_errors


# -- explode -----------------------------------------------------------------------


def test_explode_transforms_entities(doc: Any) -> None:
    handle = B.insert_block(doc, "門", (100, 50), scale=2, rotation=90, attributes={"編號": "A"})
    new = B.explode(doc, handle)
    assert _types(doc, new) == ["LINE", "CIRCLE", "TEXT"]
    line, circle, text = (E.get_entity(doc, h) for h in new)
    assert line.geometry["start"] == _approx((100.0, 50.0, 0.0))
    assert line.geometry["end"] == _approx((100.0, 70.0, 0.0))
    assert circle.geometry["center"] == _approx((90.0, 60.0, 0.0))
    assert circle.geometry["radius"] == _approx(4.0)
    assert text.geometry["text"] == "A"
    assert text.geometry["insert"] == _approx((106.0, 50.0, 0.0))
    assert text.geometry["height"] == _approx(4.0)
    assert B.list_inserts(doc) == [] and E.select(doc) == new
    with pytest.raises(KeyError, match="No such entity"):
        E.get_entity(doc, handle)
    # The definition is untouched.
    assert B.get_block(doc, "門").entity_count == 4


def test_explode_drop_attributes(doc: Any) -> None:
    handle = B.insert_block(doc, "門", (0, 0))
    assert _types(doc, B.explode(doc, handle, attributes="drop")) == ["LINE", "CIRCLE"]


def test_explode_non_uniform_scale_makes_ellipse(doc: Any) -> None:
    handle = B.insert_block(doc, "門", (0, 0), scale=(2, 1))
    new = B.explode(doc, handle, attributes="drop")
    assert _types(doc, new) == ["LINE", "ELLIPSE"]
    assert E.get_entity(doc, new[0]).geometry["end"] == _approx((20.0, 0.0, 0.0))
    ellipse = doc.entitydb[new[1]]
    assert tuple(ellipse.dxf.center) == _approx((10.0, 5.0, 0.0))
    assert ellipse.dxf.major_axis.magnitude == _approx(4.0)
    assert ellipse.dxf.ratio == _approx(0.5)


def test_explode_non_uniform_scale_needs_r2000() -> None:
    d = ezdxf.new("R12")
    d.blocks.new("B").add_circle((0, 0), 1)
    handle = B.insert_block(d, "B", (0, 0), scale=(2, 1))
    with pytest.raises(ValueError, match="requires DXF R2000"):
        B.explode(d, handle)
    assert len(B.list_inserts(d)) == 1
    mirrored = B.insert_block(d, "B", (0, 0), scale=(-1, 1))
    assert _types(d, B.explode(d, mirrored)) == ["CIRCLE"]


def test_explode_keeps_entity_properties(doc: Any) -> None:
    E.add_line(doc, (0, 0), (0, 5), block="門", layer="牆", color=3)
    handle = B.insert_block(doc, "門", (0, 0), layer="牆")
    new = B.explode(doc, handle, attributes="drop")
    details = [E.get_entity(doc, h) for h in new]
    assert [(d.layer, d.color) for d in details] == [("0", 256), ("0", 256), ("牆", 3)]


def test_explode_nested_and_recursive(doc: Any) -> None:
    B.define_block(doc, "房間")
    E.add_line(doc, (0, 0), (50, 0), block="房間")
    B.insert_block(doc, "門", (10, 0), block="房間")
    first = B.insert_block(doc, "房間", (100, 0))
    second = B.insert_block(doc, "房間", (200, 0))

    new = B.explode(doc, first, attributes="drop")
    assert _types(doc, new) == ["LINE", "INSERT"]
    nested = B.get_insert(doc, new[1])
    assert (nested.name, nested.insert) == ("門", (110.0, 0.0, 0.0))
    assert nested.attributes == {"編號": "D-00"}

    new = B.explode(doc, second, recursive=True)
    assert sorted(_types(doc, new)) == ["CIRCLE", "LINE", "LINE", "TEXT"]
    centers = [E.get_entity(doc, h).geometry for h in new if E.get_entity(doc, h).type == "CIRCLE"]
    assert centers[0]["center"] == _approx((215.0, 5.0, 0.0))
    assert [r.name for r in B.list_inserts(doc)] == ["門"]
    assert B.get_block(doc, "房間").references == 0


def test_explode_several_and_duplicates(doc: Any) -> None:
    a = B.insert_block(doc, "門", (0, 0))
    b = B.insert_block(doc, "門", (50, 0))
    new = B.explode(doc, [a, b, a.lower()])
    assert len(new) == 6 and B.get_block(doc, "門").references == 0


def test_explode_errors_change_nothing(doc: Any) -> None:
    ref = B.insert_block(doc, "門", (0, 0))
    line = E.add_line(doc, (0, 0), (1, 1))
    with pytest.raises(ValueError, match="is a LINE, not an INSERT"):
        B.explode(doc, [ref, line])
    with pytest.raises(KeyError, match="No such entity"):
        B.explode(doc, [ref, "FFFF"])
    with pytest.raises(ValueError, match="attributes must be one of"):
        B.explode(doc, ref, attributes="keep")
    doc.blocks.new("暫時")
    orphan = B.insert_block(doc, "暫時", (0, 0))
    doc.blocks.delete_block("暫時", safe=False)
    with pytest.raises(ValueError, match="missing block"):
        B.explode(doc, [ref, orphan])
    assert [r.handle for r in B.list_inserts(doc)] == [ref, orphan]


def test_exploding_attribute_only_parts_is_rejected(doc: Any) -> None:
    handle = B.insert_block(doc, "門", (0, 0))
    attrib = doc.entitydb[handle].attribs[0].dxf.handle
    with pytest.raises(ValueError, match="belongs to another entity"):
        B.explode(doc, attrib)


# -- existing entity operations on references ------------------------------------------


def test_move_and_copy_reference_carry_attributes(doc: Any) -> None:
    handle = B.insert_block(doc, "門", (0, 0), attributes={"編號": "原"})
    E.move_entities(doc, handle, 10, 0)
    assert B.get_insert(doc, handle).insert == (10.0, 0.0, 0.0)
    (copy,) = E.copy_entities(doc, handle, 0, 20)
    ref = B.get_insert(doc, copy)
    assert ref.insert == (10.0, 20.0, 0.0) and ref.attributes == {"編號": "原"}
    B.set_attributes(doc, copy, {"編號": "複本"})
    assert B.get_attributes(doc, handle) == {"編號": "原"}
    assert B.get_block(doc, "門").references == 2


# -- DxfDocument -------------------------------------------------------------------


def test_document_block_workflow() -> None:
    document = DxfDocument(ezdxf.new("R2010"))
    line = document.add_line((0, 0), (10, 0))
    circle = document.add_circle((5, 5), 2)
    block = document.define_block("椅", handles=[line, circle], replace=True)
    assert block.references == 1 and document.metadata.extra["block_count"] == 1
    assert document.metadata.extra["entity_counts"] == {"INSERT": 1}
    document.add_attdef("椅", "編號", (0, -3), default="C-00")
    document.add_line((0, 0), (0, 10), block="椅")
    assert [e.type for e in document.entity_details(block="椅")] == [
        "LINE",
        "CIRCLE",
        "ATTDEF",
        "LINE",
    ]
    assert len(document.select(block="椅", types="LINE")) == 2

    handle = document.insert_block("椅", (30, 0), rotation=180, attributes={"編號": "C-01"})
    assert document.metadata.extra["entity_counts"] == {"INSERT": 2}
    assert document.insert(handle).rotation == 180.0
    assert [r.attributes for r in document.inserts(name="椅")] == [{}, {"編號": "C-01"}]
    assert document.attributes(handle) == {"編號": "C-01"}
    assert document.set_attributes(handle, {"編號": "C-02"}) == {"編號": "C-02"}
    assert "C-02" in document.text()
    assert document.modify_insert(handle, insert=(40, 0)).insert == (40.0, 0.0, 0.0)

    assert document.rename_block("椅", "椅子").name == "椅子"
    assert [b.name for b in document.block_details()] == ["椅子"]
    assert document.block("椅子").references == 2
    assert [b.name for b in document.blocks()] == ["椅子"]

    new = document.explode(handle)
    assert document.metadata.extra["entity_counts"] == {
        "CIRCLE": 1,
        "INSERT": 1,
        "LINE": 2,
        "TEXT": 1,
    }
    assert "C-02" in [document.entity(h).geometry.get("text") for h in new]
    assert document.delete_block("椅子", delete_references=True) == 1
    assert document.metadata.extra["block_count"] == 0
    document.define_block("空")
    assert document.purge_blocks() == ["空"]
    assert document.metadata.extra["block_count"] == 0
