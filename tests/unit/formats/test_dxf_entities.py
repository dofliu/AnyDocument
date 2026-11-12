"""Tests for DXF entity editing (T082)."""

from __future__ import annotations

from typing import Any

import ezdxf
import pytest

from anydoc.formats.dxf import DxfDocument, EntityDetail
from anydoc.formats.dxf import entities as E


@pytest.fixture
def doc() -> Any:
    """An R2018 drawing with layers 牆 (red), 標註 and 鎖 (locked, one line)."""
    d = ezdxf.new("R2018")
    d.layers.add("牆", color=1)
    d.layers.add("標註", color=3)
    d.layers.add("鎖").lock()
    d.modelspace().add_line((0, 0), (1, 1), dxfattribs={"layer": "鎖"})
    return d


def _locked(d: Any) -> str:
    return E.select(d, layer="鎖")[0]


def _geo(d: Any, handle: str) -> dict[str, Any]:
    return E.get_entity(d, handle).geometry


def _approx(value: Any) -> Any:
    return pytest.approx(value, abs=1e-9)


# -- colours -------------------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (0, (0, None)),
        (256, (256, None)),
        ("0", (0, None)),
        ("256", (256, None)),
        ("ByLayer", (256, None)),
        (" BYBLOCK ", (0, None)),
        (5, (5, None)),
        ("red", (1, None)),
        ("#FF8000", (None, (255, 128, 0))),
    ],
)
def test_parse_entity_color(value: int | str, expected: tuple[Any, Any]) -> None:
    assert E.parse_entity_color(value) == expected


@pytest.mark.parametrize("value", [-1, 257, "300", "紅", "#FFF", "", True])
def test_parse_entity_color_invalid(value: Any) -> None:
    with pytest.raises(ValueError, match="[Cc]olour"):
        E.parse_entity_color(value)


# -- add -----------------------------------------------------------------------


def test_add_line(doc: Any) -> None:
    handle = E.add_line(doc, (0, 0), (10, 5), layer="牆")
    detail = E.get_entity(doc, handle)
    assert detail == EntityDetail(
        handle=handle,
        type="LINE",
        layer="牆",
        layout="Model",
        color=256,
        linetype="BYLAYER",
        true_color=None,
        geometry={"start": (0.0, 0.0, 0.0), "end": (10.0, 5.0, 0.0)},
    )


def test_add_line_3d_points(doc: Any) -> None:
    handle = E.add_line(doc, (0, 0, 1), [2, 3, 4])
    assert _geo(doc, handle) == {"start": (0.0, 0.0, 1.0), "end": (2.0, 3.0, 4.0)}


def test_add_circle(doc: Any) -> None:
    handle = E.add_circle(doc, (5, 5), 2.5, color="blue")
    detail = E.get_entity(doc, handle)
    assert detail.type == "CIRCLE" and detail.color == 5
    assert detail.geometry == {"center": (5.0, 5.0, 0.0), "radius": 2.5}


def test_add_arc(doc: Any) -> None:
    handle = E.add_arc(doc, (1, 2), 3, 30, 120)
    assert _geo(doc, handle) == {
        "center": (1.0, 2.0, 0.0),
        "radius": 3.0,
        "start_angle": 30.0,
        "end_angle": 120.0,
    }


def test_add_polyline_open_and_closed_with_bulge(doc: Any) -> None:
    opened = E.add_polyline(doc, [(0, 0), (4, 0), (4, 3)])
    closed = E.add_polyline(doc, [(0, 0), (4, 0, 1.0), (4, 3)], closed=True)
    assert E.get_entity(doc, opened).type == "LWPOLYLINE"
    assert _geo(doc, opened) == {
        "points": [(0.0, 0.0, 0.0), (4.0, 0.0, 0.0), (4.0, 3.0, 0.0)],
        "closed": False,
    }
    assert _geo(doc, closed)["closed"] is True
    assert _geo(doc, closed)["points"][1] == (4.0, 0.0, 1.0)


