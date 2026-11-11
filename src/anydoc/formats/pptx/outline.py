"""Build a presentation from an outline (JSON) and a template, and export one back (T073).

Outline format (every key is optional except ``slides``)::

    {
      "title": "簡報標題", "subtitle": "副標題", "author": "作者",
      "slide_size": "16:9",
      "theme": {"colors": {"accent1": "1F4E79"}, "fonts": {"major": "Arial"}},
      "background": "FFFFFF",
      "slides": [
        {"title": "議程", "bullets": ["第一點", {"text": "子項", "level": 1}, ["巢狀"]]},
        {"layout": "Two Content", "title": "比較", "placeholders": {"1": ["左"], "2": ["右"]}},
        {"title": "數據", "table": {"data": [["項目", "值"], ["甲", 1]]}, "notes": "備忘稿"},
        {"title": "圖表",
         "chart": {"kind": "column", "categories": ["Q1"], "series": {"營收": [1]}}},
        {"title": "圖片", "image": "logo.png", "background": "000000"}
      ]
    }
"""

from __future__ import annotations

import io
import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from pptx import Presentation
from pptx.enum.shapes import PP_PLACEHOLDER

from anydoc.core.errors import AnyDocError
from anydoc.formats.pptx import charts as _charts
from anydoc.formats.pptx import shapes as _shapes
from anydoc.formats.pptx import slides as _slides
from anydoc.formats.pptx import tables as _tables
from anydoc.formats.pptx import themes as _themes

_TOP_KEYS = {
    "title",
    "subtitle",
    "author",
    "subject",
    "keywords",
    "slide_size",
    "theme",
    "background",
    "keep_template_slides",
    "title_slide",
    "slides",
}
_SLIDE_KEYS = {
    "layout",
    "title",
    "subtitle",
    "bullets",
    "text",
    "placeholders",
    "table",
    "chart",
    "image",
    "notes",
    "background",
}
_TABLE_KEYS = {"data", "header", "banded", "first_col", "col_widths", "size", "font", "align"}
_CHART_KEYS = {
    "kind",
    "categories",
    "series",
    "title",
    "x_title",
    "y_title",
    "stacking",
    "legend",
    "data_labels",
    "number_format",
    "colors",
}
_FONT_KEYS = {"major", "minor", "major_east_asian", "minor_east_asian"}

_TITLE_TYPES = {PP_PLACEHOLDER.TITLE, PP_PLACEHOLDER.CENTER_TITLE, PP_PLACEHOLDER.VERTICAL_TITLE}
_CONTENT_TYPES = {
    PP_PLACEHOLDER.BODY,
    PP_PLACEHOLDER.OBJECT,
    PP_PLACEHOLDER.VERTICAL_BODY,
    PP_PLACEHOLDER.VERTICAL_OBJECT,
}
_LATENT_TYPES = {PP_PLACEHOLDER.DATE, PP_PLACEHOLDER.FOOTER, PP_PLACEHOLDER.SLIDE_NUMBER}

Bullet = tuple[str, int]


# -- validation helpers ---------------------------------------------------------------


def _check_keys(data: Mapping[str, Any], allowed: set[str], where: str) -> None:
    unknown = sorted(set(data) - allowed)
    if unknown:
        raise ValueError(f"{where}: unknown key(s) {', '.join(unknown)}")


