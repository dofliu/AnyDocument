"""Tests for PPTX text boxes, formatting, pictures, shapes and geometry (T071)."""

from __future__ import annotations

import io
from pathlib import Path

import pytest
from PIL import Image
from pptx import Presentation
from pptx.util import Inches

from anydoc.core.errors import AnyDocError
from anydoc.core.registry import registry
from anydoc.external import convert_with_libreoffice
from anydoc.formats.pptx import PptxDocument, PptxHandler, ShapeFormat, ShapeInfo
from anydoc.formats.pptx.shapes import SHAPE_KINDS, to_emu

CM = 360000


@pytest.fixture(autouse=True)
def _handler() -> None:
    registry.register(PptxHandler, replace=True)


def _doc() -> PptxDocument:
    doc = PptxHandler().create()
    doc.add_slide("Title and Content", title="標題", body=["第一點", "第二點"])
    doc.add_slide("Blank")
    return doc


def _reopen(doc: PptxDocument) -> PptxDocument:
    return PptxHandler().open(doc.to_bytes())


def _png(width: int = 200, height: int = 100) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (width, height), (200, 30, 30)).save(buf, "PNG")
    return buf.getvalue()


def _shape(doc: PptxDocument, slide: int, shape_id: int) -> ShapeInfo:
    return next(s for s in doc.slide(slide).shapes if s.shape_id == shape_id)


# -- lengths -------------------------------------------------------------------


def test_to_emu_units() -> None:
    assert to_emu(914400) == 914400
    assert to_emu(100.4) == 100
    assert to_emu("1in") == 914400
    assert to_emu("2.5cm") == 900000
    assert to_emu("10 mm") == 360000
    assert to_emu("72pt") == 914400
    assert to_emu("5EMU") == 5
    for bad in ("abc", "3px", "", "cm"):
        with pytest.raises(ValueError):
            to_emu(bad)
    with pytest.raises(ValueError):
        to_emu(True)


# -- text boxes ----------------------------------------------------------------


def test_add_textbox_position_and_text_roundtrip() -> None:
    doc = _doc()
    sid = doc.add_textbox(
        1, ["中文文字方塊", "第二段"], left="2cm", top="3cm", width="10cm", height="4cm"
    )
    again = _reopen(doc)
    info = _shape(again, 1, sid)
    assert info.kind == "text"
    assert info.text == "中文文字方塊\n第二段"
    assert (info.left, info.top, info.width, info.height) == (2 * CM, 3 * CM, 10 * CM, 4 * CM)
    assert "中文文字方塊" in again.text()
    fmt = again.shape_format(1, sid)
    assert fmt.word_wrap is True
    assert [p.text for p in fmt.paragraphs] == ["中文文字方塊", "第二段"]


def test_add_textbox_with_formatting() -> None:
    doc = _doc()
    sid = doc.add_textbox(
        1,
        "格式",
        left=0,
        top=0,
        width="5cm",
        height="2cm",
        bold=True,
        italic=True,
        underline=True,
        size=28,
        color="#FF0000",
        font="Noto Sans CJK TC",
        align="center",
        anchor="middle",
        fill="FFFF00",
        line_color="0000FF",
        line_width=2,
    )
    fmt = _reopen(doc).shape_format(1, sid)
    assert isinstance(fmt, ShapeFormat)
    run = fmt.paragraphs[0].runs[0]
    assert (run.bold, run.italic, run.underline) == (True, True, True)
    assert run.size == 28
    assert run.color == "FF0000"
    assert run.font == "Noto Sans CJK TC"
    assert fmt.paragraphs[0].align == "center"
    assert fmt.anchor == "middle"
    assert (fmt.fill, fmt.line_color, fmt.line_width) == ("FFFF00", "0000FF", 2.0)
    # East Asian typeface is written too, so CJK glyphs use the requested font.
    xml = doc.native.slides[1].shapes[-1]._element.xml
    assert '<a:ea typeface="Noto Sans CJK TC"/>' in xml