def test_add_text(doc: Any) -> None:
    handle = E.add_text(doc, "尺寸說明", (1, 2), height=3.5, rotation=30, layer="標註")
    detail = E.get_entity(doc, handle)
    assert detail.type == "TEXT" and detail.layer == "標註"
    assert detail.geometry == {
        "text": "尺寸說明",
        "insert": (1.0, 2.0, 0.0),
        "height": 3.5,
        "rotation": 30.0,
    }


def test_add_text_rejects_newline(doc: Any) -> None:
    with pytest.raises(ValueError, match="single-line"):
        E.add_text(doc, "a\nb", (0, 0))


def test_add_mtext(doc: Any) -> None:
    handle = E.add_mtext(doc, "第一行\n第二行", (1, 1), height=2, width=40, rotation=45)
    detail = E.get_entity(doc, handle)
    assert detail.type == "MTEXT"
    assert detail.geometry["text"] == "第一行\n第二行"
    assert detail.geometry["insert"] == (1.0, 1.0, 0.0)
    assert detail.geometry["height"] == 2.0
    assert detail.geometry["width"] == 40.0
    assert detail.geometry["rotation"] == _approx(45.0)
    assert doc.entitydb.get(handle).text == "第一行\\P第二行"


def test_add_mtext_without_width(doc: Any) -> None:
    handle = E.add_mtext(doc, "x", (0, 0))
    assert _geo(doc, handle)["width"] == 0.0 and _geo(doc, handle)["rotation"] == 0.0


def test_add_defaults_to_current_layer(doc: Any) -> None:
    doc.header["$CLAYER"] = "標註"
    assert E.get_entity(doc, E.add_line(doc, (0, 0), (1, 1))).layer == "標註"


def test_add_layer_name_is_case_insensitive_and_canonical(doc: Any) -> None:
    doc.layers.add("Walls")
    assert E.get_entity(doc, E.add_line(doc, (0, 0), (1, 1), layer="WALLS")).layer == "Walls"


def test_add_to_missing_layer_or_layout(doc: Any) -> None:
    before = len(doc.modelspace())
    with pytest.raises(KeyError, match="No such layer: 無"):
        E.add_line(doc, (0, 0), (1, 1), layer="無")
    with pytest.raises(KeyError, match="No such layout: 無"):
        E.add_line(doc, (0, 0), (1, 1), layout="無")
    assert len(doc.modelspace()) == before


def test_add_to_paper_space_layout(doc: Any) -> None:
    handle = E.add_circle(doc, (0, 0), 1, layout="Layout1")
    assert E.get_entity(doc, handle).layout == "Layout1"
    assert E.select(doc, layout="Layout1") == [handle]


def test_add_with_color_and_linetype(doc: Any) -> None:
    handle = E.add_line(doc, (0, 0), (1, 1), color="#336699", linetype="dashed")
    detail = E.get_entity(doc, handle)
    assert detail.true_color == "#336699" and detail.linetype == "DASHED"
    assert doc.linetypes.has_entry("DASHED")
    by = E.add_line(doc, (0, 0), (1, 1), color="byblock", linetype="ByBlock")
    assert E.get_entity(doc, by).color == 0 and E.get_entity(doc, by).linetype == "BYBLOCK"


def test_add_to_locked_layer_is_allowed(doc: Any) -> None:
    assert E.get_entity(doc, E.add_line(doc, (0, 0), (1, 1), layer="鎖")).layer == "鎖"


