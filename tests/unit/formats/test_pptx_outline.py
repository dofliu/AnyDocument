"""Tests for building presentations from an outline and exporting them back (T073)."""

from __future__ import annotations

import io
import json
from pathlib import Path
from typing import Any

import pytest
from PIL import Image
from pptx import Presentation
from typer.testing import CliRunner

from anydoc.cli import app
from anydoc.core.errors import AnyDocError
from anydoc.core.registry import registry
from anydoc.external import convert_with_libreoffice
from anydoc.formats.pptx import PptxDocument, PptxHandler, outline
from anydoc.formats.pptx.themes import SLIDE_SIZES

runner = CliRunner()

OUTLINE: dict[str, Any] = {
    "title": "年度報告",
    "subtitle": "2026 年度",
    "author": "王小明",
    "subject": "營運",
    "keywords": "報告, 年度",
    "theme": {
        "colors": {"accent1": "1F4E79"},
        "fonts": {"major": "Arial", "minor_east_asian": "標楷體"},
    },
    "slides": [
        {
            "title": "議程",
            "bullets": ["第一點", {"text": "子項", "level": 1}, ["巢狀", ["更深"]], "第二點"],
            "notes": "這是備忘稿",
        },
        {"layout": "Two Content", "title": "比較", "placeholders": {"1": ["左側"], "2": "右側"}},
        {"title": "數據", "table": {"data": [["項目", "值"], ["甲", 1], ["乙", 2.5]]}},
        {
            "title": "圖表",
            "bullets": ["說明文字"],
            "chart": {
                "kind": "column",
                "categories": ["Q1", "Q2"],
                "series": {"營收": [10, 20], "成本": [5, 8]},
                "title": "季度",
            },
        },
        {"title": "只有標題", "background": "FFF8E7"},
        {"layout": "Section Header", "title": "章節", "text": "第一行\n第二行"},
        {},
    ],
}


@pytest.fixture(autouse=True)
def _handler() -> None:
    registry.register(PptxHandler, replace=True)


def _build(data: dict[str, Any], **kwargs: Any) -> PptxDocument:
    return PptxHandler().open(PptxHandler().from_outline(data, **kwargs).to_bytes())


def _png(path: Path, size: tuple[int, int]) -> Path:
    Image.new("RGB", size, "red").save(path)
    return path


def _template(tmp_path: Path, *, slides: int = 2) -> Path:
    """A 4:3 template with a custom theme colour, renamed layouts and sample slides."""
    doc = PptxHandler().open(_plain_4x3(slides))
    doc.set_theme_colors({"accent1": "AA0000"})
    doc.rename_layout("Title Slide", "封面")
    doc.rename_layout("Title and Content", "標題與內容")
    doc.rename_layout("Title Only", "僅標題")
    doc.rename_layout("Blank", "空白")
    path = tmp_path / "template.pptx"
    path.write_bytes(doc.to_bytes())
    return path


def _plain_4x3(slides: int) -> bytes:
    prs = Presentation()
    for i in range(slides):
        slide = prs.slides.add_slide(prs.slide_layouts[1])
        slide.shapes.title.text_frame.text = f"範本投影片 {i + 1}"
    buf = io.BytesIO()
    prs.save(buf)
    return buf.getvalue()


# -- building --------------------------------------------------------------------


def test_build_full_outline() -> None:
    doc = _build(OUTLINE)
    slides = doc.slides()
    assert [s.layout for s in slides] == [
        "Title Slide",
        "Title and Content",
        "Two Content",
        "Title and Content",
        "Title and Content",
        "Title Only",
        "Section Header",
        "Blank",
    ]
    assert [s.title for s in slides] == [
        "年度報告",
        "議程",
        "比較",
        "數據",
        "圖表",
        "只有標題",
        "章節",
        None,
    ]
    assert doc.metadata.title == "年度報告"
    assert doc.metadata.author == "王小明"
    assert doc.metadata.extra["subject"] == "營運"
    assert doc.metadata.extra["slide_width"] == SLIDE_SIZES["16:9"][0]
    assert "2026 年度" in doc.slide_text(0)
    assert slides[1].notes == "這是備忘稿"
    assert doc.get_background(slide=5) == "FFF8E7"
    assert doc.theme().colors["accent1"] == "1F4E79"
    assert doc.theme().major_font == "Arial"
    assert doc.theme().minor_east_asian == "標楷體"
    assert slides[7].shapes == []


