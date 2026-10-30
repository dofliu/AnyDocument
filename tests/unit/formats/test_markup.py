"""Tests for the RTF / HTML / EPUB handlers (T023)."""

from __future__ import annotations

import io
import zipfile
from collections.abc import Callable
from pathlib import Path

import pytest

import anydoc
from anydoc.core.errors import CorruptFileError
from anydoc.core.model import Capability, Document
from anydoc.core.registry import HandlerRegistry, registry
from anydoc.formats.markup import (
    EpubDocument,
    EpubHandler,
    HtmlDocument,
    HtmlHandler,
    RtfDocument,
    RtfHandler,
)
from anydoc.formats.markup.html import decode_html
from tests.fixtures.generate import BODY, TITLE

Sample = Callable[[str], Path]


@pytest.fixture(autouse=True)
def _handlers() -> None:
    for h in (RtfHandler, HtmlHandler, EpubHandler):
        registry.register(h, replace=True)


def test_entry_points_register_markup_family() -> None:
    reg = HandlerRegistry()
    assert {"rtf", "html", "epub"} <= set(reg.load_entry_points())
    assert reg.get("epub") is EpubHandler


@pytest.mark.parametrize("handler", [RtfHandler, HtmlHandler, EpubHandler])
def test_capabilities(handler: type) -> None:
    assert handler.capabilities == {Capability.READ, Capability.EXTRACT_TEXT}


# ---------------------------------------------------------------- RTF


def test_rtf_open_via_api(sample: Sample) -> None:
    doc = anydoc.open(sample("rtf"))
    assert isinstance(doc, Document)
    assert isinstance(doc, RtfDocument)
    assert doc.format == "rtf"
    assert doc.metadata.title == TITLE
    assert doc.metadata.author == "Doflab"
    assert doc.text() == f"{TITLE}\n{BODY}"
    assert doc.paragraphs() == [TITLE, BODY]
    assert [p.kind for p in doc.parts()] == ["document"]


def test_rtf_skips_font_table_and_info_text() -> None:
    doc = RtfHandler().open(
        b"{\\rtf1\\ansi{\\fonttbl{\\f0 Arial;}}{\\*\\generator Foo;}{\\info{\\title T}}Hello\\par}"
    )
    assert doc.text() == "Hello"


def test_rtf_hex_escapes_use_codepage() -> None:
    # 0xA4 0xA4 is "中" in Big5 (cp950)
    doc = RtfHandler().open(b"{\\rtf1\\ansi\\ansicpg950 \\'a4\\'a4}")
    assert doc.text() == "中"


def test_rtf_unicode_skips_fallback_and_handles_negative() -> None:
    doc = RtfHandler().open(b"{\\rtf1\\ansi\\uc1 a\\u20013? b\\u-10179?\\u-8233?}")
    assert doc.text().startswith("a中 b")
    assert "?" not in doc.text()


def test_rtf_special_characters() -> None:
    doc = RtfHandler().open(b"{\\rtf1\\ansi a\\tab b\\emdash c \\{x\\} \\\\ \\~z}")
    assert doc.text() == "a\tb—c {x} \\  z"


def test_rtf_rejects_non_rtf() -> None:
    with pytest.raises(CorruptFileError):
        RtfHandler().open(b"plain text")


def test_rtf_deep_nesting_rejected() -> None:
    with pytest.raises(CorruptFileError):
        RtfHandler().open(b"{\\rtf1" + b"{" * 2000 + b"x" + b"}" * 2000 + b"}")


def test_rtf_sniff_and_save_roundtrip(tmp_path: Path, sample: Sample) -> None:
    assert RtfHandler.sniff(b"{\\rtf1 x}", None) > 0.9
    assert RtfHandler.sniff(b"hello", None) == 0.0
    out = tmp_path / "out.rtf"
    src = sample("rtf")
    doc = RtfHandler().open(src)
    assert doc.save(out) == out
    assert out.read_bytes() == src.read_bytes()
    assert RtfHandler().open(out).text() == doc.text()


def test_rtf_save_without_path_raises() -> None:
    doc = RtfHandler().open(b"{\\rtf1 x}")
    with pytest.raises(ValueError):
        doc.save()


def test_rtf_unreadable_path(tmp_path: Path) -> None:
    with pytest.raises(CorruptFileError):
        RtfHandler().open(tmp_path / "missing.rtf")


# ---------------------------------------------------------------- HTML


def test_html_open_via_api(sample: Sample) -> None:
    doc = anydoc.open(sample("html"))
    assert isinstance(doc, HtmlDocument)
    assert doc.metadata.title == TITLE
    assert doc.metadata.author == "Doflab"
    assert doc.metadata.extra["lang"] == "zh-Hant"
    assert doc.metadata.extra["encoding"] == "utf-8"
    assert [(h.level, h.text) for h in doc.headings] == [(1, TITLE)]
    assert [(link.href, link.text) for link in doc.links] == [("https://example.com", "連結")]
    assert [(i.src, i.alt) for i in doc.images] == [("a.png", "圖")]
    assert doc.tables == [[["名稱", "數量"], ["蘋果", "3"]]]
    assert doc.parts()[0].name == TITLE


def test_html_text_reading_order_excludes_script_style_title(sample: Sample) -> None:
    text = anydoc.open(sample("html")).text()
    assert text.startswith(TITLE + "\n" + BODY)
    assert "var x" not in text
    assert "color:red" not in text
    assert "名稱\t數量" in text
    assert "蘋果\t3" in text