@pytest.mark.parametrize(
    ("call", "message"),
    [
        (lambda d: E.add_line(d, (0,), (1, 1)), "Start point"),
        (lambda d: E.add_line(d, (0, 0), "1,1"), "End point"),
        (lambda d: E.add_line(d, (0, 0), (1, float("nan"))), "finite"),
        (lambda d: E.add_line(d, (0, 0), (1, "a")), "number"),
        (lambda d: E.add_circle(d, (0, 0), 0), "Radius"),
        (lambda d: E.add_circle(d, (0, 0), -1), "Radius"),
        (lambda d: E.add_arc(d, (0, 0), 1, "a", 90), "Start angle"),
        (lambda d: E.add_polyline(d, [(0, 0)]), "at least 2"),
        (lambda d: E.add_polyline(d, [(0, 0), (1,)]), "vertex"),
        (lambda d: E.add_polyline(d, "0,0"), "sequence"),
        (lambda d: E.add_text(d, "x", (0, 0), height=0), "Text height"),
        (lambda d: E.add_mtext(d, "x", (0, 0), width=-1), "Text width"),
        (lambda d: E.add_line(d, (0, 0), (1, 1), color=999), "Colour"),
        (lambda d: E.add_line(d, (0, 0), (1, 1), linetype="NOPE"), "Unknown linetype"),
    ],
)
def test_add_invalid_arguments_leave_drawing_unchanged(doc: Any, call: Any, message: str) -> None:
    before = len(doc.modelspace())
    with pytest.raises(ValueError, match=message):
        call(doc)
    assert len(doc.modelspace()) == before


def test_r12_rejects_lwpolyline_mtext_and_true_color() -> None:
    d = ezdxf.new("R12")
    with pytest.raises(ValueError, match="LWPOLYLINE requires DXF R2000"):
        E.add_polyline(d, [(0, 0), (1, 1)])
    with pytest.raises(ValueError, match="MTEXT requires DXF R2000"):
        E.add_mtext(d, "x", (0, 0))
    with pytest.raises(ValueError, match="True colour requires"):
        E.add_line(d, (0, 0), (1, 1), color="#102030")
    assert len(d.modelspace()) == 0
    assert E.get_entity(d, E.add_text(d, "x", (0, 0))).type == "TEXT"


# -- lookup --------------------------------------------------------------------


def test_get_entity_handle_is_case_insensitive(doc: Any) -> None:
    for _ in range(12):
        handle = E.add_line(doc, (0, 0), (1, 1))
    assert any(c.isalpha() for c in handle)
    assert E.get_entity(doc, f" {handle.lower()} ").handle == handle


@pytest.mark.parametrize("handle", ["FFFF", "", "zz", "0"])
def test_get_entity_missing(doc: Any, handle: str) -> None:
    with pytest.raises(KeyError, match="No such entity"):
        E.get_entity(doc, handle)


def test_get_entity_rejects_non_graphical_objects(doc: Any) -> None:
    layer_handle = doc.layers.get("牆").dxf.handle
    with pytest.raises(KeyError, match="No such entity"):
        E.get_entity(doc, layer_handle)


def test_list_and_select_filters(doc: Any) -> None:
    line = E.add_line(doc, (0, 0), (1, 1), layer="牆")
    circle = E.add_circle(doc, (0, 0), 1, layer="牆")
    text = E.add_text(doc, "x", (0, 0), layer="標註")
    assert E.select(doc, layer="牆") == [line, circle]
    assert E.select(doc, layer="牆", types="circle") == [circle]
    assert E.select(doc, types=["TEXT", "circle"]) == [circle, text]
    assert [d.handle for d in E.list_entities(doc, layer="標註")] == [text]
    assert len(E.list_entities(doc)) == 4
    assert E.select(doc, layout="Layout1") == []
    with pytest.raises(KeyError, match="No such layout"):
        E.select(doc, layout="無")


def test_unsupported_entity_has_empty_geometry(doc: Any) -> None:
    point = doc.modelspace().add_point((1, 2))
    detail = E.get_entity(doc, point.dxf.handle)
    assert detail.type == "POINT" and detail.geometry == {}


