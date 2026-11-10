"""Tests for PPTX slide management (T070)."""

from __future__ import annotations

import io
from pathlib import Path

import pytest
from pptx import Presentation
from pptx.util import Inches

import anydoc
from anydoc.core.errors import AnyDocError
from anydoc.core.model import Capability
from anydoc.core.registry import registry
from anydoc.external import convert_with_libreoffice
from anydoc.formats.pptx import PptxDocument, PptxHandler


@pytest.fixture(autouse=True)
def _handler() -> None:
    registry.register(PptxHandler, replace=True)


def _doc(n: int = 3) -> PptxDocument:
    doc = PptxHandler().create()
    for i in range(n):
        doc.add_slide("Title and Content", title=f"第{i + 1}頁", body=[f"內容{i + 1}", "第二行"])
    return doc


def _titles(doc: PptxDocument) -> list[str | None]:
    return [s.title for s in doc.slides()]


def _reopen(doc: PptxDocument) -> PptxDocument:
    return PptxHandler().open(doc.to_bytes())


def test_capabilities_and_create() -> None:
    assert {Capability.WRITE, Capability.EDIT} <= PptxHandler.capabilities
    doc = PptxHandler().create()
    assert doc.metadata.page_count == 0
    assert doc.layouts()


def test_add_slide_by_name_and_index() -> None:
    doc = PptxHandler().create()
    i = doc.add_slide("Title Slide", title="封面", body="副標題")
    j = doc.add_slide(1, title="內容", body=["甲", "乙"], notes="講稿")
    assert (i, j) == (0, 1)
    s = doc.slide(1)
    assert s.layout == "Title and Content"
    assert s.title == "內容"
    assert "甲\n乙" in doc.slide_text(1)
    assert s.notes == "講稿"
    assert "副標題" in doc.slide_text(0)
    assert doc.metadata.page_count == 2


def test_add_slide_insert_position() -> None:
    doc = _doc(2)
    idx = doc.add_slide("Title Only", index=0, title="最前")
    assert idx == 0
    assert _titles(_reopen(doc)) == ["最前", "第1頁", "第2頁"]
    doc.add_slide("Title Only", index=3, title="最後")
    assert _titles(doc)[-1] == "最後"


def test_add_slide_errors() -> None:
    doc = _doc(1)
    with pytest.raises(ValueError):
        doc.add_slide("No Such Layout")
    with pytest.raises(ValueError):
        doc.add_slide(99)
    with pytest.raises(IndexError):
        doc.add_slide(1, index=5)
    with pytest.raises(AnyDocError):
        doc.add_slide("Blank", title="x")
    with pytest.raises(AnyDocError):
        doc.add_slide("Title Only", body="x")


def test_delete_slide_removes_part() -> None:
    doc = _doc(3)
    doc.delete_slide(1)
    assert _titles(doc) == ["第1頁", "第3頁"]
    again = _reopen(doc)
    assert _titles(again) == ["第1頁", "第3頁"]
    import zipfile

    names = zipfile.ZipFile(io.BytesIO(doc.to_bytes())).namelist()
    assert sum(1 for n in names if n.startswith("ppt/slides/slide")) == 2
    with pytest.raises(IndexError):
        doc.delete_slide(5)


def test_move_and_reorder() -> None:
    doc = _doc(4)
    doc.move_slide(0, 3)
    assert _titles(doc) == ["第2頁", "第3頁", "第4頁", "第1頁"]
    doc.move_slide(3, 0)
    assert _titles(doc) == ["第1頁", "第2頁", "第3頁", "第4頁"]
    doc.reorder_slides([3, 2, 1, 0])
    assert _titles(_reopen(doc)) == ["第4頁", "第3頁", "第2頁", "第1頁"]
    with pytest.raises(ValueError):
        doc.reorder_slides([0, 1])
    with pytest.raises(ValueError):
        doc.reorder_slides([0, 0, 1, 2])
    with pytest.raises(IndexError):
        doc.move_slide(0, 9)


def test_duplicate_slide_with_picture_and_notes(tmp_path: Path) -> None:
    from tests.fixtures.generate import make_png

    png = tmp_path / "p.png"
    png.write_bytes(make_png())
    doc = _doc(2)
    doc.slide(0)
    slide = doc.native.slides[0]
    slide.shapes.add_picture(str(png), Inches(1), Inches(1))
    doc.set_notes(0, "備忘稿")
    new = doc.duplicate_slide(0)
    assert new == 1
    again = _reopen(doc)
    titles = _titles(again)
    assert titles == ["第1頁", "第1頁", "第2頁"]
    for i in (0, 1):
        kinds = [s.kind for s in again.slide(i).shapes]
        assert "picture" in kinds
        assert again.slide(i).notes == "備忘稿"
        pic = [s for s in again.native.slides[i].shapes if s.shape_type == 13][0]
        assert pic.image.blob.startswith(b"\x89PNG")
    # the copy is independent of the original
    doc.native.slides[1].shapes.title.text_frame.text = "改過"
    assert _titles(doc) == ["第1頁", "改過", "第2頁"]


def test_duplicate_to_position_and_errors() -> None:
    doc = _doc(3)
    assert doc.duplicate_slide(0, to=2) == 2
    assert _titles(doc) == ["第1頁", "第2頁", "第1頁", "第3頁"]
    with pytest.raises(IndexError):
        doc.duplicate_slide(9)
    with pytest.raises(IndexError):
        doc.duplicate_slide(0, to=9)


def test_notes_roundtrip_and_clear() -> None:
    doc = _doc(2)
    assert doc.get_notes(0) == ""
    doc.set_notes(0, "第一頁講稿\n換行")
    assert _reopen(doc).get_notes(0) == "第一頁講稿\n換行"
    doc.set_notes(0, "")
    assert doc.get_notes(0) == ""
    doc.set_notes(1, "")  # no notes slide created for empty text
    assert not doc.native.slides[1].has_notes_slide
    with pytest.raises(IndexError):
        doc.set_notes(9, "x")
    with pytest.raises(IndexError):
        doc.get_notes(9)


def test_edit_existing_file_and_save(tmp_path: Path) -> None:
    path = tmp_path / "a.pptx"
    prs = Presentation()
    s = prs.slides.add_slide(prs.slide_layouts[1])
    s.shapes.title.text = "原有"  # type: ignore[union-attr]
    prs.save(str(path))
    doc = anydoc.open(path)
    assert isinstance(doc, PptxDocument)
    doc.add_slide(1, title="新增")
    doc.move_slide(1, 0)
    doc.save()
    again = anydoc.open(path)
    assert isinstance(again, PptxDocument)
    assert _titles(again) == ["新增", "原有"]
    assert again.metadata.page_count == 2


@pytest.mark.requires("libreoffice")
def test_libreoffice_opens_edited_deck(tmp_path: Path) -> None:
    doc = _doc(3)
    doc.duplicate_slide(0)
    doc.delete_slide(2)
    doc.move_slide(0, 2)
    src = tmp_path / "e.pptx"
    src.write_bytes(doc.to_bytes())
    out = convert_with_libreoffice(src, "pdf", tmp_path / "out")
    assert out.read_bytes().startswith(b"%PDF")
