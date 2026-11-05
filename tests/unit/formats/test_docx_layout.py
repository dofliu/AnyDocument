"""Tests for DOCX headers, footers, sections and page layout (T053)."""

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
    return DocxHandler().create()


def _reload(doc: DocxDocument) -> DocxDocument:
    return DocxHandler().open(doc.to_bytes())


def test_paper_sizes_and_orientation() -> None:
    doc = _doc()
    doc.set_page_size("A4")
    info = doc.sections()[0]
    assert (info.page_width, info.page_height, info.paper) == (210.0, 297.0, "A4")
    assert info.orientation == "portrait"
    doc.set_page_size("Letter", orientation="landscape")
    info = _reload(doc).sections()[0]
    assert info.orientation == "landscape"
    assert info.page_width > info.page_height
    assert info.paper == "LETTER"
    doc.set_orientation("portrait")
    assert doc.sections()[0].page_width < doc.sections()[0].page_height
    doc.set_orientation("portrait")  # idempotent
    assert doc.sections()[0].page_width < doc.sections()[0].page_height


def test_custom_size_and_landscape_keeps_paper_name() -> None:
    doc = _doc()
    doc.set_page_size(width=100, height=150)
    assert (doc.sections()[0].page_width, doc.sections()[0].page_height) == (100.0, 150.0)
    assert doc.sections()[0].paper is None
    doc.set_page_size("A3")
    doc.set_orientation("landscape")
    doc.set_page_size("A5")  # stays landscape
    info = doc.sections()[0]
    assert info.orientation == "landscape" and info.paper == "A5"
    assert info.page_width > info.page_height


def test_page_size_errors() -> None:
    doc = _doc()
    with pytest.raises(ValueError):
        doc.set_page_size("Tabloid9")
    with pytest.raises(ValueError):
        doc.set_page_size(width=100)
    with pytest.raises(ValueError):
        doc.set_page_size(width=-1, height=10)
    with pytest.raises(ValueError):
        doc.set_orientation("diagonal")
    with pytest.raises(IndexError):
        doc.set_orientation("portrait", section=5)


def test_margins_roundtrip() -> None:
    doc = _doc()
    doc.set_margins(top=20, bottom=25, left=30, right=15, header=10, footer=12, gutter=5)
    info = _reload(doc).sections()[0]
    assert (info.margin_top, info.margin_bottom) == (20.0, 25.0)
    assert (info.margin_left, info.margin_right) == (30.0, 15.0)
    assert (info.header_distance, info.footer_distance) == (10.0, 12.0)
    doc.set_margins(top=21)
    assert doc.sections()[0].margin_left == 30.0
    with pytest.raises(ValueError):
        doc.set_margins(left=-1)


def test_sections_each_with_own_layout() -> None:
    doc = _doc()
    doc.add_paragraph("第一節")
    idx = doc.add_section("new_page")
    assert idx == 1
    doc.set_orientation("landscape", section=1)
    doc.add_paragraph("第二節橫向")
    idx = doc.add_section("continuous")
    assert idx == 2
    infos = _reload(doc).sections()
    assert [i.orientation for i in infos] == ["portrait", "landscape", "landscape"]
    assert [i.start_type for i in infos] == ["new_page", "new_page", "continuous"]
    assert [i.index for i in infos] == [0, 1, 2]
    with pytest.raises(ValueError):
        doc.add_section("bogus")


def test_header_and_footer_text_chinese() -> None:
    doc = _doc()
    doc.set_header("機密文件 Confidential", alignment="right", bold=True)
    doc.set_footer("頁尾說明", alignment="center", size=9)
    out = _reload(doc)
    assert out.headers_footers()[0] == {"header": "機密文件 Confidential", "footer": "頁尾說明"}
    paragraph = load_docx(__import__("io").BytesIO(doc.to_bytes())).sections[0].header.paragraphs[0]
    assert paragraph.alignment == 2
    assert paragraph.runs[0].bold is True
    # Replacing leaves only the new content.
    doc.set_header("新標題")
    assert doc.headers_footers()[0]["header"] == "新標題"


def test_page_number_fields() -> None:
    doc = _doc()
    doc.set_footer("第 {page} 頁，共 {pages} 頁", alignment="center")
    out = _reload(doc)
    assert out.header_footer_fields("footer") == ["PAGE", "NUMPAGES"]
    assert "第" in out.headers_footers()[0]["footer"]
    doc2 = _doc()
    doc2.add_page_number()
    assert doc2.header_footer_fields("footer") == ["PAGE"]
    doc2.add_page_number(position="header", alignment="right", template="Page {page}/{pages}")
    assert doc2.header_footer_fields("header") == ["PAGE", "NUMPAGES"]
    with pytest.raises(OperationNotSupportedError):
        doc2.add_page_number(position="side")


def test_page_number_flags_append_fields() -> None:
    doc = _doc()
    doc.set_footer("報告", page_number=True, total_pages=True)
    assert doc.header_footer_fields("footer") == ["PAGE", "NUMPAGES"]
    assert doc.headers_footers()[0]["footer"].startswith("報告")
    doc.set_footer("", page_number=True)
    assert doc.header_footer_fields("footer") == ["PAGE"]


def test_first_page_and_even_variants() -> None:
    doc = _doc()
    doc.set_header("一般頁首")
    doc.set_header("首頁頁首", which="first")
    doc.set_footer("{page}", which="even")
    out = load_docx(__import__("io").BytesIO(doc.to_bytes()))
    section = out.sections[0]
    assert section.different_first_page_header_footer is True
    assert section.first_page_header.paragraphs[0].text == "首頁頁首"
    assert section.header.paragraphs[0].text == "一般頁首"
    assert out.settings.odd_and_even_pages_header_footer is True
    assert doc.header_footer_fields("footer", which="even") == ["PAGE"]
    assert doc.sections()[0].different_first_page is True
    with pytest.raises(ValueError):
        doc.set_header("x", which="third")


def test_per_section_headers_independent() -> None:
    doc = _doc()
    doc.set_header("A 節", section=0)
    doc.add_section()
    doc.set_header("B 節", section=1)
    out = _reload(doc).headers_footers()
    assert [h["header"] for h in out] == ["A 節", "B 節"]
    # A new section without its own header stays linked (nothing to read).
    doc.add_section()
    assert doc.header_footer_fields("header", section=2) == []


def test_header_text_found_by_replace_and_file_opens(tmp_path: Path) -> None:
    doc = _doc()
    doc.add_paragraph("內文")
    doc.set_header("公司名稱")
    doc.set_page_size("A4", orientation="landscape")
    doc.replace_text("公司", "子公司")
    path = tmp_path / "out.docx"
    doc.save(path)
    reopened = load_docx(str(path))
    assert reopened.sections[0].header.paragraphs[0].text == "子公司名稱"
    assert reopened.sections[0].orientation == 1
