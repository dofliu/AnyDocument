"""Tests for DOCX <-> Markdown / HTML converters (T034)."""

from __future__ import annotations

import base64
import io
from pathlib import Path

import pytest
from docx import Document
from docx.opc.constants import RELATIONSHIP_TYPE as RT
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from PIL import Image
from typer.testing import CliRunner

import anydoc
from anydoc.cli import app
from anydoc.converters import (
    docx_to_html,
    docx_to_markdown,
    html_to_docx,
    load_builtin_converters,
    markdown_to_docx,
    register_docx_text,
)
from anydoc.converters.docx_text import docx_blocks
from anydoc.core.convert import ConversionGraph, graph
from anydoc.core.errors import CorruptFileError

runner = CliRunner()


def _png() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (40, 20), (200, 30, 30)).save(buf, "PNG")
    return buf.getvalue()


def _link(par, text: str, url: str) -> None:  # type: ignore[no-untyped-def]
    rid = par.part.relate_to(url, RT.HYPERLINK, is_external=True)
    h = OxmlElement("w:hyperlink")
    h.set(qn("r:id"), rid)
    par._p.append(h)
    run = par.add_run(text)
    h.append(run._r)


def _rich_docx(tmp_path: Path) -> Path:
    doc = Document()
    doc.core_properties.title = "測試文件"
    doc.add_heading("第一章 概述", level=1)
    p = doc.add_paragraph("普通 ")
    p.add_run("粗體").bold = True
    p.add_run(" 與 ")
    p.add_run("斜體").italic = True
    p.add_run(" 與 ")
    p.add_run("刪除").font.strike = True
    p.add_run(" 與 ")
    p.add_run("code_x").font.name = "Consolas"
    p.add_run(" 並連到 ")
    _link(p, "官網", "https://example.com/a b")
    doc.add_heading("小節", level=2)
    doc.add_paragraph("項目甲", style="List Bullet")
    doc.add_paragraph("子項目", style="List Bullet 2")
    doc.add_paragraph("項目乙", style="List Bullet")
    doc.add_paragraph("步驟一", style="List Number")
    doc.add_paragraph("步驟二", style="List Number")
    t = doc.add_table(rows=2, cols=2)
    for r, row in enumerate([["名稱", "數量"], ["蘋果|梨", "3"]]):
        for c, v in enumerate(row):
            t.cell(r, c).text = v
    doc.add_paragraph("引用文字", style="Quote")
    doc.add_paragraph().add_run().add_picture(io.BytesIO(_png()))
    path = tmp_path / "rich.docx"
    doc.save(path)
    return path


def test_registers_four_edges() -> None:
    g = ConversionGraph()
    register_docx_text(g)
    pairs = {(c.src, c.dst) for c in g.converters()}
    assert pairs == {
        ("docx", "markdown"),
        ("docx", "html"),
        ("markdown", "docx"),
        ("html", "docx"),
    }
    assert all(not c.requires for c in g.converters())


def test_global_graph_has_edges() -> None:
    load_builtin_converters()
    pairs = [(c.src, c.dst) for c in graph.converters()]
    assert ("docx", "markdown") in pairs and ("html", "docx") in pairs
    assert len(pairs) == len(set(pairs))


def test_docx_to_markdown_structure(tmp_path: Path) -> None:
    out = docx_to_markdown(_rich_docx(tmp_path), tmp_path / "out.md")
    md = out.read_text(encoding="utf-8")
    assert "# 第一章 概述" in md
    assert "## 小節" in md
    assert (
        "普通 **粗體** 與 *斜體* 與 ~~刪除~~ 與 `code_x` 並連到 [官網](https://example.com/a%20b)"
        in md
    )
    assert "- 項目甲\n    - 子項目\n- 項目乙" in md
    assert "1. 步驟一\n2. 步驟二" in md
    assert "| 名稱 | 數量 |\n| --- | --- |\n| 蘋果\\|梨 | 3 |" in md
    assert "> 引用文字" in md
    assert "![](out_files/image1.png)" in md
    assert (tmp_path / "out_files" / "image1.png").read_bytes() == _png()


def test_docx_to_html_structure(tmp_path: Path) -> None:
    out = docx_to_html(_rich_docx(tmp_path), tmp_path / "out.html")
    html = out.read_text(encoding="utf-8")
    assert html.startswith("<!DOCTYPE html>") and "<title>測試文件</title>" in html
    assert "<h1>第一章 概述</h1>" in html and "<h2>小節</h2>" in html
    assert "<strong>粗體</strong>" in html and "<em>斜體</em>" in html
    assert "<del>刪除</del>" in html and "<code>code_x</code>" in html
    assert '<a href="https://example.com/a b">官網</a>' in html
    assert "<ul><li>項目甲<ul><li>子項目</li></ul></li><li>項目乙</li></ul>" in html
    assert "<ol><li>步驟一</li><li>步驟二</li></ol>" in html
    assert "<thead><tr><th>名稱</th><th>數量</th></tr></thead>" in html
    assert "<td>蘋果|梨</td>" in html
    assert "<blockquote><p>引用文字</p></blockquote>" in html
    assert '<img src="data:image/png;base64,' in html