def test_to_dict_is_json_friendly(doc: Any) -> None:
    handle = E.add_polyline(doc, [(0, 0), (1, 1)], color="#010203")
    data = E.get_entity(doc, handle).to_dict()
    assert data == {
        "handle": handle,
        "type": "LWPOLYLINE",
        "layer": "0",
        "layout": "Model",
        "color": 256,
        "true_color": "#010203",
        "linetype": "BYLAYER",
        "points": [[0.0, 0.0, 0.0], [1.0, 1.0, 0.0]],
        "closed": False,
    }


# -- modify --------------------------------------------------------------------


def test_modify_common_properties(doc: Any) -> None:
    handle = E.add_line(doc, (0, 0), (1, 1))
    detail = E.modify_entity(doc, handle, layer="牆", color=4, linetype="CENTER")
    assert (detail.layer, detail.color, detail.linetype) == ("牆", 4, "CENTER")
    detail = E.modify_entity(doc, handle, color="#0A0B0C")
    assert detail.true_color == "#0A0B0C"
    detail = E.modify_entity(doc, handle, color="bylayer")
    assert detail.color == 256 and detail.true_color is None


def test_modify_line(doc: Any) -> None:
    handle = E.add_line(doc, (0, 0), (1, 1))
    detail = E.modify_entity(doc, handle, end=(9, 9))
    assert detail.geometry == {"start": (0.0, 0.0, 0.0), "end": (9.0, 9.0, 0.0)}
    assert _geo(doc, handle)["end"] == (9.0, 9.0, 0.0)


def test_modify_circle_and_arc(doc: Any) -> None:
    circle = E.add_circle(doc, (0, 0), 1)
    arc = E.add_arc(doc, (0, 0), 1, 0, 90)
    assert E.modify_entity(doc, circle, center=(3, 4), radius=5).geometry == {
        "center": (3.0, 4.0, 0.0),
        "radius": 5.0,
    }
    geometry = E.modify_entity(doc, arc, radius=2, start_angle=45, end_angle=270).geometry
    assert (geometry["radius"], geometry["start_angle"], geometry["end_angle"]) == (2.0, 45, 270)


def test_modify_polyline(doc: Any) -> None:
    handle = E.add_polyline(doc, [(0, 0), (1, 1)])
    geometry = E.modify_entity(doc, handle, points=[(0, 0), (5, 0, 0.5), (5, 5)], closed=True)
    assert geometry.geometry == {
        "points": [(0.0, 0.0, 0.0), (5.0, 0.0, 0.5), (5.0, 5.0, 0.0)],
        "closed": True,
    }
    assert E.modify_entity(doc, handle, closed=False).geometry["closed"] is False


def test_modify_text(doc: Any) -> None:
    handle = E.add_text(doc, "舊", (1, 1))
    geometry = E.modify_entity(
        doc, handle, text="新文字", insert=(7, 8), height=5, rotation=90
    ).geometry
    assert geometry == {"text": "新文字", "insert": (7.0, 8.0, 0.0), "height": 5.0, "rotation": 90}
    with pytest.raises(ValueError, match="single-line"):
        E.modify_entity(doc, handle, text="a\nb")


def test_modify_aligned_text_insert_keeps_alignment(doc: Any) -> None:
    from ezdxf.enums import TextEntityAlignment

    text = doc.modelspace().add_text("置中", height=2)
    text.set_placement((10, 10), align=TextEntityAlignment.MIDDLE_CENTER)
    geometry = E.modify_entity(doc, text.dxf.handle, insert=(20, 5)).geometry
    assert geometry["insert"] == (20.0, 5.0, 0.0)
    assert text.get_placement()[0] == TextEntityAlignment.MIDDLE_CENTER


