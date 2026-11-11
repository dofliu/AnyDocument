"""Tests for PPTX layouts, slide masters and themes (T073)."""

from __future__ import annotations

from pathlib import Path

import pytest

from anydoc.core.errors import AnyDocError
from anydoc.core.registry import registry
from anydoc.external import convert_with_libreoffice
from anydoc.formats.pptx import (
    LayoutInfo,
    MasterInfo,
    PptxDocument,
    PptxHandler,
    TextStyle,
    ThemeInfo,
    themes,
)
from anydoc.formats.pptx.themes import SLIDE_SIZES, THEME_COLORS

BOX = {"left": "2cm", "top": "2cm", "width": "10cm", "height": "3cm"}


@pytest.fixture(autouse=True)
def _handler() -> None:
    registry.register(PptxHandler, replace=True)


def _doc() -> PptxDocument:
    doc = PptxHandler().create()
    doc.add_slide("Title and Content", title="標題", body=["第一點", "第二點"])
    return doc


def _reopen(doc: PptxDocument) -> PptxDocument:
    return PptxHandler().open(doc.to_bytes())


def _placeholders(doc: PptxDocument, slide: int = 0) -> list[tuple[int, str, str]]:
    return [
        (
            int(ph.placeholder_format.idx),
            str(ph.placeholder_format.type).split(" ")[0],
            ph.text_frame.text,
        )
        for ph in doc.native.slides[slide].placeholders
    ]


# -- layouts and masters -----------------------------------------------------------


def test_layout_info_lists_placeholders_and_users() -> None:
    doc = _doc()
    doc.add_slide("Blank")
    infos = doc.layout_info()
    assert all(isinstance(i, LayoutInfo) for i in infos)
    assert [i.name for i in infos] == doc.layouts()
    content = infos[1]
    assert content.name == "Title and Content"
    assert content.master == 0
    assert content.used_by == [0]
    assert [(p.idx, p.type) for p in content.placeholders[:2]] == [(0, "title"), (1, "object")]
    assert next(i for i in infos if i.name == "Blank").used_by == [1]
    assert infos[0].used_by == []


def test_masters_info() -> None:
    masters = _doc().masters()
    assert len(masters) == 1
    assert isinstance(masters[0], MasterInfo)
    assert masters[0].theme == "Office Theme"
    assert "Title Slide" in masters[0].layouts
    assert {p.type for p in masters[0].placeholders} >= {"title", "body"}


def test_find_layout_by_index_and_name() -> None:
    prs = _doc().native
    assert themes.find_layout(prs, 6).name == "Blank"
    assert themes.find_layout(prs, "blank").name == "Blank"
    for bad in (99, -1, "不存在", True):
        with pytest.raises(ValueError, match="No such layout"):
            themes.find_layout(prs, bad)


def test_set_slide_layout_rebinds_placeholders_and_roundtrips() -> None:
    doc = _doc()
    doc.set_slide_layout(0, "Section Header")
    again = _reopen(doc)
    assert again.slide(0).layout == "Section Header"
    assert _placeholders(again) == [(0, "TITLE", "標題"), (1, "BODY", "第一點\n第二點")]
    # geometry now comes from the new layout
    layout = again.native.slides[0].slide_layout
    body = again.native.slides[0].placeholders[1]
    assert body.top == layout.placeholders[1].top
    assert again.layout_info()[2].used_by == [0]
    assert again.metadata.page_count == 1


def test_set_slide_layout_to_title_slide_changes_placeholder_types() -> None:
    doc = _doc()
    doc.set_slide_layout(0, 0)
    assert _placeholders(_reopen(doc)) == [
        (0, "CENTER_TITLE", "標題"),
        (1, "SUBTITLE", "第一點\n第二點"),
    ]


def test_set_slide_layout_adds_missing_placeholders() -> None:
    doc = PptxHandler().create()
    doc.add_slide("Title Only", title="只有標題")
    doc.set_slide_layout(0, "Two Content")
    again = _reopen(doc)
    assert _placeholders(again) == [(0, "TITLE", "只有標題"), (1, "OBJECT", ""), (2, "OBJECT", "")]


