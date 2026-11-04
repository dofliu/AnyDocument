"""Tests for DOCX creation and basic editing (T050)."""

from __future__ import annotations

from pathlib import Path

import pytest

import anydoc
from anydoc.core.errors import OperationNotSupportedError
from anydoc.core.model import Capability
from anydoc.core.registry import registry
from anydoc.formats.docx import DocxDocument, DocxHandler
from anydoc.formats.docx import edit as docx_edit


@pytest.fixture(autouse=True)
def _handler() -> None:
    registry.register(DocxHandler, replace=True)


def _new() -> DocxDocument:
    return DocxHandler().create()


def test_capabilities_include_write_and_edit() -> None:
    assert {Capability.WRITE, Capability.EDIT} <= DocxHandler.capabilities


def test_create_save_and_reopen(tmp_path: Path) -> None:
    doc = _new()
    doc.set_properties(title="報告", author="劉老師")
    doc.add_heading("標題 Title", 0)
    doc.add_heading("第一章", 1)
    doc.add_paragraph("這是一段中文內文 with English.")
    out = doc.save(tmp_path / "new.docx")
    assert out.exists()
    again = anydoc.open(out)
    assert isinstance(again, DocxDocument)
    assert again.metadata.title == "報告"
    assert again.metadata.author == "劉老師"
    assert [(h.heading_level, h.text) for h in again.headings()] == [
        (0, "標題 Title"),
        (1, "第一章"),
    ]
    assert "這是一段中文內文 with English." in again.text()


def test_create_without_path_cannot_save() -> None:
    with pytest.raises(ValueError):
        _new().save()


def test_to_bytes_roundtrip() -> None:
    doc = _new()
    doc.add_paragraph("bytes")
    reopened = DocxHandler().open(doc.to_bytes())
    assert reopened.text() == "bytes"


def test_text_formatting() -> None:
    doc = _new()
    p = doc.add_paragraph(
        "styled",
        bold=True,
        italic=True,
        underline=True,
        strike=True,
        font="標楷體",
        size=14,
        color="#FF0000",
        alignment="center",
    )
    run = p.runs[0]
    assert run.bold and run.italic and run.underline and run.font.strike
    assert run.font.name == "標楷體"
    assert run.font.size is not None and run.font.size.pt == 14
    assert str(run.font.color.rgb) == "FF0000"
    assert p.alignment == 1
    assert "eastAsia" in run._r.xml


def test_empty_paragraph_and_style() -> None:
    doc = _new()
    doc.add_paragraph()
    doc.add_paragraph("quoted", style="Quote")
    infos = doc.paragraphs()
    assert infos[0].text == ""
    assert infos[1].style == "Quote"


def test_lists() -> None:
    doc = _new()
    doc.add_list(["a", "b"])
    doc.add_list(["one", "two"], ordered=True)
    doc.add_list(["nested"], level=1)
    styles = [p.style for p in doc.paragraphs()]
    assert styles == [
        "List Bullet",
        "List Bullet",
        "List Number",
        "List Number",
        "List Bullet 2",
    ]


def test_page_break() -> None:
    doc = _new()
    doc.add_paragraph("before")
    doc.add_page_break()
    doc.add_paragraph("after")
    assert 'w:type="page"' in doc.native.element.xml
    reopened = DocxHandler().open(doc.to_bytes())
    assert reopened.text().splitlines()[-1] == "after"


def test_edit_existing_paragraphs(tmp_path: Path) -> None:
    doc = _new()
    doc.add_heading("H", 1)
    first = doc.add_paragraph("first", bold=True)
    first.add_run(" extra")
    doc.add_paragraph("third")
    doc.set_paragraph_text(1, "FIRST")
    assert doc.paragraphs()[1].text == "FIRST"
    assert doc.native.paragraphs[1].runs[0].bold is True
    doc.insert_paragraph(1, "second", style="Quote")
    assert [p.text for p in doc.paragraphs()] == ["H", "FIRST", "second", "third"]
    assert doc.paragraphs()[2].style == "Quote"
    doc.delete_paragraph(0)
    assert [p.text for p in doc.paragraphs()] == ["FIRST", "second", "third"]
    out = doc.save(tmp_path / "e.docx")
    assert anydoc.open(out).text() == "FIRST\nsecond\nthird"
    doc.set_paragraph_text(-1, "last")
    assert doc.paragraphs()[-1].text == "last"


def test_set_text_on_empty_paragraph() -> None:
    doc = _new()
    doc.add_paragraph()
    doc.set_paragraph_text(0, "filled", italic=True)
    assert doc.paragraphs()[0].text == "filled"
    assert doc.native.paragraphs[0].runs[0].italic is True


def test_edit_opened_file_preserves_content(tmp_path: Path, sample: object) -> None:
    src = sample("docx")  # type: ignore[operator]
    doc = anydoc.open(src)
    assert isinstance(doc, DocxDocument)
    before = doc.text()
    doc.add_paragraph("追加段落")
    out = doc.save(tmp_path / "edited.docx")
    after = anydoc.open(out).text()
    assert after.startswith(before)
    assert after.endswith("追加段落")


def test_errors() -> None:
    doc = _new()
    with pytest.raises(ValueError):
        doc.add_heading("x", 10)
    with pytest.raises(ValueError):
        doc.add_list(["x"], level=3)
    with pytest.raises(ValueError):
        doc.add_paragraph("x", alignment="diagonal")
    with pytest.raises(ValueError):
        doc.add_paragraph("x", style="No Such Style")
    with pytest.raises(ValueError):
        doc.add_paragraph("x", color="red")
    with pytest.raises(ValueError):
        docx_edit.parse_color("GGGGGG")
    with pytest.raises(OperationNotSupportedError):
        doc.delete_paragraph(5)
    with pytest.raises(OperationNotSupportedError):
        doc.set_paragraph_text(0, "x")


def test_registry_create() -> None:
    handler = registry.get("docx")
    assert isinstance(handler().create(), DocxDocument)
