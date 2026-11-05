"""Tests for DOCX tracked changes and comments (T055)."""

# ruff: noqa: E501  (inline WordprocessingML fixtures)

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest
from docx import Document as load_docx
from docx.oxml import parse_xml
from lxml import etree

from anydoc.core.registry import registry
from anydoc.formats.docx import DocxDocument, DocxHandler

NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
W = f"{{{NS}}}"


@pytest.fixture(autouse=True)
def _handler() -> None:
    registry.register(DocxHandler, replace=True)


def _tracked() -> DocxDocument:
    """A document with insert/delete/move/format/paragraph-mark/row revisions."""
    doc = DocxHandler().create()
    body = doc.native.element.body
    sect = body.find(f"{W}sectPr")

    def add(xml: str) -> None:
        el = parse_xml(xml.replace("<w:p>", f'<w:p xmlns:w="{NS}">', 1))
        if sect is not None:
            sect.addprevious(el)
        else:
            body.append(el)

    add(
        '<w:p><w:r><w:t xml:space="preserve">保留 </w:t></w:r>'
        '<w:ins w:id="1" w:author="Alice" w:date="2026-01-01T00:00:00Z">'
        "<w:r><w:t>新增文字</w:t></w:r></w:ins>"
        '<w:del w:id="2" w:author="Bob" w:date="2026-01-02T00:00:00Z">'
        "<w:r><w:delText>刪除文字</w:delText></w:r></w:del></w:p>"
    )
    add(
        '<w:p><w:pPr><w:rPr><w:ins w:id="3" w:author="Alice" w:date="2026-01-03T00:00:00Z"/></w:rPr></w:pPr>'
        "<w:r><w:t>前半</w:t></w:r></w:p>"
    )
    add("<w:p><w:r><w:t>後半</w:t></w:r></w:p>")
    add(
        '<w:p><w:pPr><w:rPr><w:del w:id="4" w:author="Bob" w:date="2026-01-04T00:00:00Z"/></w:rPr></w:pPr>'
        "<w:r><w:t>甲</w:t></w:r></w:p>"
    )
    add("<w:p><w:r><w:t>乙</w:t></w:r></w:p>")
    add(
        '<w:p><w:r><w:rPr><w:b/><w:rPrChange w:id="5" w:author="Alice" w:date="2026-01-05T00:00:00Z">'
        "<w:rPr/></w:rPrChange></w:rPr><w:t>粗體</w:t></w:r></w:p>"
    )
    add(
        '<w:p><w:moveFrom w:id="6" w:author="Bob" w:date="2026-01-06T00:00:00Z">'
        "<w:r><w:t>搬走</w:t></w:r></w:moveFrom>"
        '<w:moveTo w:id="7" w:author="Bob" w:date="2026-01-06T00:00:00Z">'
        "<w:r><w:t>搬來</w:t></w:r></w:moveTo></w:p>"
    )
    return doc


def _reload(doc: DocxDocument) -> DocxDocument:
    return DocxHandler().open(doc.to_bytes())


def _texts(doc: DocxDocument) -> list[str]:
    return [p.text for p in doc.native.paragraphs]


# -- reading ----------------------------------------------------------------


def test_list_revisions_kinds_authors_and_text() -> None:
    doc = _tracked()
    revs = doc.revisions()
    assert doc.has_revisions()
    by_id = {r.id: r for r in revs}
    assert by_id[1].kind == "insert" and by_id[1].text == "新增文字" and by_id[1].author == "Alice"
    assert by_id[2].kind == "delete" and by_id[2].text == "刪除文字"
    assert by_id[3].kind == "paragraph_insert"
    assert by_id[4].kind == "paragraph_delete"
    assert by_id[5].kind == "format" and by_id[5].text == "rPr"
    assert by_id[6].kind == "move_from" and by_id[7].kind == "move_to"
    assert by_id[1].date == "2026-01-01T00:00:00Z"
    assert [r.id for r in revs] == [1, 2, 3, 4, 5, 6, 7]


def test_no_revisions_in_plain_document() -> None:
    doc = DocxHandler().create()
    doc.add_paragraph("普通")
    assert not doc.has_revisions()
    assert doc.revisions() == []
    assert doc.accept_revisions() == 0


def test_revisions_survive_roundtrip() -> None:
    assert len(_reload(_tracked()).revisions()) == 7


# -- accept / reject ---------------------------------------------------------


