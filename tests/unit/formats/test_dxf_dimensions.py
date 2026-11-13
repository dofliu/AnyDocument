"""Tests for DXF dimensions: read measurements, create linear/aligned/radial (T085)."""

from __future__ import annotations

from typing import Any

import ezdxf
import pytest

from anydoc.formats.dxf import DimensionInfo, DxfDocument
from anydoc.formats.dxf import dimensions as D
from anydoc.formats.dxf import entities as E


@pytest.fixture
def doc() -> Any:
    """An R2018 drawing with layers 標註 and a locked layer 鎖, plus a circle."""
    d = ezdxf.new("R2018")
    d.layers.add("標註", color=3)
    d.layers.add("鎖").lock()
    d.modelspace().add_circle((50, 50), 25)
    return d


def _approx(value: Any) -> Any:
    return pytest.approx(value, abs=1e-9)


def _drawn_text(d: Any, handle: str) -> list[str]:
    """Return the text actually drawn in the geometry block of a dimension."""
    dim = d.entitydb[handle]
    block = d.blocks.get(dim.dxf.geometry)
    return [e.plain_text() for e in block if e.dxftype() in ("TEXT", "MTEXT")]


def _dim_blocks(d: Any) -> list[str]:
    return [b.name for b in d.blocks if b.name.startswith("*D")]


# -- linear ----------------------------------------------------------------------


def test_add_linear_horizontal_with_distance(doc: Any) -> None:
    handle = D.add_linear_dimension(doc, (0, 0), (100, 30), distance=10)
    info = D.get_dimension(doc, handle)
    assert isinstance(info, DimensionInfo)
    assert (info.kind, info.layout, info.layer, info.style) == ("linear", "Model", "0", "Standard")
    assert info.measurement == _approx(100.0)
    assert info.text == "100" and info.override is None
    assert info.angle == 0.0
    assert info.points["p1"] == (0.0, 0.0, 0.0) and info.points["p2"] == (100.0, 30.0, 0.0)
    assert info.points["base"] == _approx((0.0, 10.0, 0.0))
    assert _drawn_text(doc, handle) == ["100"]


def test_add_linear_vertical_with_base_and_named_angle(doc: Any) -> None:
    handle = D.add_linear_dimension(doc, (0, 0), (100, 30), base=(-8, 0), angle="Vertical")
    info = D.get_dimension(doc, handle)
    assert info.measurement == _approx(30.0) and info.angle == 90.0
    assert info.points["base"] == (-8.0, 0.0, 0.0)
    assert info.text == "30"


def test_add_linear_rotated_and_negative_distance(doc: Any) -> None:
    handle = D.add_linear_dimension(doc, (0, 0), (30, 40), distance=-5, angle=45)
    info = D.get_dimension(doc, handle)
    assert info.measurement == _approx(70 / 2**0.5)
    assert info.points["base"] == _approx((5 / 2**0.5, -5 / 2**0.5, 0.0))
    assert info.text == _drawn_text(doc, handle)[0] == "49,5"  # style default: comma