def test_modify_mtext(doc: Any) -> None:
    handle = E.add_mtext(doc, "舊", (0, 0))
    geometry = E.modify_entity(
        doc, handle, text="一\n二", insert=(2, 3), height=4, width=30, rotation=90
    ).geometry
    assert geometry["text"] == "一\n二" and geometry["insert"] == (2.0, 3.0, 0.0)
    assert geometry["height"] == 4.0 and geometry["width"] == 30.0
    assert geometry["rotation"] == _approx(90.0)


def test_modify_none_changes_nothing(doc: Any) -> None:
    handle = E.add_circle(doc, (1, 1), 2, layer="牆")
    before = E.get_entity(doc, handle)
    assert E.modify_entity(doc, handle) == before


@pytest.mark.parametrize(
    ("kind", "props", "message"),
    [
        ("line", {"radius": 2}, "'radius' does not apply to LINE"),
        ("circle", {"start": (0, 0)}, "'start' does not apply to CIRCLE"),
        ("circle", {"start_angle": 10}, "'start_angle' does not apply to CIRCLE"),
        ("text", {"width": 10}, "'width' does not apply to TEXT"),
        ("point", {"center": (0, 0)}, "'center' does not apply to POINT"),
    ],
)
def test_modify_property_not_applicable(
    doc: Any, kind: str, props: dict[str, Any], message: str
) -> None:
    handles = {
        "line": E.add_line(doc, (0, 0), (1, 1)),
        "circle": E.add_circle(doc, (0, 0), 1),
        "text": E.add_text(doc, "x", (0, 0)),
        "point": doc.modelspace().add_point((0, 0)).dxf.handle,
    }
    with pytest.raises(ValueError, match=message):
        E.modify_entity(doc, handles[kind], **props)


def test_modify_validates_before_changing(doc: Any) -> None:
    handle = E.add_circle(doc, (0, 0), 1)
    before = E.get_entity(doc, handle)
    for bad in (
        {"center": (9, 9), "radius": -1},
        {"layer": "牆", "color": "nope"},
        {"color": 3, "linetype": "NOPE"},
        {"radius": 5, "layer": "無"},
    ):
        with pytest.raises((ValueError, KeyError)):
            E.modify_entity(doc, handle, **bad)
        assert E.get_entity(doc, handle) == before


def test_modify_non_geometric_entity_common_properties(doc: Any) -> None:
    point = doc.modelspace().add_point((0, 0))
    assert E.modify_entity(doc, point.dxf.handle, layer="牆", color=2).layer == "牆"


def test_modify_missing_or_locked(doc: Any) -> None:
    with pytest.raises(KeyError, match="No such entity"):
        E.modify_entity(doc, "FFFF", layer="牆")
    with pytest.raises(ValueError, match="locked layer: 鎖"):
        E.modify_entity(doc, _locked(doc), end=(5, 5))
    assert _geo(doc, _locked(doc))["end"] == (1.0, 1.0, 0.0)


# -- delete --------------------------------------------------------------------


def test_delete_entities(doc: Any) -> None:
    a = E.add_line(doc, (0, 0), (1, 1))
    b = E.add_circle(doc, (0, 0), 1)
    c = E.add_text(doc, "x", (0, 0))
    assert E.delete_entities(doc, [a, c, a.lower()]) == 2
    assert E.select(doc, layer="0") == [b]
    with pytest.raises(KeyError):
        E.get_entity(doc, a)
    assert E.delete_entities(doc, b) == 1
    assert not doc.audit().has_errors


def test_delete_is_all_or_nothing(doc: Any) -> None:
    a = E.add_line(doc, (0, 0), (1, 1))
    before = len(doc.modelspace())
    with pytest.raises(KeyError, match="No such entity: FFFF"):
        E.delete_entities(doc, [a, "FFFF"])
    with pytest.raises(ValueError, match="locked layer"):
        E.delete_entities(doc, [a, _locked(doc)])
    assert len(doc.modelspace()) == before