def test_accept_all() -> None:
    doc = _tracked()
    assert doc.accept_revisions() == 7
    assert not doc.has_revisions()
    texts = _texts(doc)
    assert "保留 新增文字" in texts  # insertion kept, deletion gone
    assert "前半後半" not in texts and "前半" in texts  # inserted mark kept: paragraphs stay split
    assert "甲乙" in texts  # deleted mark: paragraphs merged
    assert "粗體" in texts
    assert "搬來" in texts and all("搬走" not in t for t in texts)
    assert _reload(doc).revisions() == []


def test_reject_all() -> None:
    doc = _tracked()
    assert doc.reject_revisions() == 7
    assert not doc.has_revisions()
    texts = _texts(doc)
    assert "保留 刪除文字" in texts  # insertion removed, deletion restored as normal text
    assert "前半後半" in texts  # inserted paragraph mark rejected: merged
    assert "甲" in texts and "乙" in texts  # deleted mark rejected: stay split
    assert "搬走" in texts and all("搬來" not in t for t in texts)
    xml = etree.tostring(doc.native.element)
    assert b"delText" not in xml


def test_reject_format_change_restores_old_properties() -> None:
    doc = _tracked()
    doc.reject_revisions(ids={5})
    run = next(r for p in doc.native.paragraphs for r in p.runs if r.text == "粗體")
    assert run.bold is None
    assert len(doc.revisions()) == 6


def test_accept_format_change_keeps_new_properties() -> None:
    doc = _tracked()
    doc.accept_revisions(ids={5})
    run = next(r for p in doc.native.paragraphs for r in p.runs if r.text == "粗體")
    assert run.bold is True


def test_filter_by_author_and_ids() -> None:
    doc = _tracked()
    assert doc.accept_revisions(author="Alice") == 3  # ids 1, 3, 5
    remaining = {r.id for r in doc.revisions()}
    assert remaining == {2, 4, 6, 7}
    assert doc.reject_revisions(ids={2}) == 1
    assert {r.id for r in doc.revisions()} == {4, 6, 7}
    assert doc.accept_revisions(ids={999}) == 0


def test_nested_insert_with_delete_rejected_once() -> None:
    doc = DocxHandler().create()
    body = doc.native.element.body
    p = parse_xml(
        f'<w:p xmlns:w="{NS}"><w:ins w:id="1" w:author="A"><w:del w:id="2" w:author="B">'
        "<w:r><w:delText>x</w:delText></w:r></w:del></w:ins></w:p>"
    )
    body.insert(0, p)
    assert doc.reject_revisions() == 1  # inner del went away with the insertion
    assert not doc.has_revisions()


def test_accept_nested_insert_with_delete() -> None:
    doc = DocxHandler().create()
    p = parse_xml(
        f'<w:p xmlns:w="{NS}"><w:r><w:t>a</w:t></w:r><w:ins w:id="1" w:author="A"><w:del w:id="2" w:author="B">'
        "<w:r><w:delText>x</w:delText></w:r></w:del></w:ins></w:p>"
    )
    doc.native.element.body.insert(0, p)
    assert doc.accept_revisions() == 2
    assert doc.native.paragraphs[0].text == "a"


def test_table_row_revisions() -> None:
    doc = DocxHandler().create()
    doc.add_table([["a", "b"], ["c", "d"], ["e", "f"]])
    rows = doc.native.tables[0]._tbl.findall(f"{W}tr")
    for row, tag, rid in ((rows[1], "ins", 10), (rows[2], "del", 11)):
        trpr = row.find(f"{W}trPr")
        if trpr is None:
            trpr = etree.SubElement(row, f"{W}trPr")
            row.insert(0, trpr)
        marker = etree.SubElement(trpr, f"{W}{tag}")
        marker.set(f"{W}id", str(rid))
        marker.set(f"{W}author", "Eve")
    kinds = {r.kind: r.text for r in doc.revisions()}
    assert kinds["row_insert"] == "c | d" and kinds["row_delete"] == "e | f"

    accepted = _reload(doc)
    accepted.accept_revisions()
    assert [[c.text for c in r.cells] for r in accepted.native.tables[0].rows] == [
        ["a", "b"],
        ["c", "d"],
    ]
    rejected = _reload(doc)
    rejected.reject_revisions()
    assert [[c.text for c in r.cells] for r in rejected.native.tables[0].rows] == [
        ["a", "b"],
        ["e", "f"],
    ]


def test_header_revisions_are_resolved() -> None:
    doc = DocxHandler().create()
    doc.set_header("頁首")
    header = doc.native.sections[0].header
    p = header.paragraphs[0]._p
    p.append(
        parse_xml(
            f'<w:ins xmlns:w="{NS}" w:id="20" w:author="A"><w:r><w:t>追加</w:t></w:r></w:ins>'
        )
    )
    assert [r.kind for r in doc.revisions()] == ["insert"]
    doc.accept_revisions()
    assert doc.native.sections[0].header.paragraphs[0].text == "頁首追加"