def test_linear_text_override_layer_color_and_style_overrides(doc: Any) -> None:
    handle = D.add_linear_dimension(
        doc,
        (0, 0),
        (12.3456, 0),
        distance=5,
        text="寬 <> 公分",
        layer="標註",
        color="#FF8000",
        text_height=3.5,
        arrow_size=2,
        decimals=3,
        decimal_separator=".",
    )
    info = D.get_dimension(doc, handle)
    assert info.text == "寬 12.346 公分" and info.override == "寬 <> 公分"
    assert _drawn_text(doc, handle) == ["寬 12.346 公分"]
    detail = E.get_entity(doc, handle)
    assert (detail.layer, detail.true_color) == ("標註", "#FF8000")
    style = doc.entitydb[handle].override()
    assert (style.get("dimtxt"), style.get("dimasz"), style.get("dimdec")) == (3.5, 2.0, 3)
    assert style.get_decimal_separator() == "."
    block = doc.blocks.get(doc.entitydb[handle].dxf.geometry)
    (mtext,) = [e for e in block if e.dxftype() == "MTEXT"]
    assert mtext.dxf.char_height == pytest.approx(3.5)


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({}, "exactly one of base and distance"),
        ({"base": (0, 5), "distance": 5}, "exactly one of base and distance"),
        ({"distance": 5, "angle": 90}, "no extent"),
        ({"distance": float("nan")}, "Distance"),
        ({"distance": 5, "angle": "diagonal"}, "Angle"),
        ({"distance": 5, "text": "a\nb"}, "single line"),
        ({"distance": 5, "text_height": 0}, "Text height"),
        ({"distance": 5, "arrow_size": -1}, "Arrow size"),
        ({"distance": 5, "decimals": 9}, "Decimals"),
        ({"distance": 5, "decimals": True}, "Decimals"),
        ({"distance": 5, "decimal_separator": ";"}, "Decimal separator"),
        ({"distance": 5, "color": "nope"}, "olo"),
    ],
)
def test_add_linear_rejects_bad_input(doc: Any, kwargs: dict[str, Any], message: str) -> None:
    with pytest.raises(ValueError, match=message):
        D.add_linear_dimension(doc, (0, 0), (100, 0), **kwargs)
    assert D.list_dimensions(doc) == [] and _dim_blocks(doc) == []


def test_add_linear_rejects_3d_points_and_unknown_names(doc: Any) -> None:
    with pytest.raises(ValueError, match="XY plane"):
        D.add_linear_dimension(doc, (0, 0, 5), (100, 0), distance=5)
    with pytest.raises(KeyError, match="No such layer"):
        D.add_linear_dimension(doc, (0, 0), (100, 0), distance=5, layer="無")
    with pytest.raises(KeyError, match="No such dimension style"):
        D.add_linear_dimension(doc, (0, 0), (100, 0), distance=5, style="無")
    with pytest.raises(KeyError, match="No such layout"):
        D.add_linear_dimension(doc, (0, 0), (100, 0), distance=5, layout="無")
    assert D.list_dimensions(doc) == []


# -- aligned ---------------------------------------------------------------------


def test_add_aligned_dimension(doc: Any) -> None:
    handle = D.add_aligned_dimension(doc, (0, 0), (30, 40), 5, layer="標註")
    info = D.get_dimension(doc, handle)
    assert (info.kind, info.layer, info.angle) == ("aligned", "標註", None)
    assert info.measurement == _approx(50.0) and info.text == "50"
    assert info.points["p1"] == (0.0, 0.0, 0.0) and info.points["p2"] == (30.0, 40.0, 0.0)
    assert info.points["base"] == _approx((-4.0, 3.0, 0.0))  # 5 units left of p1->p2
    assert _drawn_text(doc, handle) == ["50"]


def test_update_aligned_dimension_without_angle_attribute(doc: Any) -> None:
    """Aligned dimensions from other software carry no ``angle``; redraw derives it."""
    handle = D.add_aligned_dimension(doc, (0, 0), (30, 40), 5)
    dim = doc.entitydb[handle]
    dim.dxf.discard("angle")
    dim.dxf.defpoint3 = (0, 20, 0)
    dim.dxf.defpoint = (-5, 20, 0)
    assert D.update_dimensions(doc, handle) == [handle]
    assert dim.dxf.angle == pytest.approx(90.0)
    info = D.get_dimension(doc, handle)
    assert info.kind == "aligned" and info.measurement == _approx(20.0)
    assert _drawn_text(doc, handle) == ["20"]


def test_add_aligned_rejects_bad_input(doc: Any) -> None:
    with pytest.raises(ValueError, match="different points"):
        D.add_aligned_dimension(doc, (1, 1), (1, 1), 5)
    with pytest.raises(ValueError, match="Distance"):
        D.add_aligned_dimension(doc, (0, 0), (1, 1), float("inf"))
    with pytest.raises(ValueError, match="XY plane"):
        D.add_aligned_dimension(doc, (0, 0), (1, 1, 1), 5)
    assert D.list_dimensions(doc) == []


# -- radius / diameter -----------------------------------------------------------


