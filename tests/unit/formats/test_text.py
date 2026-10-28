"""Tests for the TXT / Markdown / JSON / YAML / XML handlers (T011)."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

import pytest

import anydoc
from anydoc.core.errors import CorruptFileError
from anydoc.core.model import Capability, Document
from anydoc.core.registry import registry
from anydoc.formats.text import (
    JsonHandler,
    MarkdownHandler,
    PlainTextDocument,
    StructuredDocument,
    TxtHandler,
    XmlDocument,
    XmlHandler,
    YamlHandler,
)
from tests.fixtures.generate import BODY, TITLE

Sample = Callable[[str], Path]
HANDLERS = (TxtHandler, MarkdownHandler, JsonHandler, YamlHandler, XmlHandler)


@pytest.fixture(autouse=True)
def _handlers() -> None:
    for h in HANDLERS:
        registry.register(h, replace=True)


def test_entry_points_register_text_family() -> None:
    from anydoc.core.registry import HandlerRegistry

    reg = HandlerRegistry()
    reg.load_entry_points()
    for fmt in ("txt", "markdown", "json", "yaml", "xml"):
        assert reg.get(fmt) is not None


def test_capabilities() -> None:
    for h in HANDLERS:
        assert Capability.READ in h.capabilities
        assert Capability.WRITE in h.capabilities
        assert Capability.EXTRACT_TEXT in h.capabilities


def test_sniff_by_extension() -> None:
    assert MarkdownHandler.sniff(b"", Path("a.MD")) == 0.9
    assert YamlHandler.sniff(b"", Path("a.yml")) == 0.9
    assert JsonHandler.sniff(b"", Path("a.txt")) == 0.0
    assert XmlHandler.sniff(b"", None) == 0.0


# --- TXT -------------------------------------------------------------------


def test_txt_sample_open(sample: Sample) -> None:
    doc = anydoc.open(sample("txt"))
    assert isinstance(doc, Document)
    assert doc.format == "txt"
    assert BODY in doc.text()
    assert doc.parts()[0].kind == "text"


@pytest.mark.parametrize(
    ("raw", "encoding", "bom"),
    [
        ("繁體中文\n第二行\n".encode(), "utf-8", False),
        (b"\xef\xbb\xbf" + "繁體中文\n".encode(), "utf-8", True),
        ("繁體中文\n".encode("big5"), "big5", False),
    ],
)
def test_txt_encoding_roundtrip(tmp_path: Path, raw: bytes, encoding: str, bom: bool) -> None:
    src = tmp_path / "a.txt"
    src.write_bytes(raw)
    doc = TxtHandler().open(src)
    assert isinstance(doc, PlainTextDocument)
    assert doc.encoding == encoding
    assert doc.has_bom is bom
    assert "繁體中文" in doc.text()
    out = doc.save(tmp_path / "b.txt")
    assert out.read_bytes() == raw


def test_txt_crlf_preserved(tmp_path: Path) -> None:
    src = tmp_path / "a.txt"
    src.write_bytes(b"one\r\ntwo\r\n")
    doc = TxtHandler().open(src)
    assert doc.text() == "one\ntwo\n"
    assert doc.metadata.extra["newline"] == "crlf"
    assert doc.save(tmp_path / "b.txt").read_bytes() == b"one\r\ntwo\r\n"


def test_txt_edit_and_create(tmp_path: Path) -> None:
    doc = TxtHandler().create()
    assert isinstance(doc, PlainTextDocument)
    doc.set_text("a\r\nb")
    assert doc.text() == "a\nb"
    assert doc.metadata.extra["line_count"] == 2
    with pytest.raises(ValueError, match="No path"):
        doc.save()
    target = tmp_path / "n.txt"
    assert doc.save(target) == target
    assert target.read_bytes() == b"a\nb"


def test_txt_open_bytes_and_missing_file(tmp_path: Path) -> None:
    assert TxtHandler().open(b"hello").text() == "hello"
    with pytest.raises(CorruptFileError):
        TxtHandler().open(tmp_path / "missing.txt")


# --- Markdown --------------------------------------------------------------

MD = """# 標題一

內文

```
# not a heading
```

## 子標題 ##