def test_bullet_levels() -> None:
    doc = _build(OUTLINE)
    body = doc.native.slides[1].placeholders[1]
    assert [(p.text, p.level) for p in body.text_frame.paragraphs] == [
        ("第一點", 0),
        ("子項", 1),
        ("巢狀", 1),
        ("更深", 2),
        ("第二點", 0),
    ]


def test_first_bullet_can_be_indented() -> None:
    doc = _build({"slides": [{"title": "t", "bullets": [{"text": "縮排", "level": 2}]}]})
    paragraph = doc.native.slides[0].placeholders[1].text_frame.paragraphs[0]
    assert (paragraph.text, paragraph.level) == ("縮排", 2)


def test_placeholders_by_index_and_text_lines() -> None:
    doc = _build(OUTLINE)
    two = doc.native.slides[2].placeholders
    assert [(int(p.placeholder_format.idx), p.text_frame.text) for p in two] == [
        (0, "比較"),
        (1, "左側"),
        (2, "右側"),
    ]
    assert doc.native.slides[6].placeholders[1].text_frame.text == "第一行\n第二行"


def test_table_replaces_body_placeholder() -> None:
    doc = _build(OUTLINE)
    shapes = doc.slide(3).shapes
    assert [s.kind for s in shapes] == ["text", "table"]
    assert shapes[1].table == [["項目", "值"], ["甲", "1"], ["乙", "2.5"]]
    layout_body = doc.native.slides[3].slide_layout.placeholders[1]
    assert (shapes[1].left, shapes[1].top, shapes[1].width) == (
        layout_body.left,
        layout_body.top,
        layout_body.width,
    )


def test_table_given_as_plain_rows_and_options() -> None:
    doc = _build({"slides": [{"title": "表", "table": [["a", "b"], ["c", "d"]]}]})
    assert doc.slide(0).shapes[1].table == [["a", "b"], ["c", "d"]]
    doc = _build(
        {
            "slides": [
                {"title": "表", "table": {"data": [["a"], ["b"]], "header": False, "size": 12}}
            ]
        }
    )
    assert doc.table_info(0, doc.slide(0).shapes[1].shape_id).header is False


def test_chart_beside_bullets() -> None:
    doc = _build(OUTLINE)
    shapes = doc.slide(4).shapes
    assert [s.kind for s in shapes] == ["text", "text", "chart"]
    text, chart = shapes[1], shapes[2]
    assert text.text == "說明文字"
    assert text.left is not None and text.width is not None and chart.left is not None
    assert text.left + text.width <= chart.left
    info = doc.chart_info(4, chart.shape_id)
    assert info.kind == "column"
    assert info.title == "季度"
    assert info.categories == ["Q1", "Q2"]
    assert [(s.name, s.values) for s in info.series] == [
        ("營收", [10.0, 20.0]),
        ("成本", [5.0, 8.0]),
    ]


def test_chart_series_as_list_and_default_kind() -> None:
    doc = _build({"slides": [{"chart": {"categories": ["甲"], "series": [["數量", [3]]]}}]})
    assert doc.slide(0).layout == "Title and Content"
    chart = next(s for s in doc.slide(0).shapes if s.kind == "chart")
    info = doc.chart_info(0, chart.shape_id)
    assert (info.kind, info.series[0].name, info.series[0].values) == ("column", "數量", [3.0])


@pytest.mark.parametrize("size", [(400, 200), (200, 800), (4000, 100)])
def test_image_fits_content_area(tmp_path: Path, size: tuple[int, int]) -> None:
    _png(tmp_path / "圖.png", size)
    built = outline.build_presentation(
        {"slides": [{"title": "圖片", "image": "圖.png"}]}, base_dir=tmp_path
    )
    doc = PptxDocument(built)
    shapes = doc.slide(0).shapes
    assert [s.kind for s in shapes] == ["text", "picture"]
    pic = shapes[1]
    body = built.slides[0].slide_layout.placeholders[1]
    assert pic.left is not None and pic.top is not None
    assert pic.width is not None and pic.height is not None
    assert pic.left >= body.left and pic.top >= body.top
    assert pic.left + pic.width <= body.left + body.width + 1
    assert pic.top + pic.height <= body.top + body.height + 1
    assert abs(pic.width / pic.height - size[0] / size[1]) < 0.02 * size[0] / size[1]