def test_add_radius_dimension_from_center(doc: Any) -> None:
    handle = D.add_radius_dimension(doc, (10, 20), 7.5, angle=90, decimal_separator=".")
    info = D.get_dimension(doc, handle)
    assert info.kind == "radius" and info.measurement == _approx(7.5)
    assert info.text == "R7.5" == _drawn_text(doc, handle)[0]
    assert info.points["center"] == (10.0, 20.0, 0.0)
    assert info.points["point"] == _approx((10.0, 27.5, 0.0))


def test_add_radius_dimension_from_entity(doc: Any) -> None:
    circle = E.select(doc, types="CIRCLE")[0]
    arc = E.add_arc(doc, (0, 0), 4, 0, 90)
    info = D.get_dimension(doc, D.add_radius_dimension(doc, entity=circle.lower(), angle=180))
    assert info.measurement == _approx(25.0) and info.text == "R25"
    assert info.points["center"] == (50.0, 50.0, 0.0)
    assert info.points["point"] == _approx((25.0, 50.0, 0.0))
    assert D.get_dimension(doc, D.add_radius_dimension(doc, entity=arc)).measurement == 4.0


def test_add_diameter_dimension(doc: Any) -> None:
    circle = E.select(doc, types="CIRCLE")[0]
    handle = D.add_diameter_dimension(doc, entity=circle, text="直徑 <>")
    info = D.get_dimension(doc, handle)
    assert info.kind == "diameter" and info.measurement == _approx(50.0)
    assert info.text == "直徑 Ø50" == _drawn_text(doc, handle)[0]
    assert info.points["center"] == _approx((50.0, 50.0, 0.0))
    assert info.points["point"] == _approx((25.0, 50.0, 0.0))
    assert info.points["opposite"] == _approx((75.0, 50.0, 0.0))
    plain = D.add_diameter_dimension(doc, (0, 0), 1.25)
    assert D.get_dimension(doc, plain).text == "Ø2,5" == _drawn_text(doc, plain)[0]


def test_radial_rejects_bad_input(doc: Any) -> None:
    circle = E.select(doc, types="CIRCLE")[0]
    line = E.add_line(doc, (0, 0), (1, 1))
    with pytest.raises(ValueError, match="center and radius, or entity"):
        D.add_radius_dimension(doc)
    with pytest.raises(ValueError, match="center and radius, or entity"):
        D.add_radius_dimension(doc, (0, 0))
    with pytest.raises(ValueError, match="not both"):
        D.add_radius_dimension(doc, (0, 0), 5, entity=circle)
    with pytest.raises(ValueError, match="not a CIRCLE or ARC"):
        D.add_diameter_dimension(doc, entity=line)
    with pytest.raises(KeyError, match="No such entity"):
        D.add_radius_dimension(doc, entity="FFFF")
    with pytest.raises(ValueError, match="Radius"):
        D.add_radius_dimension(doc, (0, 0), 0)
    with pytest.raises(ValueError, match="Angle"):
        D.add_radius_dimension(doc, (0, 0), 5, angle=float("nan"))
    tilted = doc.modelspace().add_circle((0, 0), 3, dxfattribs={"extrusion": (1, 0, 0)})
    with pytest.raises(ValueError, match="not in the XY plane"):
        D.add_radius_dimension(doc, entity=tilted.dxf.handle)
    assert D.list_dimensions(doc) == []


# -- reading ---------------------------------------------------------------------


def test_list_dimensions_filters(doc: Any) -> None:
    a = D.add_linear_dimension(doc, (0, 0), (10, 0), distance=2)
    b = D.add_aligned_dimension(doc, (0, 0), (3, 4), 1, layer="標註")
    c = D.add_radius_dimension(doc, (0, 0), 5, layer="標註")
    assert [d.handle for d in D.list_dimensions(doc)] == [a, b, c]
    assert [d.handle for d in D.list_dimensions(doc, layer="標註")] == [b, c]
    assert [d.handle for d in D.list_dimensions(doc, kinds="Radius")] == [c]
    assert [d.handle for d in D.list_dimensions(doc, kinds=["linear", "aligned"])] == [a, b]
    assert D.list_dimensions(doc, layout="Layout1") == []
    with pytest.raises(ValueError, match="Unknown dimension kind: arc"):
        D.list_dimensions(doc, kinds=["arc"])
    with pytest.raises(KeyError, match="No such layout"):
        D.list_dimensions(doc, layout="無")