def test_textbox_invalid_arguments() -> None:
    doc = _doc()
    box = {"left": 0, "top": 0, "width": "1cm", "height": "1cm"}
    with pytest.raises(IndexError):
        doc.add_textbox(9, "x", **box)
    with pytest.raises(ValueError):
        doc.add_textbox(1, "x", left=0, top=0, width=0, height="1cm")
    with pytest.raises(ValueError):
        doc.add_textbox(1, "x", color="red", **box)
    with pytest.raises(ValueError):
        doc.add_textbox(1, "x", align="middle", **box)
    with pytest.raises(ValueError):
        doc.add_textbox(1, "x", size=0, **box)
    with pytest.raises(ValueError):
        doc.add_textbox(1, "x", anchor="center", **box)


# -- text editing and formatting -----------------------------------------------


def test_set_text_on_placeholder_and_metadata_refresh() -> None:
    doc = _doc()
    title = doc.native.slides[0].shapes.title.shape_id
    doc.set_shape_text(0, title, "新標題")
    assert doc.slide(0).title == "新標題"
    assert doc.parts()[0].name == "新標題"
    body = next(s for s in doc.slide(0).shapes if s.shape_id != title).shape_id
    doc.set_shape_text(0, body, ["甲", "乙", "丙"])
    assert _shape(_reopen(doc), 0, body).text == "甲\n乙\n丙"
    doc.set_shape_text(0, body, [])
    assert _shape(doc, 0, body).text == ""


def test_add_paragraph_with_level_and_format() -> None:
    doc = _doc()
    sid = doc.add_textbox(1, "第一段", left=0, top=0, width="8cm", height="4cm")
    idx = doc.add_shape_paragraph(
        1, sid, "子項目", level=1, bold=True, color="00AA00", align="right", size=14
    )
    assert idx == 1
    fmt = _reopen(doc).shape_format(1, sid)
    para = fmt.paragraphs[1]
    assert (para.text, para.level, para.align) == ("子項目", 1, "right")
    assert para.runs[0].bold is True
    assert para.runs[0].color == "00AA00"
    assert para.runs[0].size == 14
    assert fmt.paragraphs[0].runs[0].bold is None
    with pytest.raises(ValueError):
        doc.add_shape_paragraph(1, sid, "x", level=9)


def test_format_text_whole_shape_and_single_paragraph() -> None:
    doc = _doc()
    sid = doc.add_textbox(1, ["一", "二", "三"], left=0, top=0, width="8cm", height="4cm")
    doc.format_text(1, sid, size=20, font="Arial")
    doc.format_text(1, sid, paragraph=1, bold=True, color="123456", align="justify", level=2)
    doc.format_text(1, sid, anchor="bottom", word_wrap=False, auto_size="shape")
    fmt = _reopen(doc).shape_format(1, sid)
    assert [p.runs[0].size for p in fmt.paragraphs] == [20, 20, 20]
    assert [p.runs[0].font for p in fmt.paragraphs] == ["Arial"] * 3
    assert [p.runs[0].bold for p in fmt.paragraphs] == [None, True, None]
    assert [p.runs[0].color for p in fmt.paragraphs] == [None, "123456", None]
    assert [p.align for p in fmt.paragraphs] == [None, "justify", None]
    assert [p.level for p in fmt.paragraphs] == [0, 2, 0]
    assert fmt.anchor == "bottom"
    assert fmt.word_wrap is False
    # formatting can also be switched back off
    doc.format_text(1, sid, paragraph=1, bold=False, underline=False)
    run = doc.shape_format(1, sid).paragraphs[1].runs[0]
    assert (run.bold, run.underline) == (False, False)