def test_image_absolute_path_and_missing(tmp_path: Path) -> None:
    png = _png(tmp_path / "a.png", (100, 100))
    doc = _build({"slides": [{"layout": "Blank", "image": str(png)}]})
    assert [s.kind for s in doc.slide(0).shapes] == ["picture"]
    with pytest.raises(AnyDocError, match="Image not found"):
        outline.build_presentation({"slides": [{"image": "missing.png"}]}, base_dir=tmp_path)


def test_layout_without_body_uses_textbox() -> None:
    doc = _build({"slides": [{"layout": "Title Only", "title": "標題", "bullets": ["甲", "乙"]}]})
    shapes = doc.slide(0).shapes
    assert [s.text for s in shapes] == ["標題", "甲\n乙"]
    assert shapes[1].placeholder is False
    title = shapes[0]
    assert title.top is not None and title.height is not None and shapes[1].top is not None
    assert shapes[1].top >= title.top + title.height

    blank = _build(
        {"slides": [{"layout": "Blank", "text": "純文字", "table": [["a"]]}]},
    )
    kinds = {s.kind: s for s in blank.slide(0).shapes}
    assert kinds["text"].left is not None and kinds["text"].width is not None
    assert kinds["table"].left is not None
    assert kinds["text"].left + kinds["text"].width <= kinds["table"].left


def test_subtitle_falls_back_to_body_placeholder() -> None:
    doc = _build({"slides": [{"layout": "Title and Content", "title": "t", "subtitle": "副"}]})
    assert doc.slide_text(0) == "t\n副"


def test_title_only_outline_and_slide_size() -> None:
    doc = _build({"title": "只有封面", "slide_size": "4:3"})
    assert [s.layout for s in doc.slides()] == ["Title Slide"]
    assert doc.metadata.extra["slide_width"] == SLIDE_SIZES["4:3"][0]
    doc = _build({"slides": [{}], "slide_size": ["20cm", "15cm"], "background": "101010"})
    assert doc.metadata.extra["slide_height"] == 5400000
    assert doc.get_background() == "101010"


# -- templates -------------------------------------------------------------------


def test_template_supplies_theme_layouts_and_size(tmp_path: Path) -> None:
    template = _template(tmp_path)
    doc = _build(OUTLINE | {"theme": None}, template=template)
    assert [s.layout for s in doc.slides()] == [
        "封面",
        "標題與內容",
        "Two Content",
        "標題與內容",
        "標題與內容",
        "僅標題",
        "Section Header",
        "空白",
    ]
    assert doc.slides()[0].title == "年度報告"  # template slides are dropped
    assert "範本投影片" not in doc.text()
    assert doc.theme().colors["accent1"] == "AA0000"
    assert doc.metadata.extra["slide_width"] == SLIDE_SIZES["4:3"][0]


def test_template_as_bytes_and_keep_slides(tmp_path: Path) -> None:
    data = _template(tmp_path).read_bytes()
    doc = _build(
        {"keep_template_slides": True, "slides": [{"layout": "標題與內容", "title": "新增"}]},
        template=data,
    )
    assert [s.title for s in doc.slides()] == ["範本投影片 1", "範本投影片 2", "新增"]


def test_template_errors(tmp_path: Path) -> None:
    with pytest.raises(AnyDocError, match="Template not found"):
        _build({"slides": [{}]}, template=tmp_path / "none.pptx")
    with pytest.raises(AnyDocError, match="Invalid PPTX template"):
        _build({"slides": [{}]}, template=b"junk")


def test_pick_layout_by_placeholder_signature() -> None:
    prs = Presentation()
    for layout in prs.slide_layouts:
        layout.name = f"版面 {layout.name[::-1]}"
    names = {
        kind: outline.pick_layout(prs, kind).name[3:][::-1]
        for kind in ("title", "content", "title_only", "blank")
    }
    assert names == {
        "title": "Title Slide",
        "content": "Title and Content",
        "title_only": "Title Only",
        "blank": "Blank",
    }


def test_pick_layout_fallbacks() -> None:
    prs = Presentation()
    keep = {"Comparison"}
    for layout in list(prs.slide_layouts):
        if layout.name not in keep:
            prs.slide_layouts.remove(layout)
    for kind in ("title", "content", "title_only", "blank"):
        assert outline.pick_layout(prs, kind).name == "Comparison"
    prs.slide_layouts.remove(prs.slide_layouts[0])
    with pytest.raises(AnyDocError, match="no slide layouts"):
        outline.pick_layout(prs, "content")


# -- validation ------------------------------------------------------------------