def test_delete_from_paper_space(doc: Any) -> None:
    handle = E.add_line(doc, (0, 0), (1, 1), layout="Layout1")
    assert E.delete_entities(doc, [handle]) == 1
    assert E.select(doc, layout="Layout1") == []


def test_attribs_must_be_edited_through_their_insert(doc: Any) -> None:
    block = doc.blocks.new("DOOR")
    block.add_attdef("TAG", (0, 0))
    insert = doc.modelspace().add_blockref("DOOR", (1, 1))
    attrib = insert.add_attrib("TAG", "門甲", (1, 1))
    with pytest.raises(ValueError, match="belongs to another entity"):
        E.delete_entities(doc, [attrib.dxf.handle])
    with pytest.raises(ValueError, match="belongs to another entity"):
        E.move_entities(doc, [attrib.dxf.handle], 1, 1)
    assert E.delete_entities(doc, [insert.dxf.handle]) == 1
    assert not doc.audit().has_errors


# -- transform -----------------------------------------------------------------


def test_move(doc: Any) -> None:
    line = E.add_line(doc, (0, 0), (10, 0))
    circle = E.add_circle(doc, (5, 5), 2)
    poly = E.add_polyline(doc, [(0, 0), (4, 0, 1), (4, 3)])
    text = E.add_text(doc, "x", (1, 2))
    mtext = E.add_mtext(doc, "y", (3, 4))
    handles = [line, circle, poly, text, mtext]
    assert E.move_entities(doc, handles, 10, -5) == handles
    assert _geo(doc, line) == {"start": (10.0, -5.0, 0.0), "end": (20.0, -5.0, 0.0)}
    assert _geo(doc, circle) == {"center": (15.0, 0.0, 0.0), "radius": 2.0}
    assert _geo(doc, poly)["points"] == [(10.0, -5.0, 0.0), (14.0, -5.0, 1.0), (14.0, -2.0, 0.0)]
    assert _geo(doc, text)["insert"] == (11.0, -3.0, 0.0)
    assert _geo(doc, mtext)["insert"] == (13.0, -1.0, 0.0)


def test_move_in_z(doc: Any) -> None:
    line = E.add_line(doc, (0, 0), (1, 0))
    E.move_entities(doc, line, 0, 0, 5)
    assert _geo(doc, line)["start"] == (0.0, 0.0, 5.0)


def test_rotate_about_origin_and_base(doc: Any) -> None:
    line = E.add_line(doc, (1, 0), (2, 0))
    arc = E.add_arc(doc, (1, 0), 1, 0, 90)
    text = E.add_text(doc, "x", (1, 0), rotation=10)
    E.rotate_entities(doc, [line, arc, text], 90)
    assert _geo(doc, line)["start"] == _approx((0.0, 1.0, 0.0))
    assert _geo(doc, line)["end"] == _approx((0.0, 2.0, 0.0))
    assert _geo(doc, arc)["center"] == _approx((0.0, 1.0, 0.0))
    assert _geo(doc, arc)["start_angle"] == _approx(90.0)
    assert _geo(doc, arc)["end_angle"] == _approx(180.0)
    assert _geo(doc, text)["rotation"] == _approx(100.0)

    other = E.add_line(doc, (2, 1), (3, 1))
    E.rotate_entities(doc, other, 180, base=(1, 1))
    assert _geo(doc, other)["start"] == _approx((0.0, 1.0, 0.0))
    assert _geo(doc, other)["end"] == _approx((-1.0, 1.0, 0.0))