def test_markdown_to_docx_structure(tmp_path: Path) -> None:
    md = tmp_path / "in.md"
    md.write_text(
        "# 標題\n\n文字 **粗** *斜* ~~刪~~ `c` [連結](https://x.test/)\n\n"
        "- a\n    - b\n- c\n\n1. one\n2. two\n\n"
        "| h1 | h2 |\n| --- | --- |\n| 甲 | 乙 |\n\n> 引言\n\n"
        "```\nprint(1)\nprint(2)\n```\n\n---\n",
        encoding="utf-8",
    )
    out = markdown_to_docx(md, tmp_path / "out.docx")
    doc = Document(str(out))
    styles = [(p.style.name, p.text) for p in doc.paragraphs]
    assert ("Heading 1", "標題") in styles
    assert ("List Bullet", "a") in styles and ("List Bullet 2", "b") in styles
    assert ("List Number", "one") in styles
    assert ("Quote", "引言") in styles
    assert ("Code Block", "print(1)") in styles and ("Code Block", "print(2)") in styles
    body = next(p for p in doc.paragraphs if p.text.startswith("文字"))
    fmt = {r.text: r for r in body.runs}
    assert fmt["粗"].bold and fmt["斜"].italic and fmt["刪"].font.strike
    assert fmt["c"].font.name == "Consolas"
    assert any(
        r.reltype == RT.HYPERLINK and r.target_ref == "https://x.test/"
        for r in doc.part.rels.values()
    )
    assert [[c.text for c in r.cells] for r in doc.tables[0].rows] == [["h1", "h2"], ["甲", "乙"]]
    assert doc.tables[0].rows[0].cells[0].paragraphs[0].runs[0].bold
    assert doc.paragraphs[-1]._p.xpath("./w:pPr/w:pBdr/w:bottom")


def test_html_to_docx_basics_and_title(tmp_path: Path) -> None:
    html = tmp_path / "in.html"
    html.write_text(
        "<html><head><title>T 標題</title><style>p{}</style></head><body>"
        "<h2>二級</h2><p>甲<br>乙 <b>b</b><i>i</i></p><pre>x = 1\ny = 2</pre>"
        "<ol><li>一<ul><li>內</li></ul></li><li>二</li></ol></body></html>",
        encoding="utf-8",
    )
    doc = Document(str(html_to_docx(html, tmp_path / "o.docx")))
    assert doc.core_properties.title == "T 標題"
    styles = [(p.style.name, p.text) for p in doc.paragraphs]
    assert ("Heading 2", "二級") in styles
    assert ("Normal", "甲\n乙 bi") in styles
    assert ("Code Block", "x = 1") in styles and ("Code Block", "y = 2") in styles
    assert ("List Number", "一") in styles and ("List Bullet 2", "內") in styles
    assert ("List Number", "二") in styles
    assert not any("p{}" in t for _, t in styles)


@pytest.mark.parametrize("ext", ["md", "html"])
def test_roundtrip_docx_text_docx(tmp_path: Path, ext: str) -> None:
    src = _rich_docx(tmp_path)
    mid = tmp_path / f"mid.{ext}"
    (docx_to_markdown if ext == "md" else docx_to_html)(src, mid)
    back = (markdown_to_docx if ext == "md" else html_to_docx)(mid, tmp_path / "back.docx")
    a = [
        (b.kind, b.level, "".join(r.text for r in b.runs)) for b in docx_blocks(Document(str(src)))
    ]
    b = [
        (b.kind, b.level, "".join(r.text for r in b.runs)) for b in docx_blocks(Document(str(back)))
    ]
    assert a == b
    original = docx_blocks(Document(str(src)))
    restored = docx_blocks(Document(str(back)))
    assert [t for t in restored if t.kind == "table"][0].rows[1][0][0].text == "蘋果|梨"
    runs = [r for blk in restored if blk.kind == "para" for r in blk.runs]
    assert any(r.bold and r.text == "粗體" for r in runs)
    assert any(r.italic and r.text == "斜體" for r in runs)
    assert any(r.strike and r.text == "刪除" for r in runs)
    assert any(r.code and r.text == "code_x" for r in runs)
    assert any(
        r.href == "https://example.com/a b" or r.href == "https://example.com/a%20b" for r in runs
    )
    assert sum(1 for blk in original if blk.kind == "para" and blk.runs and blk.runs[0].image) == 1
    imgs = [r for blk in restored for r in blk.runs if r.image]
    assert len(imgs) == 1 and imgs[0].image is not None and imgs[0].image[0].startswith(b"\x89PNG")