@pytest.mark.parametrize(
    ("data", "message"),
    [
        ([], "outline must be an object"),
        ({"slides": [{}], "titel": "x"}, "outline: unknown key"),
        ({"slides": {}}, "slides must be a list"),
        ({}, "nothing to build"),
        ({"title": "x", "title_slide": False}, "nothing to build"),
        ({"subtitle": "x", "slides": [{}]}, "subtitle needs a title"),
        ({"title": 3}, "must be a string"),
        ({"slides": ["x"]}, "slide 1 must be an object"),
        ({"slides": [{}, {"bulets": []}]}, "slide 2: unknown key"),
        ({"slides": [{"layout": "不存在"}]}, "slide 1: No such layout"),
        ({"slides": [{"layout": 1.5}]}, "layout must be a name or an index"),
        ({"slides": [{"layout": "Blank", "title": "x"}]}, "no title placeholder"),
        ({"slides": [{"layout": "Blank", "subtitle": "x"}]}, "no subtitle placeholder"),
        ({"slides": [{"bullets": ["a"], "text": "b"}]}, "either bullets or text"),
        ({"slides": [{"bullets": 5}]}, "must be a string or a list"),
        ({"slides": [{"bullets": [5]}]}, "invalid bullet"),
        ({"slides": [{"bullets": [{"text": "a", "level": 9}]}]}, "between 0 and 8"),
        ({"slides": [{"bullets": [{"text": "a", "level": "1"}]}]}, "level must be an integer"),
        ({"slides": [{"bullets": [{"text": "a", "lvl": 1}]}]}, "unknown key"),
        ({"slides": [{"bullets": [[[[[[[[[["x"]]]]]]]]]]}]}, "nested too deeply"),
        ({"slides": [{"table": [["a"]], "chart": {"series": {"a": [1]}}}]}, "only one of"),
        ({"slides": [{"table": {"data": []}}]}, "non-empty list of rows"),
        ({"slides": [{"table": {"data": [["a"]], "rows": 2}}]}, "table: unknown key"),
        ({"slides": [{"table": "x"}]}, "table must be an object"),
        ({"slides": [{"chart": {"kind": "column"}}]}, "series is required"),
        ({"slides": [{"chart": {"series": {"a": [1]}, "kind": "bubble"}}]}, "Unknown chart kind"),
        ({"slides": [{"chart": {"series": {"a": [1]}, "color": []}}]}, "chart: unknown key"),
        ({"slides": [{"image": 5}]}, "image must be a string"),
        ({"slides": [{"placeholders": {"x": "a"}}]}, "invalid index"),
        ({"slides": [{"placeholders": {"7": "a"}}]}, "no text placeholder 7"),
        ({"slides": [{"placeholders": []}]}, "placeholders must be an object"),
        ({"slides": [{"notes": 1}]}, "notes must be a string"),
        ({"slides": [{"background": "red"}]}, "Invalid color"),
        ({"slides": [{}], "slide_size": "1:1"}, "Unknown slide size"),
        ({"slides": [{}], "theme": {"palette": {}}}, "theme: unknown key"),
        ({"slides": [{}], "theme": {"fonts": {"heading": "x"}}}, "theme fonts: unknown key"),
        ({"slides": [{}], "theme": {"colors": {"accent7": "000000"}}}, "Unknown theme color"),
    ],
)
def test_invalid_outlines(data: Any, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        outline.build_presentation(data)


# -- export and round trip ---------------------------------------------------------


def test_export_outline() -> None:
    exported = _build(OUTLINE).to_outline()
    assert exported["title"] == "年度報告"
    assert exported["author"] == "王小明"
    assert exported["subject"] == "營運"
    assert exported["title_slide"] is False
    slides = exported["slides"]
    assert slides[0] == {"layout": "Title Slide", "title": "年度報告", "subtitle": "2026 年度"}
    assert slides[1] == {
        "layout": "Title and Content",
        "title": "議程",
        "bullets": [
            "第一點",
            {"text": "子項", "level": 1},
            {"text": "巢狀", "level": 1},
            {"text": "更深", "level": 2},
            "第二點",
        ],
        "notes": "這是備忘稿",
    }
    assert slides[2] == {
        "layout": "Two Content",
        "title": "比較",
        "bullets": ["左側"],
        "placeholders": {"2": ["右側"]},
    }
    assert slides[3]["table"] == {"data": [["項目", "值"], ["甲", "1"], ["乙", "2.5"]]}
    assert slides[4]["bullets"] == ["說明文字"]
    assert slides[4]["chart"] == {
        "kind": "column",
        "series": [["營收", [10.0, 20.0]], ["成本", [5.0, 8.0]]],
        "categories": ["Q1", "Q2"],
        "title": "季度",
    }
    assert slides[5] == {"layout": "Title Only", "title": "只有標題"}
    assert slides[7] == {"layout": "Blank"}
    json.dumps(exported)  # JSON serialisable


def test_export_scatter_chart_pairs() -> None:
    doc = _build({"slides": [{"chart": {"kind": "scatter", "series": {"點": [[1, 2], [3, 4]]}}}]})
    chart = doc.to_outline()["slides"][0]["chart"]
    assert chart == {"kind": "scatter", "series": [["點", [[1.0, 2.0], [3.0, 4.0]]]]}


def test_outline_roundtrip_is_stable() -> None:
    first = _build(OUTLINE).to_outline()
    second = _build(first).to_outline()
    assert second == first
    assert [s.layout for s in _build(first).slides()] == [s["layout"] for s in first["slides"]]


def test_load_outline_and_outline_to_pptx(tmp_path: Path) -> None:
    _png(tmp_path / "logo.png", (300, 100))
    source = tmp_path / "大綱.json"
    data = {"title": "檔案大綱", "slides": [{"title": "圖", "image": "logo.png"}]}
    source.write_text("﻿" + json.dumps(data, ensure_ascii=False), encoding="utf-8")
    assert outline.load_outline(source) == data

    out = outline.outline_to_pptx(source, tmp_path / "out.pptx")
    doc = PptxHandler().open(out)
    assert [s.title for s in doc.slides()] == ["檔案大綱", "圖"]
    assert [s.kind for s in doc.slide(1).shapes] == ["text", "picture"]

    again = outline.outline_to_pptx(data | {"slides": []}, str(tmp_path / "b.pptx"))
    assert PptxHandler().open(again).metadata.page_count == 1

    via_handler = PptxHandler().from_outline(source)
    assert via_handler.metadata.page_count == 2


def test_load_outline_errors(tmp_path: Path) -> None:
    with pytest.raises(AnyDocError, match="Outline not found"):
        outline.load_outline(tmp_path / "none.json")
    bad = tmp_path / "bad.json"
    bad.write_text("{oops", encoding="utf-8")
    with pytest.raises(AnyDocError, match="Invalid outline JSON"):
        outline.load_outline(bad)
    bad.write_text("[1, 2]", encoding="utf-8")
    with pytest.raises(AnyDocError, match="top level must be an object"):
        outline.load_outline(bad)


@pytest.mark.requires("libreoffice")
def test_libreoffice_opens_built_presentation(tmp_path: Path) -> None:
    src = tmp_path / "built.pptx"
    outline.outline_to_pptx(OUTLINE, src, template=_template(tmp_path))
    pdf = convert_with_libreoffice(src, "pdf", tmp_path / "pdf")
    assert pdf.read_bytes().startswith(b"%PDF")
    back = PptxHandler().open(convert_with_libreoffice(src, "pptx", tmp_path / "pptx"))
    assert back.metadata.page_count == 8
    assert [s.title for s in back.slides()][:5] == ["年度報告", "議程", "比較", "數據", "圖表"]
    assert "子項" in back.slide_text(1)
    assert any(s.kind == "table" for s in back.slide(3).shapes)
    assert any(s.kind == "chart" for s in back.slide(4).shapes)


# -- CLI -----------------------------------------------------------------------


def _write_outline(tmp_path: Path, data: dict[str, Any] | None = None) -> Path:
    path = tmp_path / "outline.json"
    path.write_text(json.dumps(data or OUTLINE, ensure_ascii=False), encoding="utf-8")
    return path


def test_cli_build_and_outline(tmp_path: Path) -> None:
    out = tmp_path / "deck.pptx"
    result = runner.invoke(app, ["pptx", "build", str(_write_outline(tmp_path)), str(out)])
    assert result.exit_code == 0, result.output
    assert str(out) in result.output
    assert PptxHandler().open(out).metadata.page_count == 8

    result = runner.invoke(app, ["pptx", "outline", str(out)])
    assert result.exit_code == 0
    assert json.loads(result.output)["slides"][1]["title"] == "議程"

    dump = tmp_path / "dump.json"
    result = runner.invoke(app, ["pptx", "outline", str(out), "-o", str(dump)])
    assert result.exit_code == 0
    assert json.loads(dump.read_text(encoding="utf-8"))["title"] == "年度報告"

    rebuilt = tmp_path / "rebuilt.pptx"
    result = runner.invoke(app, ["pptx", "build", str(dump), str(rebuilt)])
    assert result.exit_code == 0, result.output
    assert PptxHandler().open(rebuilt).to_outline() == json.loads(dump.read_text("utf-8"))


def test_cli_build_with_template(tmp_path: Path) -> None:
    out = tmp_path / "deck.pptx"
    data = {"slides": [{"title": "套用範本", "bullets": ["甲"]}]}
    result = runner.invoke(
        app,
        [
            "pptx",
            "build",
            str(_write_outline(tmp_path, data)),
            str(out),
            "-t",
            str(_template(tmp_path)),
        ],
    )
    assert result.exit_code == 0, result.output
    doc = PptxHandler().open(out)
    assert [s.layout for s in doc.slides()] == ["標題與內容"]
    assert doc.theme().colors["accent1"] == "AA0000"


def test_cli_build_errors(tmp_path: Path) -> None:
    out = tmp_path / "deck.pptx"
    result = runner.invoke(app, ["pptx", "build", str(tmp_path / "none.json"), str(out)])
    assert result.exit_code == 2
    source = _write_outline(tmp_path, {"slides": [{"layout": "不存在"}]})
    result = runner.invoke(app, ["pptx", "build", str(source), str(out)])
    assert result.exit_code == 1
    assert "No such layout" in result.output
    assert not out.exists()
    result = runner.invoke(
        app, ["pptx", "build", str(source), str(out), "-t", str(tmp_path / "none.pptx")]
    )
    assert result.exit_code == 2


def test_cli_layouts(tmp_path: Path) -> None:
    out = tmp_path / "deck.pptx"
    outline.outline_to_pptx(OUTLINE, out)
    result = runner.invoke(app, ["pptx", "layouts", str(out)])
    assert result.exit_code == 0
    rows = json.loads(result.output)
    assert rows[1]["name"] == "Title and Content"
    assert rows[1]["used_by"] == [1, 3, 4]
    assert rows[1]["placeholders"][0] == {"idx": 0, "type": "title", "name": "Title 1"}

    assert runner.invoke(app, ["pptx", "layouts", str(tmp_path / "none.pptx")]).exit_code == 2
    bad = tmp_path / "bad.pptx"
    bad.write_bytes(b"junk")
    assert runner.invoke(app, ["pptx", "layouts", str(bad)]).exit_code == 1


def test_cli_theme_show_and_change(tmp_path: Path) -> None:
    src = tmp_path / "deck.pptx"
    outline.outline_to_pptx({"slides": [{"title": "主題"}]}, src)
    result = runner.invoke(app, ["pptx", "theme", str(src)])
    assert result.exit_code == 0
    assert json.loads(result.output)["colors"]["accent1"] == "4F81BD"

    out = tmp_path / "themed.pptx"
    result = runner.invoke(
        app,
        [
            "pptx",
            "theme",
            str(src),
            "-c",
            "accent1=1F4E79",
            "-c",
            "accent2 = 00AA00",
            "--major-font",
            "Arial",
            "--minor-font",
            "Verdana",
            "-o",
            str(out),
        ],
    )
    assert result.exit_code == 0, result.output
    info = PptxHandler().open(out).theme()
    assert (info.colors["accent1"], info.colors["accent2"]) == ("1F4E79", "00AA00")
    assert (info.major_font, info.minor_font) == ("Arial", "Verdana")
    assert PptxHandler().open(src).theme().colors["accent1"] == "4F81BD"

    applied = tmp_path / "applied.pptx"
    result = runner.invoke(
        app, ["pptx", "theme", str(src), "--apply", str(out), "-o", str(applied)]
    )
    assert result.exit_code == 0
    assert PptxHandler().open(applied).theme().colors["accent1"] == "1F4E79"


def test_cli_theme_errors(tmp_path: Path) -> None:
    src = tmp_path / "deck.pptx"
    outline.outline_to_pptx({"slides": [{}]}, src)
    out = tmp_path / "o.pptx"
    assert runner.invoke(app, ["pptx", "theme", str(src), "-c", "accent1=000000"]).exit_code == 2
    for args in (["-c", "accent1"], ["-c", "accent1=zz"], ["--apply", str(tmp_path / "x.pptx")]):
        result = runner.invoke(app, ["pptx", "theme", str(src), *args, "-o", str(out)])
        assert result.exit_code == 1, args
        assert not out.exists()