def test_html_block_and_list_structure() -> None:
    doc = HtmlHandler().open(b"<ul><li>a</li><li>b <b>c</b></li></ul><p>x<br>y</p>")
    assert doc.text() == "- a\n- b c\nx\ny"


def test_html_pre_preserves_whitespace() -> None:
    doc = HtmlHandler().open(b"<pre>a  b\n  c</pre>")
    assert doc.text() == "a  b\n  c"


def test_html_entities_decoded() -> None:
    assert HtmlHandler().open(b"<p>a &amp; b &lt;c&gt; &#20013;</p>").text() == "a & b <c> 中"


def test_html_meta_charset_big5() -> None:
    raw = f'<html><head><meta charset="big5"></head><body><p>{BODY}</p></body></html>'.encode(
        "big5"
    )
    doc = HtmlHandler().open(raw)
    assert doc.text() == BODY
    assert doc.metadata.extra["encoding"] == "big5"


def test_html_decode_bom_and_fallback() -> None:
    assert decode_html("﻿<p>中</p>".encode())[0].endswith("<p>中</p>")
    text, enc = decode_html("<p>中文</p>".encode("big5"))
    assert text == "<p>中文</p>"
    assert enc in ("big5", "cp950")
    # unknown declared charset falls back to probing
    assert decode_html(b'<meta charset="bogus"><p>x</p>')[0].endswith("<p>x</p>")


def test_html_nested_tables_and_unclosed_tags() -> None:
    doc = HtmlHandler().open(
        b"<table><tr><td>a<table><tr><td>in</td></tr></table></td><td>b</td></tr></table><p>tail"
    )
    assert [["in"]] in doc.tables
    assert doc.text().endswith("tail")


def test_html_rejects_text_without_markup() -> None:
    with pytest.raises(CorruptFileError):
        HtmlHandler().open(b"just words")


def test_html_sniff_and_save(tmp_path: Path, sample: Sample) -> None:
    assert HtmlHandler.sniff(b"<!DOCTYPE html><p>", None) > 0.8
    assert HtmlHandler.sniff(b"<p>x</p>", tmp_path / "a.htm") > 0
    assert HtmlHandler.sniff(b"hello", None) == 0.0
    out = tmp_path / "o.html"
    doc = HtmlHandler().open(sample("html"))
    doc.save(out)
    assert out.read_bytes() == sample("html").read_bytes()
    with pytest.raises(ValueError):
        HtmlHandler().open(b"<p>x</p>").save()


# ---------------------------------------------------------------- EPUB


def test_epub_open_via_api(sample: Sample) -> None:
    doc = anydoc.open(sample("epub"))
    assert isinstance(doc, EpubDocument)
    assert doc.format == "epub"
    assert doc.metadata.title == TITLE
    assert doc.metadata.author == "Doflab"
    assert doc.metadata.page_count == 2
    assert doc.metadata.extra["language"] == "zh-TW"
    assert doc.metadata.extra["version"] == "3.0"
    assert [(p.kind, p.name) for p in doc.parts()] == [("chapter", "第一章"), ("chapter", "第二章")]
    assert doc.text() == f"第一章\n{BODY}\n\n第二章\n結尾"


def test_epub_save_roundtrip(tmp_path: Path, sample: Sample) -> None:
    out = tmp_path / "o.epub"
    doc = EpubHandler().open(sample("epub"))
    doc.save(out)
    assert EpubHandler().open(out).text() == doc.text()


def test_epub_sniff(sample: Sample) -> None:
    assert EpubHandler.sniff(sample("epub").read_bytes()[:200], None) > 0.9
    assert EpubHandler.sniff(b"PK", Path("a.epub")) > 0
    assert EpubHandler.sniff(b"PK", Path("a.zip")) == 0.0


def _zip(files: dict[str, str]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, data in files.items():
            zf.writestr(name, data)
    return buf.getvalue()


def test_epub_not_a_zip() -> None:
    with pytest.raises(CorruptFileError):
        EpubHandler().open(b"not a zip")


def test_epub_missing_container() -> None:
    with pytest.raises(CorruptFileError):
        EpubHandler().open(_zip({"mimetype": "application/epub+zip"}))


def test_epub_container_without_rootfile() -> None:
    container = '<container xmlns="urn:oasis:names:tc:opendocument:xmlns:container"/>'
    with pytest.raises(CorruptFileError):
        EpubHandler().open(_zip({"META-INF/container.xml": container}))


def test_epub_malformed_xml() -> None:
    with pytest.raises(CorruptFileError):
        EpubHandler().open(_zip({"META-INF/container.xml": "<container"}))


def test_epub_empty_spine() -> None:
    container = (
        '<container xmlns="urn:oasis:names:tc:opendocument:xmlns:container"><rootfiles>'
        '<rootfile full-path="c.opf"/></rootfiles></container>'
    )
    opf = '<package xmlns="http://www.idpf.org/2007/opf"><spine/></package>'
    with pytest.raises(CorruptFileError):
        EpubHandler().open(_zip({"META-INF/container.xml": container, "c.opf": opf}))


def test_epub_entity_bomb_rejected() -> None:
    bomb = '<!DOCTYPE x [<!ENTITY a "aaaa"><!ENTITY b "&a;&a;">]><container>&b;</container>'
    with pytest.raises(CorruptFileError):
        EpubHandler().open(_zip({"META-INF/container.xml": bomb}))