def test_set_slide_layout_removes_empty_and_keeps_filled_orphans() -> None:
    doc = _doc()
    before = doc.native.slides[0].placeholders[1]
    geometry = (before.left, before.top, before.width, before.height)
    doc.set_slide_layout(0, "Blank")
    again = _reopen(doc)
    assert again.slide(0).layout == "Blank"
    kept = again.native.slides[0].placeholders
    assert [ph.text_frame.text for ph in kept] == ["標題", "第一點\n第二點"]
    body = kept[1]
    assert (body.left, body.top, body.width, body.height) == geometry
    assert "第一點" in again.text()

    empty = PptxHandler().create()
    empty.add_slide("Title and Content", title="保留")
    empty.set_slide_layout(0, "Title Only")
    assert _placeholders(_reopen(empty)) == [(0, "TITLE", "保留")]


def test_set_slide_layout_keep_positions() -> None:
    doc = _doc()
    body = doc.native.slides[0].placeholders[1]
    geometry = (body.left, body.top, body.width, body.height)
    doc.set_slide_layout(0, "Section Header", keep_positions=True)
    moved = _reopen(doc).native.slides[0].placeholders[1]
    assert (moved.left, moved.top, moved.width, moved.height) == geometry


def test_set_slide_layout_keeps_other_shapes_and_noop() -> None:
    doc = _doc()
    box = doc.add_textbox(0, "自由文字", **BOX)
    doc.set_slide_layout(0, "Title and Content")  # same layout: nothing happens
    doc.set_slide_layout(0, "Title Only")
    again = _reopen(doc)
    assert any(s.shape_id == box and s.text == "自由文字" for s in again.slide(0).shapes)


def test_set_slide_layout_errors() -> None:
    doc = _doc()
    with pytest.raises(IndexError):
        doc.set_slide_layout(5, "Blank")
    with pytest.raises(ValueError, match="No such layout"):
        doc.set_slide_layout(0, "不存在")


def test_rename_layout() -> None:
    doc = _doc()
    doc.rename_layout("Title and Content", "標題與內容")
    again = _reopen(doc)
    assert again.slide(0).layout == "標題與內容"
    assert "標題與內容" in again.layouts()
    with pytest.raises(ValueError, match="already in use"):
        again.rename_layout("標題與內容", "blank")
    with pytest.raises(ValueError, match="empty"):
        again.rename_layout(0, "  ")
    again.rename_layout("標題與內容", "標題與內容")  # renaming to itself is allowed


def test_remove_unused_layouts() -> None:
    doc = _doc()
    doc.add_slide("Blank")
    removed = doc.remove_unused_layouts()
    assert len(removed) == 9
    again = _reopen(doc)
    assert again.layouts() == ["Title and Content", "Blank"]
    assert [s.layout for s in again.slides()] == ["Title and Content", "Blank"]
    assert again.remove_unused_layouts() == []

    empty = PptxHandler().create()
    empty.remove_unused_layouts()
    assert len(empty.layouts()) == 1  # a master keeps at least one layout


# -- backgrounds -----------------------------------------------------------------


def test_background_on_slide_layout_and_master() -> None:
    doc = _doc()
    assert doc.get_background(slide=0) is None
    doc.set_background("#1f4e79", slide=0)
    doc.set_background("EEEEEE", layout="Blank")
    doc.set_background("FFF8E7")
    again = _reopen(doc)
    assert again.get_background(slide=0) == "1F4E79"
    assert again.get_background(layout="Blank") == "EEEEEE"
    assert again.get_background(layout=0) is None
    assert again.get_background() == "FFF8E7"
    assert again.get_background(master=0) == "FFF8E7"


def test_background_reset_to_inherited() -> None:
    doc = _doc()
    doc.set_background("000000", slide=0)
    doc.set_background(None, slide=0)
    doc.set_background(None, slide=0)  # already inherited: no error
    assert _reopen(doc).get_background(slide=0) is None
    doc.set_background("123456", layout=1)
    doc.set_background(None, layout=1)
    assert doc.get_background(layout=1) is None


def test_background_errors() -> None:
    doc = _doc()
    with pytest.raises(ValueError, match="only one"):
        doc.set_background("000000", slide=0, master=0)
    with pytest.raises(ValueError, match="Invalid color"):
        doc.set_background("blue", slide=0)
    with pytest.raises(ValueError, match="always needs"):
        doc.set_background(None)
    with pytest.raises(ValueError, match="always needs"):
        doc.set_background(None, master=0)
    with pytest.raises(IndexError):
        doc.set_background("000000", master=3)
    with pytest.raises(IndexError):
        doc.get_background(slide=9)