def test_scale_about_origin_and_base(doc: Any) -> None:
    circle = E.add_circle(doc, (2, 2), 1)
    text = E.add_text(doc, "x", (1, 1), height=2)
    mtext = E.add_mtext(doc, "y", (1, 1), height=2, width=10)
    E.scale_entities(doc, [circle, text, mtext], 3)
    assert _geo(doc, circle)["center"] == _approx((6.0, 6.0, 0.0))
    assert _geo(doc, circle)["radius"] == _approx(3.0)
    assert _geo(doc, text)["height"] == _approx(6.0)
    assert _geo(doc, mtext)["height"] == _approx(6.0)
    assert _geo(doc, mtext)["width"] == _approx(30.0)

    line = E.add_line(doc, (1, 1), (3, 1))
    E.scale_entities(doc, line, 0.5, base=(1, 1))
    assert _geo(doc, line)["start"] == _approx((1.0, 1.0, 0.0))
    assert _geo(doc, line)["end"] == _approx((2.0, 1.0, 0.0))


def test_scale_keeps_polyline_bulge(doc: Any) -> None:
    poly = E.add_polyline(doc, [(0, 0), (4, 0, 1), (4, 4)], closed=True)
    E.scale_entities(doc, poly, 2)
    assert _geo(doc, poly)["points"] == _approx_points([(0, 0, 0), (8, 0, 1), (8, 8, 0)])
    assert _geo(doc, poly)["closed"] is True


def _approx_points(points: list[tuple[float, float, float]]) -> Any:
    return [_approx(p) for p in points]


@pytest.mark.parametrize("factor", [0, -2, float("inf"), "2"])
def test_scale_invalid_factor(doc: Any, factor: Any) -> None:
    line = E.add_line(doc, (0, 0), (1, 1))
    with pytest.raises(ValueError, match="Scale factor"):
        E.scale_entities(doc, line, factor)
    assert _geo(doc, line)["end"] == (1.0, 1.0, 0.0)


def test_transform_invalid_arguments(doc: Any) -> None:
    line = E.add_line(doc, (0, 0), (1, 1))
    with pytest.raises(ValueError, match="dx"):
        E.move_entities(doc, line, "1", 0)  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="Angle"):
        E.rotate_entities(doc, line, float("nan"))
    with pytest.raises(ValueError, match="Base point"):
        E.rotate_entities(doc, line, 90, base=(1,))


def test_transform_is_all_or_nothing_for_locked_and_missing(doc: Any) -> None:
    line = E.add_line(doc, (0, 0), (1, 1))
    with pytest.raises(ValueError, match="locked layer"):
        E.move_entities(doc, [line, _locked(doc)], 5, 5)
    with pytest.raises(KeyError):
        E.rotate_entities(doc, [line, "FFFF"], 90)
    assert _geo(doc, line) == {"start": (0.0, 0.0, 0.0), "end": (1.0, 1.0, 0.0)}


def test_transform_insert_moves_its_attributes(doc: Any) -> None:
    block = doc.blocks.new("DOOR")
    block.add_attdef("TAG", (0, 0))
    insert = doc.modelspace().add_blockref("DOOR", (1, 1))
    attrib = insert.add_attrib("TAG", "門甲", (1, 1))
    E.move_entities(doc, insert.dxf.handle, 10, 0)
    assert tuple(insert.dxf.insert)[:2] == (11.0, 1.0)
    assert tuple(attrib.dxf.insert)[:2] == (11.0, 1.0)


# -- copy ----------------------------------------------------------------------


def test_copy_with_offset(doc: Any) -> None:
    line = E.add_line(doc, (0, 0), (1, 1), layer="牆", color=5)
    text = E.add_text(doc, "複製", (2, 2))
    copies = E.copy_entities(doc, [line, text], 10, 20)
    assert len(copies) == 2 and set(copies).isdisjoint({line, text})
    copy = E.get_entity(doc, copies[0])
    assert (copy.type, copy.layer, copy.color, copy.layout) == ("LINE", "牆", 5, "Model")
    assert copy.geometry == {"start": (10.0, 20.0, 0.0), "end": (11.0, 21.0, 0.0)}
    assert _geo(doc, copies[1]) == {**_geo(doc, text), "insert": (12.0, 22.0, 0.0)}
    assert _geo(doc, line)["start"] == (0.0, 0.0, 0.0)
    assert not doc.audit().has_errors