def test_get_dimension_errors_and_to_dict(doc: Any) -> None:
    handle = D.add_linear_dimension(doc, (0, 0), (10, 0), distance=2)
    circle = E.select(doc, types="CIRCLE")[0]
    with pytest.raises(KeyError, match="No such entity"):
        D.get_dimension(doc, "FFFF")
    with pytest.raises(ValueError, match="is a CIRCLE, not a DIMENSION"):
        D.get_dimension(doc, circle)
    data = D.get_dimension(doc, handle.lower()).to_dict()
    assert data["handle"] == handle and data["kind"] == "linear"
    assert data["measurement"] == 10.0 and data["text"] == "10" and data["override"] is None
    assert data["points"]["p2"] == [10.0, 0.0, 0.0] and data["angle"] == 0.0


def test_measurement_follows_definition_points_of_existing_dimensions() -> None:
    """Dimensions made by another tool (ezdxf directly) are read the same way."""
    d = ezdxf.new("R2010", setup=True)
    msp = d.modelspace()
    msp.add_linear_dim(base=(0, 3), p1=(0, 0), p2=(7.5, 0), dimstyle="EZDXF").render()
    msp.add_angular_dim_3p(
        base=(5, 5), center=(0, 0), p1=(10, 0), p2=(0, 10), dimstyle="EZ_CURVED"
    ).render()
    linear, angular = D.list_dimensions(d)
    assert linear.style == "EZDXF" and linear.measurement == _approx(7.5)
    assert linear.text == _drawn_text(d, linear.handle)[0]  # EZDXF style: metres shown as cm
    assert angular.kind == "angular3p" and angular.measurement == _approx(90.0)
    assert angular.text == "90°"
    assert set(angular.points) >= {"defpoint", "defpoint2", "defpoint3"}
    assert "EZDXF" in D.dimension_styles(d) and "Standard" in D.dimension_styles(d)


def test_style_formatting_dimlfac_dimpost_and_suppressed_text(doc: Any) -> None:
    style = doc.dimstyles.new("公制")
    style.dxf.dimlfac = 10
    style.dxf.dimdec = 1
    style.dxf.dimzin = 0
    style.dxf.dimpost = "<> mm"
    style.dxf.dimdsep = ord(".")
    handle = D.add_linear_dimension(doc, (0, 0), (12.34, 0), distance=3, style="公制")
    info = D.get_dimension(doc, handle)
    assert info.style == "公制" and info.measurement == _approx(12.34)
    assert info.text == "123.4 mm" == _drawn_text(doc, handle)[0]
    hidden = D.set_dimension_text(doc, handle, " ")
    assert hidden.text == "" and hidden.override == " "
    special = D.set_dimension_text(doc, handle, "%%c20 %%p0.1 45%%d")
    assert special.text == "Ø20 ±0.1 45°"


def test_zero_dimrnd_means_no_rounding(doc: Any) -> None:
    """Loaded drawings store DIMRND = 0; the drawn text must not be rounded to integers."""
    style = doc.dimstyles.get("Standard")
    style.dxf.dimrnd = 0.0
    handle = D.add_linear_dimension(doc, (0, 0), (70.71, 0), distance=3)
    assert D.get_dimension(doc, handle).text == "70,71" == _drawn_text(doc, handle)[0]
    assert style.dxf.dimrnd == 0.0  # the style itself is left unchanged
    style.dxf.dimrnd = 0.5
    other = D.add_linear_dimension(doc, (0, 0), (70.71, 0), distance=6)
    assert D.get_dimension(doc, other).text == "70,5" == _drawn_text(doc, other)[0]
    # A per-dimension "no rounding" override wins over the style and survives a redraw.
    over = doc.entitydb[handle].override()
    over["dimrnd"] = 0.0
    over.commit()
    D.update_dimensions(doc, [handle, other])
    assert D.get_dimension(doc, handle).text == "70,71" == _drawn_text(doc, handle)[0]
    assert D.get_dimension(doc, other).text == "70,5" == _drawn_text(doc, other)[0]