def test_non_solid_background_reads_as_none() -> None:
    doc = _doc()
    doc.native.slides[0].background.fill.gradient()
    assert doc.get_background(slide=0) is None


# -- master text styles ----------------------------------------------------------


def test_master_text_style_roundtrip() -> None:
    doc = _doc()
    doc.set_master_text_style(
        "title", size=40, bold=True, italic=False, color="C00000", font="Noto Sans CJK TC"
    )
    doc.set_master_text_style("body", level=2, size=20.5, east_asian_font="標楷體")
    again = _reopen(doc)
    title = again.master_text_style("title")
    assert isinstance(title, TextStyle)
    assert title == TextStyle(40.0, True, False, "C00000", "Noto Sans CJK TC", "Noto Sans CJK TC")
    body = again.master_text_style("body", level=2)
    assert body.size == 20.5
    assert body.east_asian_font == "標楷體"
    assert body.color is None
    # untouched level keeps the template's own size
    assert again.master_text_style("body", level=1).size == 32.0


def test_master_text_style_can_be_changed_twice() -> None:
    doc = _doc()
    doc.set_master_text_style("title", color="FF0000", font="Arial")
    doc.set_master_text_style("title", color="00FF00", font="Verdana", east_asian_font="新細明體")
    style = _reopen(doc).master_text_style("title")
    assert (style.color, style.font, style.east_asian_font) == ("00FF00", "Verdana", "新細明體")
    rpr = themes._style_level(doc.native, "title", 1, 0, create=False).find(
        "{http://schemas.openxmlformats.org/drawingml/2006/main}defRPr"
    )
    tags = [child.tag.split("}")[1] for child in rpr]
    assert tags.count("solidFill") == tags.count("latin") == tags.count("ea") == 1
    assert tags.index("solidFill") < tags.index("latin") < tags.index("ea")


def test_master_text_style_creates_missing_structure() -> None:
    doc = _doc()
    root = doc.native.slide_masters[0]._element
    ns = "{http://schemas.openxmlformats.org/presentationml/2006/main}"
    root.remove(root.find(f"{ns}txStyles"))
    assert doc.master_text_style("title") == TextStyle(None, None, None, None, None, None)
    doc.set_master_text_style("other", level=3, size=12)
    doc.set_master_text_style("title", size=36)
    again = _reopen(doc)
    assert again.master_text_style("other", level=3).size == 12.0
    assert again.master_text_style("title").size == 36.0
    assert again.master_text_style("body").size is None
    styles = again.native.slide_masters[0]._element.find(f"{ns}txStyles")
    assert [c.tag.split("}")[1] for c in styles] == ["titleStyle", "otherStyle"]


def test_master_text_style_errors() -> None:
    doc = _doc()
    with pytest.raises(ValueError, match="Unknown text style"):
        doc.set_master_text_style("footer", size=10)
    with pytest.raises(ValueError, match="level"):
        doc.set_master_text_style("body", level=10, size=10)
    with pytest.raises(ValueError, match="positive"):
        doc.set_master_text_style("body", size=0)
    with pytest.raises(ValueError, match="Invalid color"):
        doc.set_master_text_style("body", color="red")
    with pytest.raises(IndexError):
        doc.master_text_style("title", master=2)


# -- themes --------------------------------------------------------------------


def test_theme_info_defaults() -> None:
    info = _doc().theme()
    assert isinstance(info, ThemeInfo)
    assert info.name == "Office Theme"
    assert tuple(info.colors) == THEME_COLORS
    assert info.colors["dk1"] == "000000"  # system colour resolved through lastClr
    assert info.colors["accent1"] == "4F81BD"
    assert info.major_font == info.minor_font == "Calibri"
    assert info.major_east_asian == ""


def test_set_theme_colors_roundtrip() -> None:
    doc = _doc()
    doc.set_theme_colors({"accent1": "#1f4e79", "dk1": "111111"})
    info = _reopen(doc).theme()
    assert info.colors["accent1"] == "1F4E79"
    assert info.colors["dk1"] == "111111"
    assert info.colors["accent2"] == "C0504D"