def _mapping(value: Any, where: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{where} must be an object")
    return value


def _text(value: Any, where: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{where} must be a string")
    return value


def _bullets(value: Any, where: str, level: int = 0) -> list[Bullet]:
    """Flatten bullets: strings, ``{"text", "level"}`` objects and nested lists."""
    if isinstance(value, str):
        return [(line, level) for line in value.split("\n")]
    if not isinstance(value, Sequence):
        raise ValueError(f"{where} must be a string or a list")
    result: list[Bullet] = []
    for item in value:
        if isinstance(item, str):
            result.append((item, level))
        elif isinstance(item, Mapping):
            _check_keys(item, {"text", "level"}, where)
            item_level = item.get("level", level)
            if isinstance(item_level, bool) or not isinstance(item_level, int):
                raise ValueError(f"{where}: level must be an integer")
            if not 0 <= item_level <= 8:
                raise ValueError(f"{where}: level must be between 0 and 8")
            result.append((_text(item.get("text"), f"{where} text"), item_level))
        elif isinstance(item, Sequence):
            if level >= 8:
                raise ValueError(f"{where}: bullets are nested too deeply")
            result.extend(_bullets(item, where, level + 1))
        else:
            raise ValueError(f"{where}: invalid bullet {item!r}")
    return result


def _write_bullets(frame: Any, bullets: list[Bullet]) -> None:
    frame.text = bullets[0][0] if bullets else ""
    if bullets and bullets[0][1]:
        frame.paragraphs[0].level = bullets[0][1]
    for line, level in bullets[1:]:
        paragraph = frame.add_paragraph()
        paragraph.text = line
        if level:
            paragraph.level = level


# -- layout selection -----------------------------------------------------------------


def _types(layout: Any) -> list[Any]:
    return [
        ph.placeholder_format.type
        for ph in layout.placeholders
        if ph.placeholder_format.type not in _LATENT_TYPES
    ]


def _by_name(layouts: list[Any], name: str) -> Any | None:
    return next((item for item in layouts if item.name.lower() == name.lower()), None)


def _first(layouts: list[Any], predicate: Any) -> Any | None:
    return next((item for item in layouts if predicate(_types(item))), None)


def _is_title_slide(types: list[Any]) -> bool:
    return PP_PLACEHOLDER.CENTER_TITLE in types or PP_PLACEHOLDER.SUBTITLE in types


def _is_content(types: list[Any]) -> bool:
    return (
        len(types) == 2
        and types[0] in _TITLE_TYPES
        and types[1] in _CONTENT_TYPES
        and not _is_title_slide(types)
    )


def pick_layout(prs: Any, kind: str) -> Any:
    """Choose a layout for ``kind`` (``title``/``content``/``title_only``/``blank``).

    Standard English names are tried first, then the placeholder signature, so that
    templates with localized layout names still work.
    """
    layouts = _themes.all_layouts(prs)
    if not layouts:
        raise AnyDocError("The presentation has no slide layouts")
    content = (
        _by_name(layouts, "Title and Content")
        or _first(layouts, _is_content)
        or _first(layouts, lambda t: any(x in _CONTENT_TYPES for x in t))
        or layouts[min(1, len(layouts) - 1)]
    )
    if kind == "title":
        return _by_name(layouts, "Title Slide") or _first(layouts, _is_title_slide) or layouts[0]
    if kind == "title_only":
        only = _by_name(layouts, "Title Only") or _first(
            layouts, lambda t: len(t) == 1 and t[0] in _TITLE_TYPES
        )
        return only or content
    if kind == "blank":
        return _by_name(layouts, "Blank") or _first(layouts, lambda t: not t) or layouts[-1]
    return content


def _slide_kind(spec: Mapping[str, Any]) -> str:
    if any(spec.get(k) is not None for k in ("bullets", "text", "table", "chart", "image")):
        return "content"
    if spec.get("placeholders"):
        return "content"
    if spec.get("subtitle") is not None:
        return "title"
    if spec.get("title") is not None:
        return "title_only"
    return "blank"


# -- slide building -------------------------------------------------------------------


def _find_placeholder(slide: Any, types: set[Any]) -> Any | None:
    for ph in slide.placeholders:
        if ph.placeholder_format.type in types:
            return ph
    return None


def _region(prs: Any, slide: Any) -> tuple[int, int, int, int]:
    """Default content area: below the title, inside 5% side margins."""
    width, height = int(prs.slide_width), int(prs.slide_height)
    top = round(height * 0.25)
    title = slide.shapes.title
    if title is not None and title.top is not None and title.height is not None:
        top = int(title.top) + int(title.height) + round(height * 0.03)
    left = round(width * 0.05)
    return left, top, width - 2 * left, max(height - top - round(height * 0.08), 1)


def _body_text(prs: Any, index: int, slide: Any, bullets: list[Bullet], half: bool) -> None:
    body = _find_placeholder(slide, _CONTENT_TYPES | {PP_PLACEHOLDER.SUBTITLE})
    if body is not None:
        if half and None not in (body.left, body.top, body.width, body.height):
            # write the inherited geometry explicitly, then narrow it to the left half
            geometry = (int(body.left), int(body.top), int(body.width), int(body.height))
            body.left, body.top, body.height = geometry[0], geometry[1], geometry[3]
            body.width = round(geometry[2] * 0.48)
        _write_bullets(body.text_frame, bullets)
        return
    left, top, width, height = _region(prs, slide)
    if half:
        width = round(width * 0.48)
    box = slide.shapes.add_textbox(left, top, width, height)
    box.text_frame.word_wrap = True
    _write_bullets(box.text_frame, bullets)


def _object_region(prs: Any, slide: Any, has_text: bool) -> tuple[int, int, int, int]:
    body = _find_placeholder(slide, _CONTENT_TYPES)
    if body is not None and None not in (body.left, body.top, body.width, body.height):
        left, top = int(body.left), int(body.top)
        width, height = int(body.width), int(body.height)
        if not has_text:
            body._element.getparent().remove(body._element)
    else:
        left, top, width, height = _region(prs, slide)
    if has_text:
        shift = round(width * 0.52)
        left, width = left + shift, width - shift
    return left, top, width, height


def _add_table(prs: Any, index: int, spec: Any, box: tuple[int, int, int, int], where: str) -> None:
    options = dict(_mapping({"data": spec} if isinstance(spec, list) else spec, f"{where} table"))
    _check_keys(options, _TABLE_KEYS, f"{where} table")
    data = options.pop("data", None)
    if not isinstance(data, list) or not data or not all(isinstance(r, list) for r in data):
        raise ValueError(f"{where} table: data must be a non-empty list of rows")
    left, top, width, _ = box
    _tables.add_table(prs, index, data, left=left, top=top, width=width, **options)


def _add_chart(prs: Any, index: int, spec: Any, box: tuple[int, int, int, int], where: str) -> None:
    options = dict(_mapping(spec, f"{where} chart"))
    _check_keys(options, _CHART_KEYS, f"{where} chart")
    series = options.pop("series", None)
    if not isinstance(series, Mapping | list) or not series:
        raise ValueError(f"{where} chart: series is required")
    if isinstance(series, list):
        series = [(_text(s[0], f"{where} chart series name"), s[1]) for s in series]
    kind = _text(options.pop("kind", "column"), f"{where} chart kind")
    categories = options.pop("categories", None)
    left, top, width, height = box
    _charts.add_chart(
        prs,
        index,
        kind,
        categories,
        series,
        left=left,
        top=top,
        width=width,
        height=height,
        **options,
    )


def _add_image(
    prs: Any,
    index: int,
    spec: Any,
    box: tuple[int, int, int, int],
    base_dir: Path | None,
    where: str,
) -> None:
    path = Path(_text(spec, f"{where} image"))
    if not path.is_absolute() and base_dir is not None:
        path = base_dir / path
    left, top, width, height = box
    shape_id = _shapes.add_picture(prs, index, path, left=left, top=top, height=height)
    picture = _shapes.find_shape(prs, index, shape_id)
    if int(picture.width) > width:  # too wide for the area: fit to its width instead
        ratio = width / int(picture.width)
        picture.width, picture.height = width, round(int(picture.height) * ratio)
    picture.left = left + (width - int(picture.width)) // 2
    picture.top = top + (height - int(picture.height)) // 2


def _fill_placeholders(slide: Any, spec: Any, where: str) -> None:
    by_idx = {int(ph.placeholder_format.idx): ph for ph in slide.placeholders}
    for key, value in _mapping(spec, f"{where} placeholders").items():
        try:
            idx = int(key)
        except (TypeError, ValueError):
            raise ValueError(f"{where} placeholders: invalid index {key!r}") from None
        target = by_idx.get(idx)
        if target is None or not target.has_text_frame:
            raise ValueError(
                f"{where} placeholders: layout {slide.slide_layout.name!r} has no text "
                f"placeholder {idx} (available: {sorted(by_idx)})"
            )
        _write_bullets(target.text_frame, _bullets(value, f"{where} placeholders[{key}]"))


def _build_slide(prs: Any, spec: Mapping[str, Any], number: int, base_dir: Path | None) -> None:
    where = f"slide {number}"
    _check_keys(spec, _SLIDE_KEYS, where)
    objects = [k for k in ("table", "chart", "image") if spec.get(k) is not None]
    if len(objects) > 1:
        raise ValueError(f"{where}: only one of table, chart or image is allowed per slide")
    if spec.get("bullets") is not None and spec.get("text") is not None:
        raise ValueError(f"{where}: give either bullets or text, not both")

    layout_ref = spec.get("layout")
    if layout_ref is None:
        layout = pick_layout(prs, _slide_kind(spec))
    else:
        if isinstance(layout_ref, bool) or not isinstance(layout_ref, int | str):
            raise ValueError(f"{where}: layout must be a name or an index")
        try:
            layout = _themes.find_layout(prs, layout_ref)
        except ValueError as exc:
            raise ValueError(f"{where}: {exc}") from None
    slide = prs.slides.add_slide(layout)
    index = len(prs.slides) - 1

    if spec.get("title") is not None:
        title = _text(spec["title"], f"{where} title")
        if slide.shapes.title is None:
            raise ValueError(f"{where}: layout {layout.name!r} has no title placeholder")
        slide.shapes.title.text_frame.text = title
    if spec.get("subtitle") is not None:
        target = _find_placeholder(slide, {PP_PLACEHOLDER.SUBTITLE}) or _find_placeholder(
            slide, _CONTENT_TYPES
        )
        if target is None:
            raise ValueError(f"{where}: layout {layout.name!r} has no subtitle placeholder")
        _write_bullets(target.text_frame, _bullets(spec["subtitle"], f"{where} subtitle"))
    if spec.get("placeholders") is not None:
        _fill_placeholders(slide, spec["placeholders"], where)

    raw = spec.get("bullets") if spec.get("bullets") is not None else spec.get("text")
    bullets = _bullets(raw, f"{where} bullets") if raw is not None else []
    if objects:
        box = _object_region(prs, slide, bool(bullets))
    if bullets:
        _body_text(prs, index, slide, bullets, bool(objects))
    if "table" in objects:
        _add_table(prs, index, spec["table"], box, where)
    elif "chart" in objects:
        _add_chart(prs, index, spec["chart"], box, where)
    elif "image" in objects:
        _add_image(prs, index, spec["image"], box, base_dir, where)

    if spec.get("notes") is not None:
        slide.notes_slide.notes_text_frame.text = _text(spec["notes"], f"{where} notes")
    if spec.get("background") is not None:
        _themes.set_background(prs, _text(spec["background"], f"{where} background"), slide=index)


def _open_template(template: Path | str | bytes | None) -> tuple[Any, bool]:
    if template is None:
        return Presentation(), False
    if isinstance(template, bytes):
        source: Any = io.BytesIO(template)
    else:
        if not Path(template).is_file():
            raise AnyDocError(f"Template not found: {template}")
        source = str(template)
    try:
        return Presentation(source), True
    except Exception as exc:
        raise AnyDocError(f"Invalid PPTX template: {exc}") from exc


def _apply_theme_spec(prs: Any, spec: Any) -> None:
    theme = _mapping(spec, "theme")
    _check_keys(theme, {"colors", "fonts"}, "theme")
    if theme.get("colors") is not None:
        _themes.set_theme_colors(prs, _mapping(theme["colors"], "theme colors"))
    if theme.get("fonts") is not None:
        fonts = _mapping(theme["fonts"], "theme fonts")
        _check_keys(fonts, _FONT_KEYS, "theme fonts")
        names = {k: _text(v, f"theme fonts {k}") for k, v in fonts.items()}
        _themes.set_theme_fonts(
            prs,
            major=names.get("major"),
            minor=names.get("minor"),
            major_east_asian=names.get("major_east_asian"),
            minor_east_asian=names.get("minor_east_asian"),
        )


def build_presentation(
    outline: Mapping[str, Any],
    *,
    template: Path | str | bytes | None = None,
    base_dir: Path | None = None,
) -> Any:
    """Create a ``pptx.Presentation`` from an outline, optionally based on a template.

    The template supplies masters, layouts and theme; its own slides are dropped unless
    the outline sets ``keep_template_slides``. A top-level ``title`` produces a title
    slide (unless ``title_slide`` is false) and becomes the document title. Slides
    without ``layout`` get one that suits their content. Relative image paths are
    resolved against ``base_dir``. Invalid outlines raise ``ValueError``.
    """
    outline = _mapping(outline, "outline")
    _check_keys(outline, _TOP_KEYS, "outline")
    specs = outline.get("slides", [])
    if not isinstance(specs, list):
        raise ValueError("outline: slides must be a list")
    if not specs and (outline.get("title") is None or not outline.get("title_slide", True)):
        raise ValueError("outline: nothing to build (no title and no slides)")

    prs, from_template = _open_template(template)
    if from_template and not outline.get("keep_template_slides", False):
        for _ in range(len(prs.slides)):
            _slides.delete_slide(prs, 0)
    size = outline.get("slide_size", None if from_template else "16:9")
    if size is not None:
        _themes.set_slide_size(prs, size)
    if outline.get("theme") is not None:
        _apply_theme_spec(prs, outline["theme"])
    if outline.get("background") is not None:
        _themes.set_background(prs, _text(outline["background"], "outline background"))

    props = prs.core_properties
    for key in ("title", "author", "subject", "keywords"):
        if outline.get(key) is not None:
            setattr(props, key, _text(outline[key], f"outline {key}"))

    number = 0
    if outline.get("title") is not None and outline.get("title_slide", True):
        cover: dict[str, Any] = {"title": outline["title"]}
        if outline.get("subtitle") is not None:
            cover["subtitle"] = outline["subtitle"]
        cover["layout"] = pick_layout(prs, "title").name
        number += 1
        _build_slide(prs, cover, number, base_dir)
    elif outline.get("subtitle") is not None and outline.get("title") is None:
        raise ValueError("outline: subtitle needs a title")
    for spec in specs:
        number += 1
        _build_slide(prs, _mapping(spec, f"slide {number}"), number, base_dir)
    return prs


def load_outline(path: Path | str) -> dict[str, Any]:
    """Read an outline from a UTF-8 JSON file."""
    source = Path(path)
    if not source.is_file():
        raise AnyDocError(f"Outline not found: {source}")
    try:
        data = json.loads(source.read_text(encoding="utf-8-sig"))
    except (ValueError, OSError) as exc:
        raise AnyDocError(f"Invalid outline JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise AnyDocError("Invalid outline JSON: the top level must be an object")
    return data


def outline_to_pptx(
    outline: Mapping[str, Any] | Path | str,
    output: Path | str,
    *,
    template: Path | str | bytes | None = None,
) -> Path:
    """Build a presentation from an outline (object or JSON file) and save it to ``output``."""
    base_dir: Path | None = None
    if isinstance(outline, Path | str):
        base_dir = Path(outline).resolve().parent
        outline = load_outline(outline)
    prs = build_presentation(outline, template=template, base_dir=base_dir)
    target = Path(output)
    prs.save(str(target))
    return target


# -- export -----------------------------------------------------------------------


def _frame_bullets(frame: Any) -> list[Any]:
    result: list[Any] = []
    for paragraph in frame.paragraphs:
        text = "".join(run.text for run in paragraph.runs)
        if not text.strip():
            continue
        level = int(paragraph.level or 0)
        result.append({"text": text, "level": level} if level else text)
    return result


def export_outline(prs: Any) -> dict[str, Any]:
    """Describe a presentation as an outline accepted by ``build_presentation``.

    Titles, subtitles, body bullets (with levels), further text placeholders, the first
    table or chart and the speaker notes of each slide are exported; pictures and
    free-floating shapes are not.
    """
    outline: dict[str, Any] = {}
    props = prs.core_properties
    for key in ("title", "author", "subject", "keywords"):
        value = getattr(props, key)
        if value:
            outline[key] = value
    if "title" in outline:
        outline["title_slide"] = False  # the cover is already among the slides
    result: list[dict[str, Any]] = []
    for index, slide in enumerate(prs.slides):
        entry: dict[str, Any] = {"layout": str(slide.slide_layout.name)}
        title = slide.shapes.title
        if title is not None and title.text_frame.text:
            entry["title"] = title.text_frame.text
        main_done = False
        extra: dict[str, Any] = {}
        for ph in slide.placeholders:
            kind = ph.placeholder_format.type
            if kind in _TITLE_TYPES or kind in _LATENT_TYPES or not ph.has_text_frame:
                continue
            bullets = _frame_bullets(ph.text_frame)
            if not bullets:
                continue
            if kind == PP_PLACEHOLDER.SUBTITLE and "subtitle" not in entry:
                entry["subtitle"] = "\n".join(
                    b if isinstance(b, str) else b["text"] for b in bullets
                )
            elif kind in _CONTENT_TYPES and not main_done:
                entry["bullets"] = bullets
                main_done = True
            else:
                extra[str(int(ph.placeholder_format.idx))] = bullets
        if extra:
            entry["placeholders"] = extra
        for shape in slide.shapes:
            if getattr(shape, "has_table", False) and shape.has_table:
                info = _tables.table_info(prs, index, shape.shape_id)
                entry["table"] = {"data": [list(row) for row in info.data]}
                break
            if getattr(shape, "has_chart", False) and shape.has_chart:
                chart = _charts.chart_info(prs, index, shape.shape_id)
                data: dict[str, Any] = {
                    "kind": chart.kind,
                    "series": [
                        [
                            s.name,
                            [list(pair) for pair in zip(s.x_values, s.values, strict=False)]
                            if s.x_values is not None
                            else list(s.values),
                        ]
                        for s in chart.series
                    ],
                }
                if chart.categories:
                    data["categories"] = list(chart.categories)
                if chart.title:
                    data["title"] = chart.title
                entry["chart"] = data
                break
        if slide.has_notes_slide and slide.notes_slide.notes_text_frame.text:
            entry["notes"] = slide.notes_slide.notes_text_frame.text
        result.append(entry)
    outline["slides"] = result
    return outline