def test_markdown_escaping_roundtrip(tmp_path: Path) -> None:
    doc = Document()
    doc.add_paragraph("snake_case * star [x] <b> `tick` \\ back")
    doc.add_paragraph("# not heading")
    doc.add_paragraph("1. not list")
    doc.add_paragraph("- not bullet")
    src = tmp_path / "e.docx"
    doc.save(src)
    md = docx_to_markdown(src, tmp_path / "e.md")
    back = markdown_to_docx(md, tmp_path / "e2.docx")
    texts = [p.text for p in Document(str(back)).paragraphs]
    assert texts == [
        "snake_case * star [x] <b> `tick` \\ back",
        "# not heading",
        "1. not list",
        "- not bullet",
    ]


def test_code_block_and_rule_docx_to_markdown(tmp_path: Path) -> None:
    md_in = tmp_path / "c.md"
    md_in.write_text("```\nline1\nline2\n```\n\n---\n\nend\n", encoding="utf-8")
    docx = markdown_to_docx(md_in, tmp_path / "c.docx")
    md = docx_to_markdown(docx, tmp_path / "c2.md").read_text(encoding="utf-8")
    assert md == "```\nline1\nline2\n```\n\n---\n\nend\n"


def test_numbering_part_ordered_detection(tmp_path: Path) -> None:
    doc = Document()
    par = doc.add_paragraph("n1", style="List Paragraph")
    num_pr = OxmlElement("w:numPr")
    ilvl = OxmlElement("w:ilvl")
    ilvl.set(qn("w:val"), "1")
    num_id = OxmlElement("w:numId")
    num_id.set(qn("w:val"), "999")
    num_pr.append(ilvl)
    num_pr.append(num_id)
    par._p.get_or_add_pPr().append(num_pr)
    blocks = docx_blocks(doc)
    assert blocks[0].kind == "item" and blocks[0].level == 1 and not blocks[0].ordered


def test_relative_and_data_images_and_path_restriction(tmp_path: Path) -> None:
    (tmp_path / "pic.png").write_bytes(_png())
    outside = tmp_path / "outside"
    outside.mkdir()
    (tmp_path / "secret.png").write_bytes(_png())
    sub = tmp_path / "sub"
    sub.mkdir()
    data = base64.b64encode(_png()).decode()
    md = sub / "i.md"
    md.write_text(
        f"![a](../pic.png) ![b](pic.png) ![c](data:image/png;base64,{data}) "
        "![d](https://example.com/x.png)\n",
        encoding="utf-8",
    )
    (sub / "pic.png").write_bytes(_png())
    doc = Document(str(markdown_to_docx(md, sub / "o.docx")))
    n_pics = len(doc.inline_shapes)
    assert n_pics == 2  # sub/pic.png + data URI; parent-dir and remote images are refused
    assert "a" in doc.paragraphs[0].text and "d" in doc.paragraphs[0].text


def test_corrupt_docx_raises(tmp_path: Path) -> None:
    bad = tmp_path / "bad.docx"
    bad.write_bytes(b"not a zip")
    with pytest.raises(CorruptFileError):
        docx_to_markdown(bad, tmp_path / "x.md")
    with pytest.raises(CorruptFileError):
        docx_to_html(bad, tmp_path / "x.html")


def test_text_decoding_bom_and_big5(tmp_path: Path) -> None:
    bom = tmp_path / "bom.md"
    bom.write_bytes("﻿# 中文".encode())
    assert Document(str(markdown_to_docx(bom, tmp_path / "a.docx"))).paragraphs[0].text == "中文"
    big5 = tmp_path / "big5.html"
    big5.write_bytes("<p>繁體中文</p>".encode("big5"))
    assert Document(str(html_to_docx(big5, tmp_path / "b.docx"))).paragraphs[0].text == "繁體中文"


def test_convert_api_and_cli(tmp_path: Path) -> None:
    src = _rich_docx(tmp_path)
    out = anydoc.convert(src, tmp_path / "api.md")
    assert out.read_text(encoding="utf-8").startswith("# 第一章 概述")
    back = anydoc.convert(out, tmp_path / "api.docx")
    assert Document(str(back)).paragraphs[0].text == "第一章 概述"
    res = runner.invoke(app, ["convert", str(src), str(tmp_path / "cli.html")])
    assert res.exit_code == 0, res.output
    assert "<h1>第一章 概述</h1>" in (tmp_path / "cli.html").read_text(encoding="utf-8")


def test_empty_document(tmp_path: Path) -> None:
    src = tmp_path / "empty.docx"
    Document().save(src)
    assert docx_to_markdown(src, tmp_path / "e.md").read_text(encoding="utf-8") == ""
    assert "<body>\n\n</body>" in docx_to_html(src, tmp_path / "e.html").read_text(encoding="utf-8")


def test_merged_cells_and_empty_table(tmp_path: Path) -> None:
    doc = Document()
    t = doc.add_table(rows=2, cols=2)
    t.cell(0, 0).merge(t.cell(0, 1)).text = "合併"
    t.cell(1, 0).text = "a"
    t.cell(1, 1).text = "b"
    src = tmp_path / "m.docx"
    doc.save(src)
    md = docx_to_markdown(src, tmp_path / "m.md").read_text(encoding="utf-8")
    assert "| 合併 |  |" in md and "| a | b |" in md