def test_text_extraction_after_accept_matches_final_text() -> None:
    doc = _tracked()
    doc.accept_revisions()
    assert "刪除文字" not in doc.text()


# -- track changes setting ---------------------------------------------------


def test_track_changes_setting_toggle_and_roundtrip() -> None:
    doc = DocxHandler().create()
    assert not doc.track_changes()
    doc.set_track_changes(True)
    doc.set_track_changes(True)  # idempotent
    assert doc.track_changes()
    assert _reload(doc).track_changes()
    doc.set_track_changes(False)
    assert not doc.track_changes()
    doc.set_track_changes(False)


# -- comments ---------------------------------------------------------------


def _commented() -> DocxDocument:
    doc = DocxHandler().create()
    doc.add_paragraph("第一段內容")
    doc.add_paragraph("請修正這個詞彙的用法")
    return doc


def test_add_comment_on_paragraph() -> None:
    doc = _commented()
    info = doc.add_comment("需要補充", paragraph=0, author="王老師")
    assert info.author == "王老師" and info.text == "需要補充"
    assert info.initials == "王"
    assert info.anchor_text == "第一段內容"
    again = _reload(doc).comments()
    assert [(c.text, c.author, c.anchor_text) for c in again] == [
        ("需要補充", "王老師", "第一段內容")
    ]


def test_add_comment_on_matched_text_across_runs() -> None:
    doc = DocxHandler().create()
    para = doc.native.add_paragraph()
    para.add_run("請修正")
    para.add_run("這個詞彙")
    para.add_run("的用法")
    info = doc.add_comment("改用「術語」", match="詞彙")
    assert info.anchor_text == "這個詞彙"  # whole run overlapping the match


def test_add_comment_defaults_and_multiple() -> None:
    doc = _commented()
    a = doc.add_comment("first")
    b = doc.add_comment("second", match="詞彙", author="Alice Smith")
    assert a.anchor_text == "第一段內容"
    assert b.anchor_text == "請修正這個詞彙的用法"
    assert b.initials == "AS"
    assert a.id != b.id
    assert [c.text for c in doc.comments()] == ["first", "second"]


def test_add_comment_errors() -> None:
    doc = _commented()
    with pytest.raises(ValueError, match="out of range"):
        doc.add_comment("x", paragraph=9)
    with pytest.raises(ValueError, match="not found in the document"):
        doc.add_comment("x", match="不存在")
    with pytest.raises(ValueError, match="not found in paragraph"):
        doc.add_comment("x", paragraph=0, match="詞彙")
    empty = DocxHandler().create()
    with pytest.raises(ValueError, match="no paragraphs"):
        empty.add_comment("x")
    blank = DocxHandler().create()
    blank.native.add_paragraph("")
    with pytest.raises(ValueError, match="no text runs"):
        blank.add_comment("x")


def test_delete_comment_removes_anchors() -> None:
    doc = _commented()
    a = doc.add_comment("one", paragraph=0)
    b = doc.add_comment("two", paragraph=1)
    doc.delete_comment(a.id)
    assert [c.id for c in doc.comments()] == [b.id]
    xml = etree.tostring(doc.native.element.body).decode()
    assert f'w:id="{a.id}"' not in xml.replace(f'w:id="{b.id}"', "")
    reopened = _reload(doc)
    assert [c.text for c in reopened.comments()] == ["two"]
    with pytest.raises(KeyError):
        doc.delete_comment(12345)


def test_comments_empty_document() -> None:
    assert DocxHandler().create().comments() == []


def test_saved_file_opens_in_python_docx(tmp_path: Path) -> None:
    doc = _tracked()
    doc.add_comment("留言", paragraph=0)
    out = doc.save(tmp_path / "r.docx")
    reloaded = load_docx(str(out))
    assert len(reloaded.comments) == 1


@pytest.mark.skipif(shutil.which("soffice") is None, reason="LibreOffice not available")
def test_libreoffice_renders_accepted_document(tmp_path: Path) -> None:
    doc = _tracked()
    doc.accept_revisions()
    doc.add_comment("留言", paragraph=0)
    src = doc.save(tmp_path / "r.docx")
    subprocess.run(
        ["soffice", "--headless", "--convert-to", "pdf", "--outdir", str(tmp_path), str(src)],
        check=True,
        capture_output=True,
        timeout=120,
    )
    assert (tmp_path / "r.pdf").stat().st_size > 0