def test_default_style_falls_back_to_standard_and_creates_it() -> None:
    d = ezdxf.new("R2010")
    d.header["$DIMSTYLE"] = "不存在"
    assert D.get_dimension(d, D.add_aligned_dimension(d, (0, 0), (5, 0), 1)).style == "Standard"
    d.dimstyles.remove("Standard")
    assert D.get_dimension(d, D.add_aligned_dimension(d, (0, 0), (5, 0), 1)).style == "Standard"
    d2 = ezdxf.new("R2010", setup=True)
    d2.header["$DIMSTYLE"] = "EZ_RADIUS"
    assert D.get_dimension(d2, D.add_radius_dimension(d2, (0, 0), 5)).style == "EZ_RADIUS"
    named = D.add_linear_dimension(d2, (0, 0), (5, 0), distance=1, style="ezdxf")
    assert D.get_dimension(d2, named).style == "EZDXF"


def test_dimension_in_block_definition(doc: Any) -> None:
    doc.blocks.new("零件")
    handle = D.add_linear_dimension(doc, (0, 0), (8, 0), distance=2, block="零件")
    (info,) = D.list_dimensions(doc, block="零件")
    assert info.handle == handle and info.layout == "零件" and info.measurement == 8.0
    assert D.list_dimensions(doc) == []
    with pytest.raises(KeyError, match="No such block"):
        D.add_linear_dimension(doc, (0, 0), (8, 0), distance=2, block="無")


def test_r12_drawing_supports_dimensions_but_not_decimals() -> None:
    d = ezdxf.new("R12")
    linear = D.add_linear_dimension(d, (0, 0), (10, 0), distance=2, text="長 <>", text_height=3)
    radius = D.add_radius_dimension(d, (0, 0), 5)
    assert D.get_dimension(d, linear).text == "長 10" == _drawn_text(d, linear)[0]
    assert D.get_dimension(d, radius).text == "R5"
    with pytest.raises(ValueError, match="require DXF R2000"):
        D.add_linear_dimension(d, (0, 0), (10, 0), distance=2, decimals=1)
    with pytest.raises(ValueError, match="require DXF R2000"):
        D.add_radius_dimension(d, (0, 0), 5, decimal_separator=".")
    assert len(D.list_dimensions(d)) == 2


# -- editing ---------------------------------------------------------------------


def test_set_dimension_text_redraws_and_resets(doc: Any) -> None:
    handle = D.add_linear_dimension(doc, (0, 0), (10, 0), distance=2)
    assert len(_dim_blocks(doc)) == 1
    info = D.set_dimension_text(doc, handle, "約 <> 公尺")
    assert info.text == "約 10 公尺" and info.override == "約 <> 公尺"
    assert _drawn_text(doc, handle) == ["約 10 公尺"]
    assert len(_dim_blocks(doc)) == 1  # the old geometry block is removed
    fixed = D.set_dimension_text(doc, handle, "固定")
    assert fixed.text == "固定" and fixed.measurement == 10.0
    reset = D.set_dimension_text(doc, handle, None)
    assert reset.text == "10" and reset.override is None
    assert _drawn_text(doc, handle) == ["10"]
    with pytest.raises(ValueError, match="single line"):
        D.set_dimension_text(doc, handle, "a\nb")
    with pytest.raises(ValueError, match="not a DIMENSION"):
        D.set_dimension_text(doc, E.select(doc, types="CIRCLE")[0], "x")


def test_locked_layer_blocks_edit_but_not_add(doc: Any) -> None:
    handle = D.add_linear_dimension(doc, (0, 0), (10, 0), distance=2, layer="鎖")
    with pytest.raises(ValueError, match="locked layer"):
        D.set_dimension_text(doc, handle, "x")
    with pytest.raises(ValueError, match="locked layer"):
        D.delete_dimensions(doc, handle)
    with pytest.raises(ValueError, match="locked layer"):
        D.update_dimensions(doc, [handle])
    assert D.update_dimensions(doc) == []  # locked dimensions are skipped
    assert D.get_dimension(doc, handle).text == "10"