def test_format_text_errors() -> None:
    doc = _doc()
    sid = doc.add_textbox(1, "x", left=0, top=0, width="2cm", height="1cm")
    with pytest.raises(IndexError):
        doc.format_text(1, sid, paragraph=3, bold=True)
    with pytest.raises(ValueError):
        doc.format_text(1, sid, level=10)
    with pytest.raises(ValueError):
        doc.format_text(1, sid, auto_size="huge")
    with pytest.raises(AnyDocError):
        doc.format_text(1, 9999, bold=True)
    pic = doc.add_picture(1, _png(), left=0, top=0)
    with pytest.raises(AnyDocError):
        doc.set_shape_text(1, pic, "不可")


# -- pictures ------------------------------------------------------------------


def test_add_picture_from_bytes_native_size() -> None:
    doc = _doc()
    sid = doc.add_picture(1, _png(200, 100), left="1cm", top="2cm")
    info = _shape(_reopen(doc), 1, sid)
    assert info.kind == "picture"
    assert (info.left, info.top) == (CM, 2 * CM)
    assert info.width == 2 * info.height


def test_add_picture_from_path_keeps_aspect_ratio(tmp_path: Path) -> None:
    path = tmp_path / "圖.png"
    path.write_bytes(_png(300, 100))
    doc = _doc()
    by_width = doc.add_picture(1, path, left=0, top=0, width="6cm")
    by_height = doc.add_picture(1, str(path), left=0, top="5cm", height="1cm")
    both = doc.add_picture(1, path, left=0, top="8cm", width="4cm", height="4cm")
    again = _reopen(doc)
    assert (_shape(again, 1, by_width).width, _shape(again, 1, by_width).height) == (6 * CM, 2 * CM)
    assert _shape(again, 1, by_height).width == 3 * CM
    assert (_shape(again, 1, both).width, _shape(again, 1, both).height) == (4 * CM, 4 * CM)
    # the image bytes are embedded in the package
    images = [s.image.blob for s in again.native.slides[1].shapes if hasattr(s, "image")]
    assert images and all(blob == path.read_bytes() for blob in images)


def test_add_picture_errors(tmp_path: Path) -> None:
    doc = _doc()
    with pytest.raises(AnyDocError):
        doc.add_picture(1, tmp_path / "missing.png", left=0, top=0)
    with pytest.raises(AnyDocError):
        doc.add_picture(1, b"not an image", left=0, top=0)
    with pytest.raises(ValueError):
        doc.add_picture(1, _png(), left=0, top=0, width=-5)


# -- shapes --------------------------------------------------------------------


@pytest.mark.parametrize("kind", sorted(SHAPE_KINDS))
def test_add_every_shape_kind(kind: str) -> None:
    doc = _doc()
    sid = doc.add_shape(1, kind, left="1cm", top="1cm", width="4cm", height="3cm")
    info = _shape(_reopen(doc), 1, sid)
    assert (info.left, info.top, info.width, info.height) == (CM, CM, 4 * CM, 3 * CM)


def test_add_shape_with_text_and_style() -> None:
    doc = _doc()
    sid = doc.add_shape(
        1,
        "Rounded-Rectangle",
        left=0,
        top=0,
        width="6cm",
        height="2cm",
        text="按鈕",
        fill="#336699",
        line_color="000000",
        line_width=1.5,
    )
    again = _reopen(doc)
    assert _shape(again, 1, sid).text == "按鈕"
    fmt = again.shape_format(1, sid)
    assert (fmt.fill, fmt.line_color, fmt.line_width) == ("336699", "000000", 1.5)
    with pytest.raises(ValueError):
        doc.add_shape(1, "blob", left=0, top=0, width=1, height=1)
    with pytest.raises(ValueError):
        doc.add_shape(1, "oval", left=0, top=0, width=1, height=-1)