def test_copy_in_place_and_to_layer(doc: Any) -> None:
    circle = E.add_circle(doc, (1, 1), 2)
    (copy,) = E.copy_entities(doc, circle, layer="標註")
    assert E.get_entity(doc, copy).layer == "標註"
    assert _geo(doc, copy) == _geo(doc, circle)
    with pytest.raises(KeyError, match="No such layer"):
        E.copy_entities(doc, circle, layer="無")


def test_copy_stays_in_source_layout_and_allows_locked_source(doc: Any) -> None:
    paper = E.add_line(doc, (0, 0), (1, 1), layout="Layout1")
    (copy,) = E.copy_entities(doc, paper, 1, 0)
    assert E.get_entity(doc, copy).layout == "Layout1"
    (locked_copy,) = E.copy_entities(doc, _locked(doc), 1, 1)
    assert E.get_entity(doc, locked_copy).layer == "鎖"


def test_copy_insert_copies_attributes(doc: Any) -> None:
    block = doc.blocks.new("DOOR")
    block.add_attdef("TAG", (0, 0))
    insert = doc.modelspace().add_blockref("DOOR", (1, 1))
    insert.add_attrib("TAG", "門甲", (1, 1))
    (copy,) = E.copy_entities(doc, insert.dxf.handle, 5, 0)
    clone = doc.entitydb.get(copy)
    assert [a.dxf.text for a in clone.attribs] == ["門甲"]
    assert clone.attribs[0] is not insert.attribs[0]
    assert tuple(clone.attribs[0].dxf.insert)[:2] == (6.0, 1.0)
    assert not doc.audit().has_errors


def test_copy_missing_handle_copies_nothing(doc: Any) -> None:
    line = E.add_line(doc, (0, 0), (1, 1))
    before = len(doc.modelspace())
    with pytest.raises(KeyError):
        E.copy_entities(doc, [line, "FFFF"], 1, 1)
    assert len(doc.modelspace()) == before


# -- DxfDocument ---------------------------------------------------------------


def test_document_methods_and_metadata_refresh(doc: Any) -> None:
    document = DxfDocument(doc)
    assert document.metadata.extra["entity_counts"] == {"LINE": 1}
    line = document.add_line((0, 0), (10, 0), layer="牆")
    circle = document.add_circle((5, 5), 2)
    arc = document.add_arc((0, 0), 3, 0, 90)
    poly = document.add_polyline([(0, 0), (1, 0), (1, 1)], closed=True)
    text = document.add_text("說明", (0, -5), height=3)
    mtext = document.add_mtext("多行\n文字", (0, -10))
    assert document.metadata.extra["entity_counts"] == {
        "ARC": 1,
        "CIRCLE": 1,
        "LINE": 2,
        "LWPOLYLINE": 1,
        "MTEXT": 1,
        "TEXT": 1,
    }
    assert document.entity(line).geometry["end"] == (10.0, 0.0, 0.0)
    assert document.select(layer="牆") == [line]
    assert [d.handle for d in document.entity_details(types="ARC")] == [arc]
    assert document.modify_entity(circle, radius=4).geometry["radius"] == 4.0
    assert document.move_entities([poly], 1, 1) == [poly]
    assert document.rotate_entities(text, 90) == [text]
    assert document.scale_entities(mtext, 2, (0, 0)) == [mtext]
    copies = document.copy_entities([line, circle], 0, 50)
    assert document.metadata.extra["entity_counts"]["LINE"] == 3
    assert document.delete_entities(copies) == 2
    assert document.metadata.extra["entity_counts"]["LINE"] == 2
    assert "說明" in document.text() and "多行" in document.text()


def test_decode_text_handles_unicode_escapes() -> None:
    assert E.decode_text("\\U+7248\\U+672c A") == "版本 A"
    assert E.decode_text("plain 文字") == "plain 文字"