def test_set_theme_colors_errors() -> None:
    doc = _doc()
    with pytest.raises(ValueError, match="Unknown theme color"):
        doc.set_theme_colors({"accent9": "000000"})
    with pytest.raises(ValueError, match="Invalid color"):
        doc.set_theme_colors({"accent1": "navy"})
    with pytest.raises(IndexError):
        doc.set_theme_colors({"accent1": "000000"}, master=4)
    assert doc.theme().colors["accent1"] == "4F81BD"


def test_set_theme_fonts_roundtrip() -> None:
    doc = _doc()
    doc.set_theme_fonts(major="Arial", major_east_asian="微軟正黑體", minor_east_asian="標楷體")
    again = _reopen(doc)
    info = again.theme()
    assert (info.major_font, info.minor_font) == ("Arial", "Calibri")
    assert (info.major_east_asian, info.minor_east_asian) == ("微軟正黑體", "標楷體")
    blob = themes._theme_part(again.native, 0).blob.decode("utf-8")
    assert '<a:font script="Hant" typeface="微軟正黑體"/>' in blob
    assert '<a:font script="Hant" typeface="標楷體"/>' in blob


def test_set_theme_fonts_errors() -> None:
    doc = _doc()
    with pytest.raises(ValueError, match="at least one"):
        doc.set_theme_fonts()
    with pytest.raises(ValueError, match="empty"):
        doc.set_theme_fonts(major=" ")


def _donor() -> PptxDocument:
    donor = PptxHandler().create()
    donor.set_theme_colors({"accent1": "AA0000", "lt1": "FFFDF5"})
    donor.set_theme_fonts(major="Georgia", minor="Verdana", minor_east_asian="標楷體")
    root = themes._theme_root(themes._theme_part(donor.native, 0))
    root.set("name", "企業主題")
    themes._store_theme(themes._theme_part(donor.native, 0), root)
    return donor


def test_apply_theme_from_path_bytes_and_document(tmp_path: Path) -> None:
    donor = _donor()
    path = tmp_path / "donor.pptx"
    path.write_bytes(donor.to_bytes())
    for source in (path, str(path), donor.to_bytes(), donor, donor.native):
        doc = _doc()
        result = doc.apply_theme(source)
        info = _reopen(doc).theme()
        assert info == result
        assert info.name == "企業主題"
        assert info.colors["accent1"] == "AA0000"
        assert info.colors["lt1"] == "FFFDF5"
        assert (info.major_font, info.minor_font) == ("Georgia", "Verdana")
        assert info.minor_east_asian == "標楷體"
        assert _reopen(doc).slide(0).title == "標題"


def test_apply_theme_partial() -> None:
    doc = _doc()
    info = doc.apply_theme(_donor(), fonts=False, effects=False)
    assert info.colors["accent1"] == "AA0000"
    assert info.major_font == "Calibri"
    assert info.name == "Office Theme"
    info = _doc().apply_theme(_donor(), colors=False)
    assert info.colors["accent1"] == "4F81BD"
    assert info.major_font == "Georgia"


def test_apply_theme_skips_format_scheme_with_image_references() -> None:
    donor = _donor()
    part = themes._theme_part(donor.native, 0)
    root = themes._theme_root(part)
    a = "{http://schemas.openxmlformats.org/drawingml/2006/main}"
    r = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
    fmt = root.find(f"{a}themeElements/{a}fmtScheme")
    fmt.set("name", "含圖片")
    fmt.append(fmt.makeelement(f"{a}blip", {f"{r}embed": "rId1"}))
    themes._store_theme(part, root)
    doc = _doc()
    doc.apply_theme(donor)
    blob = themes._theme_part(_reopen(doc).native, 0).blob.decode("utf-8")
    assert "含圖片" not in blob
    assert "AA0000" in blob


def test_apply_theme_errors(tmp_path: Path) -> None:
    doc = _doc()
    with pytest.raises(AnyDocError, match="not found"):
        doc.apply_theme(tmp_path / "missing.pptx")
    with pytest.raises(AnyDocError, match="Invalid PPTX"):
        doc.apply_theme(b"not a pptx")
    bad = tmp_path / "bad.pptx"
    bad.write_bytes(b"PK\x03\x04 broken")
    with pytest.raises(AnyDocError, match="Invalid PPTX"):
        doc.apply_theme(bad)