def test_style_shape_change_and_remove() -> None:
    doc = _doc()
    sid = doc.add_shape(1, "oval", left=0, top=0, width="3cm", height="3cm")
    assert doc.shape_format(1, sid).fill is None  # theme fill: no explicit colour
    doc.style_shape(1, sid, fill="ABCDEF", line_color="112233", line_width=3)
    fmt = _reopen(doc).shape_format(1, sid)
    assert (fmt.fill, fmt.line_color, fmt.line_width) == ("ABCDEF", "112233", 3.0)
    doc.style_shape(1, sid, fill="none", line_color="none")
    fmt = _reopen(doc).shape_format(1, sid)
    assert (fmt.fill, fmt.line_color) == (None, None)
    with pytest.raises(ValueError):
        doc.style_shape(1, sid, line_width=-1)
    with pytest.raises(ValueError):
        doc.style_shape(1, sid, fill="12345")


def test_add_line() -> None:
    doc = _doc()
    sid = doc.add_line(1, x1="1cm", y1="1cm", x2="6cm", y2="3cm", color="FF00FF", width=2.25)
    again = _reopen(doc)
    info = _shape(again, 1, sid)
    assert (info.left, info.top, info.width, info.height) == (CM, CM, 5 * CM, 2 * CM)
    fmt = again.shape_format(1, sid)
    assert (fmt.line_color, fmt.line_width) == ("FF00FF", 2.25)
    assert fmt.paragraphs == []


# -- geometry ------------------------------------------------------------------


def test_set_geometry_move_resize_rotate() -> None:
    doc = _doc()
    sid = doc.add_shape(1, "rectangle", left="1cm", top="1cm", width="4cm", height="2cm")
    doc.set_geometry(1, sid, left="5cm", top=Inches(1))
    fmt = doc.shape_format(1, sid)
    assert (fmt.left, fmt.top, fmt.width, fmt.height) == (5 * CM, 914400, 4 * CM, 2 * CM)
    doc.set_geometry(1, sid, width="8cm", height="1cm", rotation=45)
    fmt = _reopen(doc).shape_format(1, sid)
    assert (fmt.left, fmt.width, fmt.height, fmt.rotation) == (5 * CM, 8 * CM, CM, 45.0)
    doc.set_geometry(1, sid, rotation=-90)
    assert doc.shape_format(1, sid).rotation == 270.0
    with pytest.raises(ValueError):
        doc.set_geometry(1, sid, width=0)
    with pytest.raises(AnyDocError):
        doc.set_geometry(1, 12345, left=0)


def test_set_geometry_on_placeholder_and_picture() -> None:
    doc = _doc()
    title = doc.native.slides[0].shapes.title.shape_id
    doc.set_geometry(0, title, left="1cm", top="1cm", width="20cm", height="3cm")
    info = _shape(_reopen(doc), 0, title)
    assert (info.left, info.top, info.width, info.height) == (CM, CM, 20 * CM, 3 * CM)
    pic = doc.add_picture(1, _png(), left=0, top=0)
    doc.set_geometry(1, pic, left="2cm", width="5cm", height="5cm", rotation=30)
    fmt = _reopen(doc).shape_format(1, pic)
    assert (fmt.left, fmt.width, fmt.height, fmt.rotation) == (2 * CM, 5 * CM, 5 * CM, 30.0)


# -- delete and stacking order -------------------------------------------------


def test_delete_shape() -> None:
    doc = _doc()
    a = doc.add_textbox(1, "甲", left=0, top=0, width="2cm", height="1cm")
    b = doc.add_textbox(1, "乙", left=0, top="2cm", width="2cm", height="1cm")
    doc.delete_shape(1, a)
    again = _reopen(doc)
    assert [s.shape_id for s in again.slide(1).shapes] == [b]
    assert again.slide_text(1) == "乙"
    with pytest.raises(AnyDocError):
        doc.delete_shape(1, a)