### 第三層
"""


def test_markdown_headings_and_title() -> None:
    doc = MarkdownHandler().open(MD.encode())
    assert isinstance(doc, PlainTextDocument)
    assert doc.format == "markdown"
    assert doc.headings() == [(1, "標題一"), (2, "子標題"), (3, "第三層")]
    assert doc.metadata.title == "標題一"
    assert doc.metadata.mime == "text/markdown"


def test_markdown_roundtrip(tmp_path: Path) -> None:
    p = tmp_path / "a.md"
    p.write_text(MD, encoding="utf-8")
    doc = anydoc.open(p)
    assert doc.format == "markdown"
    assert doc.save(tmp_path / "b.md").read_text(encoding="utf-8") == MD


def test_txt_has_no_headings() -> None:
    assert TxtHandler().open(b"# hi").headings() == []  # type: ignore[attr-defined]


# --- JSON ------------------------------------------------------------------


def test_json_sample(sample: Sample) -> None:
    doc = anydoc.open(sample("json"))
    assert doc.format == "json"
    assert isinstance(doc, StructuredDocument)
    assert isinstance(doc.to_dict(), dict)
    assert BODY in doc.text() or TITLE in doc.text()
    assert json.loads(doc.text()) == doc.data


def test_json_roundtrip_preserves_chinese(tmp_path: Path) -> None:
    value = {"名稱": "測試", "list": [1, 2.5, None, True], "nested": {"k": "值"}}
    src = tmp_path / "a.json"
    src.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
    doc = JsonHandler().open(src)
    assert isinstance(doc, StructuredDocument)
    assert doc.data == value
    assert doc.metadata.extra["top_level_keys"] == ["名稱", "list", "nested"]
    assert doc.metadata.extra["root_type"] == "dict"
    out = doc.save(tmp_path / "b.json")
    assert json.loads(out.read_text(encoding="utf-8")) == value
    assert "測試" in out.read_text(encoding="utf-8")


def test_json_list_root_and_edit(tmp_path: Path) -> None:
    doc = JsonHandler().open(b"[1, 2, 3]")
    assert isinstance(doc, StructuredDocument)
    assert doc.metadata.extra["length"] == 3
    doc.data.append(4)
    out = doc.save(tmp_path / "x.json")
    assert json.loads(out.read_bytes()) == [1, 2, 3, 4]


@pytest.mark.parametrize("bad", [b"{not json", b"", b"[1,"])
def test_json_invalid(bad: bytes) -> None:
    with pytest.raises(CorruptFileError):
        JsonHandler().open(bad)


def test_json_create() -> None:
    doc = JsonHandler().create()
    assert isinstance(doc, StructuredDocument)
    assert doc.data == {}


# --- YAML ------------------------------------------------------------------

YAML = "名稱: 測試\n清單:\n  - 1\n  - 2\nnested:\n  key: 值\n"


def test_yaml_parse_and_roundtrip(tmp_path: Path) -> None:
    src = tmp_path / "a.yaml"
    src.write_text(YAML, encoding="utf-8")
    doc = anydoc.open(src)
    assert doc.format == "yaml"
    assert isinstance(doc, StructuredDocument)
    assert doc.data == {"名稱": "測試", "清單": [1, 2], "nested": {"key": "值"}}
    out = doc.save(tmp_path / "b.yml")
    assert "名稱: 測試" in out.read_text(encoding="utf-8")
    assert YamlHandler().open(out).data == doc.data


def test_yaml_safe_loading_rejects_python_tags() -> None:
    with pytest.raises(CorruptFileError):
        YamlHandler().open(b"x: !!python/object/apply:os.system ['echo hi']")


def test_yaml_invalid() -> None:
    with pytest.raises(CorruptFileError):
        YamlHandler().open(b"a: [1, 2\nb: }")


# --- XML -------------------------------------------------------------------

XML = (
    '<?xml version="1.0" encoding="utf-8"?>\n'
    '<書 id="1"><章 no="1">第一章</章><章 no="2">第二章<註>附註</註></章></書>\n'
)


def test_xml_tree_and_text() -> None:
    doc = XmlHandler().open(XML.encode())
    assert isinstance(doc, XmlDocument)
    tree = doc.tree()
    assert tree["tag"] == "書"
    assert tree["attrs"] == {"id": "1"}
    assert [c["text"] for c in tree["children"]] == ["第一章", "第二章"]
    assert tree["children"][1]["children"][0]["tag"] == "註"
    assert doc.text().splitlines() == ["第一章", "第二章", "附註"]
    assert doc.metadata.extra["element_count"] == 4
    assert doc.metadata.extra["root_tag"] == "書"
    assert doc.parts()[0].name == "書"


def test_xml_roundtrip(tmp_path: Path) -> None:
    src = tmp_path / "a.xml"
    src.write_text(XML, encoding="utf-8")
    doc = anydoc.open(src)
    assert doc.format == "xml"
    out = doc.save(tmp_path / "b.xml")
    again = XmlHandler().open(out)
    assert isinstance(again, XmlDocument)
    assert again.tree() == XmlHandler().open(XML.encode()).tree()  # type: ignore[attr-defined]


def test_xml_big5_declared_encoding(tmp_path: Path) -> None:
    raw = '<?xml version="1.0" encoding="big5"?><a>繁體</a>'.encode("big5")
    doc = XmlHandler().open(raw)
    assert isinstance(doc, XmlDocument)
    assert doc.encoding == "big5"
    assert doc.text() == "繁體"
    assert XmlHandler().open(doc.save(tmp_path / "x.xml")).text() == "繁體"  # type: ignore[attr-defined]


def test_xml_invalid_and_hostile() -> None:
    with pytest.raises(CorruptFileError):
        XmlHandler().open(b"<a><b></a>")
    bomb = (
        b'<?xml version="1.0"?><!DOCTYPE l [<!ENTITY a "aaaa"><!ENTITY b "&a;&a;&a;&a;">]>'
        b"<l>&b;</l>"
    )
    with pytest.raises(CorruptFileError):
        XmlHandler().open(bomb)
    xxe = b'<?xml version="1.0"?><!DOCTYPE d [<!ENTITY x SYSTEM "file:///etc/passwd">]><d>&x;</d>'
    with pytest.raises(CorruptFileError):
        XmlHandler().open(xxe)


def test_xml_create() -> None:
    doc = XmlHandler().create()
    assert isinstance(doc, XmlDocument)
    assert doc.tree()["tag"] == "root"
    assert b"<root />" in doc.to_bytes() or b"<root/>" in doc.to_bytes()


# --- cross-format via anydoc.open ------------------------------------------


@pytest.mark.parametrize("key", ["txt", "json"])
def test_open_via_public_api_info(sample: Sample, key: str) -> None:
    doc = anydoc.open(sample(key))
    assert doc.metadata.to_dict()["format"] == key
    json.dumps(doc.metadata.to_dict(), ensure_ascii=False)


def test_xml_unknown_declared_encoding() -> None:
    with pytest.raises(CorruptFileError):
        XmlHandler().open(b'<?xml version="1.0" encoding="no-such-codec"?><a>x</a>')