def test_broken_theme_reports_errors() -> None:
    doc = _doc()
    part = themes._theme_part(doc.native, 0)
    part._blob = b"<not-xml"
    with pytest.raises(AnyDocError, match="Cannot parse theme"):
        doc.theme()
    part._blob = (
        b'<a:theme xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" name="x"/>'
    )
    with pytest.raises(AnyDocError, match="themeElements"):
        doc.theme()
    part._blob = (
        b'<a:theme xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" name="x">'
        b"<a:themeElements/></a:theme>"
    )
    assert doc.theme() == ThemeInfo("x", {}, "", "", "", "")
    with pytest.raises(AnyDocError, match="color scheme"):
        doc.set_theme_colors({"accent1": "000000"})
    with pytest.raises(AnyDocError, match="font scheme"):
        doc.set_theme_fonts(major="Arial")


# -- slide size ----------------------------------------------------------------


def test_parse_slide_size() -> None:
    assert themes.parse_slide_size("16:9") == SLIDE_SIZES["16:9"]
    assert themes.parse_slide_size(" A4 ") == SLIDE_SIZES["a4"]
    assert themes.parse_slide_size(("25.4cm", "10in")) == (9144000, 9144000)
    assert themes.parse_slide_size([1000, 2000]) == (1000, 2000)
    for bad in ("21:9", (1, 2, 3), (0, 100), ("1cm", "-1cm")):
        with pytest.raises(ValueError, match="[Ss]lide size"):
            themes.parse_slide_size(bad)


def test_set_slide_size_scales_shapes() -> None:
    doc = PptxHandler().open(_fresh_4x3())
    box = doc.add_textbox(0, "方塊", left=914400, top=914400, width=1828800, height=914400)
    layout_title = doc.native.slides[0].slide_layout.placeholders[0]
    old_left, old_width = layout_title.left, layout_title.width
    assert doc.set_slide_size("16:9") == SLIDE_SIZES["16:9"]
    again = _reopen(doc)
    assert again.metadata.extra["slide_width"] == 12192000
    fmt = again.shape_format(0, box)
    ratio = 12192000 / 9144000
    assert (fmt.left, fmt.top, fmt.width, fmt.height) == (
        round(914400 * ratio),
        914400,
        round(1828800 * ratio),
        914400,
    )
    new_title = again.native.slides[0].slide_layout.placeholders[0]
    assert new_title.left == round(old_left * ratio)
    assert new_title.width == round(old_width * ratio)


def test_set_slide_size_without_scaling() -> None:
    doc = PptxHandler().open(_fresh_4x3())
    box = doc.add_textbox(0, "方塊", left=914400, top=914400, width=1828800, height=914400)
    doc.set_slide_size(("20cm", "20cm"), scale=False)
    again = _reopen(doc)
    assert again.metadata.extra["slide_height"] == 7200000
    assert again.shape_format(0, box).left == 914400


def _fresh_4x3() -> bytes:
    import io

    from pptx import Presentation

    prs = Presentation()
    prs.slides.add_slide(prs.slide_layouts[1])
    buf = io.BytesIO()
    prs.save(buf)
    return buf.getvalue()


# -- LibreOffice round trip ------------------------------------------------------


@pytest.mark.requires("libreoffice")
def test_libreoffice_roundtrip_keeps_layout_theme_and_background(tmp_path: Path) -> None:
    doc = _doc()
    doc.add_slide("Title Only", title="第二張")
    doc.set_slide_layout(0, "Section Header")
    doc.set_theme_colors({"accent1": "1F4E79"})
    doc.set_theme_fonts(major="Arial", minor_east_asian="Noto Sans CJK TC")
    doc.set_background("FFF8E7", slide=1)
    doc.set_master_text_style("title", size=40, color="C00000")
    src = tmp_path / "themed.pptx"
    src.write_bytes(doc.to_bytes())

    pdf = convert_with_libreoffice(src, "pdf", tmp_path / "pdf")
    assert pdf.read_bytes().startswith(b"%PDF")

    back = PptxHandler().open(convert_with_libreoffice(src, "pptx", tmp_path / "pptx"))
    assert [s.title for s in back.slides()] == ["標題", "第二張"]
    assert "第一點" in back.slide_text(0)
    assert back.slide(0).layout != back.slide(1).layout
    assert back.get_background(slide=1) == "FFF8E7"
    assert back.theme().colors["accent1"] == "1F4E79"