def test_update_dimensions_after_scale(doc: Any) -> None:
    a = D.add_linear_dimension(doc, (0, 0), (10, 0), distance=2)
    b = D.add_radius_dimension(doc, (0, 0), 5)
    E.scale_entities(doc, [a, b], 3, base=(0, 0))
    assert D.get_dimension(doc, a).measurement == _approx(30.0)
    assert D.get_dimension(doc, a).text == "30"
    assert _drawn_text(doc, a) == ["10"]  # the drawing is stale until redrawn
    assert D.update_dimensions(doc, a) == [a]
    assert _drawn_text(doc, a) == ["30"] and _drawn_text(doc, b) == ["R5"]
    assert D.update_dimensions(doc) == [a, b]
    assert _drawn_text(doc, b) == ["R15"]
    assert len(_dim_blocks(doc)) == 2
    with pytest.raises(KeyError):
        D.update_dimensions(doc, [a, "FFFF"])
    with pytest.raises(ValueError, match="not a DIMENSION"):
        D.update_dimensions(doc, [a, E.select(doc, types="CIRCLE")[0]])
    assert not doc.audit().has_errors


def test_delete_dimensions_removes_geometry_blocks(doc: Any) -> None:
    a = D.add_linear_dimension(doc, (0, 0), (10, 0), distance=2)
    b = D.add_aligned_dimension(doc, (0, 0), (3, 4), 1)
    circle = E.select(doc, types="CIRCLE")[0]
    with pytest.raises(ValueError, match="not a DIMENSION"):
        D.delete_dimensions(doc, [a, circle])
    assert len(D.list_dimensions(doc)) == 2
    assert D.delete_dimensions(doc, [a, a.lower()]) == 1
    assert [d.handle for d in D.list_dimensions(doc)] == [b]
    assert len(_dim_blocks(doc)) == 1
    assert D.delete_dimensions(doc, b) == 1
    assert _dim_blocks(doc) == [] and D.list_dimensions(doc) == []
    assert E.select(doc) == [circle]
    assert not doc.audit().has_errors


def test_copied_dimension_shares_no_geometry_after_edit(doc: Any) -> None:
    a = D.add_linear_dimension(doc, (0, 0), (10, 0), distance=2)
    (copy,) = E.copy_entities(doc, [a], 0, 20)
    shared = doc.entitydb[a].dxf.geometry == doc.entitydb[copy].dxf.geometry
    D.set_dimension_text(doc, copy, "副本 <>")
    assert _drawn_text(doc, a) == ["10"] and _drawn_text(doc, copy) == ["副本 10"]
    D.delete_dimensions(doc, copy)
    assert _drawn_text(doc, a) == ["10"]
    assert not shared or len(_dim_blocks(doc)) == 1
    assert not doc.audit().has_errors


# -- document API ----------------------------------------------------------------


def test_document_methods_and_metadata_refresh() -> None:
    document = DxfDocument(ezdxf.new("R2018"))
    circle = document.add_circle((0, 0), 5)
    linear = document.add_linear_dimension((0, 0), (10, 0), distance=3)
    aligned = document.add_aligned_dimension((0, 0), (3, 4), 1, text="斜 <>")
    radius = document.add_radius_dimension(entity=circle, angle=30)
    diameter = document.add_diameter_dimension((0, 0), 5)
    assert document.entity_counts() == {"CIRCLE": 1, "DIMENSION": 4}
    assert [d.kind for d in document.dimensions()] == ["linear", "aligned", "radius", "diameter"]
    assert [d.handle for d in document.dimensions(kinds="radius")] == [radius]
    assert document.dimension(aligned).text == "斜 5"
    assert document.dimension_styles() == ["Standard"]
    assert document.set_dimension_text(linear, "x").text == "x"
    document.scale_entities([diameter], 2, base=(0, 0))
    assert document.update_dimensions([diameter]) == [diameter]
    assert document.dimension(diameter).text == "Ø20"
    assert len(document.update_dimensions()) == 4
    assert document.delete_dimensions([linear, aligned]) == 2
    assert document.entity_counts() == {"CIRCLE": 1, "DIMENSION": 2}