def test_set_z_order() -> None:
    doc = _doc()
    ids = [
        doc.add_shape(1, "rectangle", left=0, top=0, width="2cm", height="2cm") for _ in range(3)
    ]
    a, b, c = ids

    def order() -> list[int]:
        return [s.shape_id for s in _reopen(doc).slide(1).shapes]

    assert order() == [a, b, c]
    doc.set_z_order(1, a, "front")
    assert order() == [b, c, a]
    doc.set_z_order(1, a, "back")
    assert order() == [a, b, c]
    doc.set_z_order(1, c, 1)
    assert order() == [a, c, b]
    doc.set_z_order(1, a, 2)
    assert order() == [c, b, a]
    with pytest.raises(IndexError):
        doc.set_z_order(1, a, 5)
    with pytest.raises(ValueError):
        doc.set_z_order(1, a, "middle")


def test_shapes_inside_group_are_addressable() -> None:
    doc = _doc()
    slide = doc.native.slides[1]
    group = slide.shapes.add_group_shape()
    inner = group.shapes.add_textbox(0, 0, Inches(2), Inches(1))
    inner.text_frame.text = "群組內"
    doc.set_shape_text(1, inner.shape_id, "已修改")
    doc.format_text(1, inner.shape_id, bold=True)
    again = _reopen(doc)
    assert again.slide_text(1) == "已修改"
    assert again.shape_format(1, inner.shape_id).paragraphs[0].runs[0].bold is True
    with pytest.raises(AnyDocError):
        doc.set_z_order(1, inner.shape_id, "front")
    doc.delete_shape(1, inner.shape_id)
    assert _reopen(doc).slide_text(1) == ""


# -- existing files and external round trip ------------------------------------


def test_edit_existing_file_and_save(tmp_path: Path) -> None:
    prs = Presentation()
    slide = prs.slides.add_slide(prs.slide_layouts[5])
    slide.shapes.title.text = "原始"
    src = tmp_path / "in.pptx"
    prs.save(str(src))

    doc = PptxHandler().open(src)
    box = doc.add_textbox(0, "新增", left="1cm", top="5cm", width="6cm", height="2cm", size=18)
    pic = doc.add_picture(0, _png(), left="8cm", top="5cm", width="4cm")
    star = doc.add_shape(
        0, "star", left="14cm", top="5cm", width="3cm", height="3cm", fill="FFD700"
    )
    out = doc.save(tmp_path / "out.pptx")

    again = PptxHandler().open(out)
    kinds = {s.shape_id: s.kind for s in again.slide(0).shapes}
    assert kinds[box] == "text"
    assert kinds[pic] == "picture"
    assert again.shape_format(0, star).fill == "FFD700"
    assert again.slide(0).title == "原始"
    assert len({box, pic, star}) == 3


@pytest.mark.requires("libreoffice")
def test_libreoffice_roundtrip_keeps_text_and_shapes(tmp_path: Path) -> None:
    doc = _doc()
    doc.add_textbox(
        1, "往返文字方塊", left="2cm", top="2cm", width="10cm", height="2cm", size=24, bold=True
    )
    doc.add_picture(1, _png(), left="2cm", top="6cm", width="4cm")
    doc.add_shape(1, "oval", left="8cm", top="6cm", width="4cm", height="3cm", text="橢圓")
    doc.add_line(1, x1="2cm", y1="12cm", x2="12cm", y2="12cm", color="FF0000", width=2)
    src = tmp_path / "shapes.pptx"
    src.write_bytes(doc.to_bytes())

    pdf = convert_with_libreoffice(src, "pdf", tmp_path / "pdf")
    assert pdf.read_bytes().startswith(b"%PDF")

    back = PptxHandler().open(convert_with_libreoffice(src, "pptx", tmp_path / "pptx"))
    text = back.slide_text(1)
    assert "往返文字方塊" in text
    assert "橢圓" in text
    assert "picture" in {s.kind for s in back.slide(1).shapes}
    assert len(back.slide(1).shapes) >= 4
