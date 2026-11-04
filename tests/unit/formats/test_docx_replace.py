"""Tests for DOCX find and replace (T051)."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

import anydoc
from anydoc.core.registry import registry
from anydoc.formats.docx import DocxDocument, DocxHandler
from anydoc.formats.docx.replace import compile_pattern


@pytest.fixture(autouse=True)
def _handler() -> None:
    registry.register(DocxHandler, replace=True)


def _doc() -> DocxDocument:
    return DocxHandler().create()


def _split_paragraph(doc: DocxDocument, parts: list[tuple[str, dict[str, bool]]]) -> None:
    p = doc.native.add_paragraph()
    for text, fmt in parts:
        run = p.add_run(text)
        run.bold = fmt.get("bold")
        run.italic = fmt.get("italic")


def test_simple_replace_and_count() -> None:
    doc = _doc()
    doc.add_paragraph("Hello world, hello World")
    assert doc.replace_text("world", "there", ignore_case=True) == 2
    assert doc.text() == "Hello there, hello there"


def test_case_sensitive_by_default() -> None:
    doc = _doc()
    doc.add_paragraph("Apple apple")
    assert doc.replace_text("apple", "pear") == 1
    assert doc.text() == "Apple pear"


def test_cross_run_match_keeps_first_run_format() -> None:
    doc = _doc()
    _split_paragraph(
        doc, [("Dear ", {}), ("Na", {"bold": True}), ("me", {"italic": True}), (",", {})]
    )
    assert doc.replace_text("Name", "老師") == 1
    p = doc.native.paragraphs[0]
    assert p.text == "Dear 老師,"
    runs = p.runs
    assert runs[1].text == "老師" and runs[1].bold is True
    assert runs[2].text == ""
    assert runs[3].text == ","


def test_replace_preserves_surrounding_formatting() -> None:
    doc = _doc()
    _split_paragraph(doc, [("plain ", {}), ("BOLD", {"bold": True}), (" tail", {})])
    doc.replace_text("BOLD", "strong")
    runs = doc.native.paragraphs[0].runs
    assert [r.text for r in runs] == ["plain ", "strong", " tail"]
    assert runs[1].bold is True and runs[0].bold is None


def test_regex_with_groups() -> None:
    doc = _doc()
    doc.add_paragraph("2026-11-04 and 2025-01-02")
    assert doc.replace_text(r"(\d{4})-(\d\d)-(\d\d)", r"\3/\2/\1", regex=True) == 2
    assert doc.text() == "04/11/2026 and 02/01/2025"


def test_whole_word() -> None:
    doc = _doc()
    doc.add_paragraph("cat concat cat.")
    assert doc.replace_text("cat", "dog", whole_word=True) == 2
    assert doc.text() == "dog concat dog."


def test_literal_special_chars_escaped() -> None:
    doc = _doc()
    doc.add_paragraph("price (a+b) = 1.5")
    assert doc.replace_text("(a+b)", "X") == 1
    assert doc.replace_text(".", ",") == 1
    assert doc.text() == "price X = 1,5"


def test_count_limit() -> None:
    doc = _doc()
    doc.add_paragraph("a a a")
    doc.add_paragraph("a a")
    assert doc.replace_text("a", "b", count=4) == 4
    assert doc.text() == "b b b\nb a"
    assert doc.replace_text("a", "c", count=0) == 1


def test_tables_including_nested() -> None:
    doc = _doc()
    table = doc.native.add_table(rows=1, cols=2)
    table.cell(0, 0).text = "名稱：{{name}}"
    nested = table.cell(0, 1).add_table(rows=1, cols=1)
    nested.cell(0, 0).text = "{{name}} nested"
    assert doc.replace_text("{{name}}", "王小明") == 2
    assert table.cell(0, 0).text == "名稱：王小明"
    assert nested.cell(0, 0).text == "王小明 nested"


def test_headers_and_footers() -> None:
    doc = _doc()
    doc.add_paragraph("body TOKEN")
    section = doc.native.sections[0]
    section.header.paragraphs[0].text = "head TOKEN"
    section.footer.paragraphs[0].text = "foot TOKEN"
    assert doc.replace_text("TOKEN", "ok", headers_footers=False) == 1
    assert section.header.paragraphs[0].text == "head TOKEN"
    assert doc.replace_text("TOKEN", "ok", body=False) == 2
    assert section.header.paragraphs[0].text == "head ok"
    assert section.footer.paragraphs[0].text == "foot ok"


def test_find_text_does_not_modify() -> None:
    doc = _doc()
    _split_paragraph(doc, [("ab", {"bold": True}), ("cd", {})])
    doc.native.sections[0].footer.paragraphs[0].text = "bc"
    found = doc.find_text("bc")
    assert [(m.location, m.text) for m in found] == [("body", "bc"), ("footer", "bc")]
    assert doc.text() == "abcd"


def test_no_match_and_empty_paragraph() -> None:
    doc = _doc()
    doc.add_paragraph("")
    doc.add_paragraph("abc")
    assert doc.replace_text("zzz", "y") == 0
    assert doc.text() == "\nabc"


def test_delete_via_empty_replacement_and_leading_space_preserved() -> None:
    doc = _doc()
    doc.add_paragraph("keep [x] this")
    assert doc.replace_text("[x]", "") == 1
    assert doc.text() == "keep  this"
    doc.replace_text("keep", " keep")
    assert doc.text() == " keep  this"


def test_tab_and_break_runs_untouched() -> None:
    doc = _doc()
    p = doc.native.add_paragraph()
    p.add_run("a\tb")
    assert doc.replace_text("a", "x") == 1
    assert p.text == "x\tb"


def test_errors() -> None:
    doc = _doc()
    doc.add_paragraph("x")
    with pytest.raises(ValueError):
        doc.replace_text("", "y")
    with pytest.raises(ValueError):
        doc.replace_text("(", "y", regex=True)
    with pytest.raises(ValueError):
        compile_pattern("")
    with pytest.raises((ValueError, re.error, IndexError)):
        doc.replace_text("x", r"\9", regex=True)


def test_roundtrip_through_file(tmp_path: Path) -> None:
    doc = _doc()
    doc.add_heading("Report {{year}}", 1)
    doc.add_paragraph("中文 {{year}}")
    out = doc.save(tmp_path / "t.docx")
    again = anydoc.open(out)
    assert isinstance(again, DocxDocument)
    assert again.replace_text("{{year}}", "2026") == 2
    again.save()
    final = anydoc.open(out)
    assert final.text() == "Report 2026\n中文 2026"
    assert final.headings()[0].heading_level == 1  # type: ignore[attr-defined]
