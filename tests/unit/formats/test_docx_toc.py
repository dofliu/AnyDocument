"""Tests for DOCX table of contents generation (T054)."""

from __future__ import annotations

from pathlib import Path

import pytest
from docx import Document as load_docx

from anydoc.core.errors import OperationNotSupportedError
from anydoc.core.registry import registry
from anydoc.formats.docx import DocxDocument, DocxHandler

W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


@pytest.fixture(autouse=True)
def _handler() -> None:
    registry.register(DocxHandler, replace=True)


def _doc() -> DocxDocument:
    doc = DocxHandler().create()
    doc.add_heading("第一章 緒論", 1)
    doc.add_paragraph("內文")
    doc.add_heading("背景", 2)
    doc.add_heading("細節", 3)
    doc.add_heading("第二章 方法", 1)
    doc.add_heading("太深", 4)
    return doc


def _reload(doc: DocxDocument) -> DocxDocument:
    return DocxHandler().open(doc.to_bytes())


def test_toc_entries_filter_by_levels() -> None:
    doc = _doc()
    assert [(e.level, e.text) for e in doc.toc_entries("1-2")] == [
        (1, "第一章 緒論"),
        (2, "背景"),
        (1, "第二章 方法"),
    ]
    assert len(doc.toc_entries((1, 4))) == 5
    assert [e.text for e in doc.toc_entries("2")] == ["背景"]


@pytest.mark.parametrize("bad", ["0-3", "3-1", "abc", "1-10", (3, 2)])
def test_invalid_levels(bad: str | tuple[int, int]) -> None:
    with pytest.raises(ValueError):
        _doc().toc_entries(bad)


def test_field_toc_has_instruction_and_prefilled_entries() -> None:
    doc = _doc()
    entries = doc.add_toc(levels="1-3", index=0)
    assert [e.text for e in entries] == ["第一章 緒論", "背景", "細節", "第二章 方法"]
    again = _reload(doc)
    info = again.toc_info()
    assert info["kind"] == "field"
    assert 'TOC \\o "1-3"' in info["instruction"]
    assert info["entries"] == ["第一章 緒論", "背景", "細節", "第二章 方法"]
    texts = [p.text for p in again.native.paragraphs]
    assert texts[0] == "目錄"  # inserted at the top, before the headings
    assert texts.index("第一章 緒論") == 1
    # Word is asked to refresh the field on open.
    settings = again.native.settings.element
    assert settings.find(W + "updateFields").get(W + "val") == "true"


def test_field_toc_hyperlinks_point_to_heading_bookmarks() -> None:
    doc = _doc()
    doc.add_toc()
    body = _reload(doc).native.element.body
    anchors = {h.get(W + "anchor") for h in body.iter(W + "hyperlink")}
    marks = {b.get(W + "name") for b in body.iter(W + "bookmarkStart")}
    assert len(anchors) == 4 and anchors == marks
    ids = [b.get(W + "id") for b in body.iter(W + "bookmarkStart")]
    assert len(ids) == len(set(ids))


def test_toc_levels_styles_created_with_indent() -> None:
    doc = _doc()
    doc.add_toc(levels="1-3")
    styles = {s.style_id: s for s in doc.native.styles}
    assert {"TOC1", "TOC2", "TOC3"} <= set(styles)
    assert styles["TOC2"].paragraph_format.left_indent > styles["TOC1"].paragraph_format.left_indent
    assert "TOC4" not in styles


def test_static_toc_has_no_field_and_is_detected() -> None:
    doc = _doc()
    entries = doc.add_static_toc(levels="1-2", title="Contents", hyperlinks=False)
    assert len(entries) == 3
    again = _reload(doc)
    info = again.toc_info()
    assert info["kind"] == "static" and info["instruction"] == ""
    assert info["entries"] == ["第一章 緒論", "背景", "第二章 方法"]
    body = again.native.element.body
    assert not list(body.iter(W + "fldChar"))
    assert not list(body.iter(W + "hyperlink"))
    assert again.native.paragraphs[-4].text == "Contents"


def test_static_toc_appended_at_end_by_default() -> None:
    doc = _doc()
    doc.add_static_toc(title=None)
    texts = [p.text for p in doc.native.paragraphs]
    assert texts[-4:] == ["第一章 緒論", "背景", "細節", "第二章 方法"]


def test_page_break_after_toc() -> None:
    doc = _doc()
    doc.add_toc(index=0, page_break_after=True)
    paras = doc.native.paragraphs
    breaks = [p for p in paras if p._p.xpath('.//w:br[@w:type="page"]')]
    assert len(breaks) == 1
    assert paras.index(breaks[0]) < [p.text for p in paras].index("第一章 緒論") + 5


def test_duplicate_toc_rejected_and_remove() -> None:
    doc = _doc()
    doc.add_toc()
    assert doc.has_toc()
    with pytest.raises(OperationNotSupportedError):
        doc.add_static_toc()
    removed = doc.remove_toc()
    assert removed == 5  # title + 4 entries
    assert not doc.has_toc()
    assert doc.toc_info() == {"kind": None, "instruction": "", "entries": []}
    assert doc.remove_toc() == 0
    assert doc.native.settings.element.find(W + "updateFields") is None
    doc.add_static_toc()  # allowed again
    assert doc.toc_info()["kind"] == "static"


def test_remove_static_toc() -> None:
    doc = _doc()
    doc.add_static_toc(title=None)
    assert doc.remove_toc() == 4
    assert not doc.has_toc()
    assert [p.text for p in doc.native.paragraphs if p.text.startswith("背景")] == ["背景"]


def test_no_update_on_open_and_empty_document() -> None:
    doc = DocxHandler().create()
    assert doc.add_toc(update_on_open=False) == []
    assert doc.has_toc() and doc.toc_info()["kind"] == "field"
    assert doc.native.settings.element.find(W + "updateFields") is None
    assert _reload(doc).toc_info()["kind"] == "field"


def test_index_out_of_range_and_end_index() -> None:
    doc = _doc()
    with pytest.raises(IndexError):
        doc.add_toc(index=99)
    n = len(doc.native.paragraphs)
    doc.add_static_toc(index=n, title=None)
    assert doc.native.paragraphs[-1].text == "第二章 方法"


def test_existing_bookmarks_reused_and_file_opens_in_python_docx() -> None:
    doc = _doc()
    doc.add_static_toc(title=None)
    first = {b.get(W + "name") for b in doc.native.element.body.iter(W + "bookmarkStart")}
    doc.remove_toc()
    doc.add_toc(title=None)
    second = {b.get(W + "name") for b in doc.native.element.body.iter(W + "bookmarkStart")}
    assert first == second
    reopened = load_docx(__import__("io").BytesIO(doc.to_bytes()))
    assert reopened.paragraphs


def test_headings_text_extraction_still_ordered() -> None:
    doc = _doc()
    doc.add_toc(index=0)
    text = _reload(doc).text()
    assert text.index("目錄") < text.index("第一章 緒論")


@pytest.mark.requires("libreoffice")
def test_libreoffice_renders_toc(tmp_path: Path) -> None:
    import anydoc

    doc = _doc()
    doc.add_toc(index=0, page_break_after=True)
    src = tmp_path / "toc.docx"
    doc.save(src)
    out = tmp_path / "toc.pdf"
    anydoc.convert(src, out)
    text = anydoc.extract_text(out)
    assert "目錄" in text
    assert text.count("第一章 緒論") >= 2  # TOC entry + heading
    assert text.count("第二章 方法") >= 2
