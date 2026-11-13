r"""Unit tests for the DXF loader that decodes ``\U+XXXX`` escapes (T086)."""

from __future__ import annotations

from pathlib import Path

import ezdxf
import pytest
from ezdxf.lldxf.types import DXFTag

from anydoc.core.errors import CorruptFileError
from anydoc.formats.dxf import DxfHandler
from anydoc.formats.dxf import loader as L

WALL = "牆面"
WALL_ESCAPED = r"\U+7246\U+9762"


def _legacy(path: Path, version: str = "R12", *, binary: bool = False) -> Path:
    """Write a drawing whose Chinese names end up as escapes (code page ANSI_1252)."""
    doc = ezdxf.new(version)
    doc.layers.add(WALL, color=1)
    doc.modelspace().add_line((0, 0), (3, 4), dxfattribs={"layer": WALL})
    block = doc.blocks.new("門塊")
    block.add_line((0, 0), (1, 0), dxfattribs={"layer": WALL})
    doc.modelspace().add_blockref("門塊", (5, 5))
    doc.saveas(path, fmt="bin" if binary else "asc")
    return path


# -- decode_escapes ---------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("", ""),
        ("WALL", "WALL"),
        (WALL_ESCAPED, WALL),
        (r"A-\U+7246-B", "A-牆-B"),
        (r"\U+7246\U+9762_2F", "牆面_2F"),
        (r"\u+7246", r"\u+7246"),  # the marker is upper case only
        (r"\U+724", r"\U+724"),  # too short
        (r"\U+7246\U+", "牆\\U+"),
        (r"C:\Users\U+ser", r"C:\Users\U+ser"),
        (r"\U+00e9t\U+00E9", "été"),
    ],
)
def test_decode_escapes(text: str, expected: str) -> None:
    assert L.decode_escapes(text) == expected


def test_decode_escapes_beyond_bmp() -> None:
    """ezdxf writes characters above U+FFFF with eight hex digits."""
    assert L.decode_escapes(r"\U+0001f600!") == "😀!"
    assert L.decode_escapes(r"\U+0010ffff") == "\U0010ffff"
    # Four digits followed by ordinary hex-looking text is not an eight digit escape.
    assert L.decode_escapes(r"\U+7246abcd") == "牆abcd"
    assert L.decode_escapes(r"\U+00419762") == "A9762"


@pytest.mark.parametrize("escape", [r"\U+000A", r"\U+000d", r"\U+0000", r"\U+007F", r"\U+0085"])
def test_decode_escapes_keeps_control_characters(escape: str) -> None:
    """A decoded line break would corrupt the line-based DXF structure."""
    assert L.decode_escapes(f"a{escape}b") == f"a{escape}b"


@pytest.mark.parametrize("escape", [r"\U+D800", r"\U+dfff", r"\U+DC80"])
def test_decode_escapes_keeps_surrogates(escape: str) -> None:
    assert L.decode_escapes(escape) == escape


def test_decoded_tags_only_touches_strings() -> None:
    tags = [DXFTag(8, WALL_ESCAPED), DXFTag(70, 7246), DXFTag(10, 1.5), DXFTag(2, "PLAIN")]
    out = list(L._decoded_tags(tags))
    assert out[0] == DXFTag(8, WALL)
    assert out[1:] == tags[1:]
    assert out[3] is tags[3]


# -- read_dxf ---------------------------------------------------------------


@pytest.mark.parametrize("version", ["R12", "R2000", "R2004"])
def test_read_dxf_decodes_names(tmp_path: Path, version: str) -> None:
    path = _legacy(tmp_path / "a.dxf", version)
    assert WALL_ESCAPED.encode() in path.read_bytes()  # really stored as escapes
    assert ezdxf.readfile(path).layers.has_entry(WALL) is False  # plain ezdxf keeps them

    doc = L.read_dxf(path)
    assert doc.filename == str(path)
    assert doc.encoding == "cp1252"
    assert doc.layers.has_entry(WALL)
    assert doc.layers.get(WALL).dxf.name == WALL
    assert "門塊" in doc.blocks
    line, ref = list(doc.modelspace())
    assert line.dxf.layer == WALL
    assert ref.dxf.name == "門塊"
    assert [e.dxf.layer for e in doc.blocks.get("門塊")] == [WALL]


@pytest.mark.parametrize("version", ["R12", "R2000"])
def test_read_dxf_binary(tmp_path: Path, version: str) -> None:
    path = _legacy(tmp_path / "bin.dxf", version, binary=True)
    assert path.read_bytes().startswith(b"AutoCAD Binary DXF")
    doc = L.read_dxf(path)
    assert doc.layers.has_entry(WALL)
    assert doc.filename == str(path)
    assert [e.dxf.layer for e in doc.modelspace()][0] == WALL
    assert "門塊" in doc.blocks


def test_read_dxf_unicode_file_unchanged(tmp_path: Path) -> None:
    """R2007+ drawings are UTF-8 and need no decoding."""
    path = _legacy(tmp_path / "u.dxf", "R2010")
    assert WALL.encode("utf-8") in path.read_bytes()
    doc = L.read_dxf(path)
    assert doc.output_encoding == "utf-8"
    assert doc.layers.has_entry(WALL)


def test_read_dxf_native_code_page_names(tmp_path: Path) -> None:
    """Names stored as Big5 bytes (ANSI_950) are read through the code page as before."""
    doc = ezdxf.new("R2000")
    doc.encoding = "cp950"
    doc.layers.add(WALL)
    doc.layers.add("㐀層")  # not in Big5 → escaped even in this code page
    path = tmp_path / "big5.dxf"
    doc.saveas(path)
    raw = path.read_bytes()
    assert WALL.encode("cp950") in raw and rb"\U+3400" in raw

    loaded = L.read_dxf(path)
    assert loaded.encoding == "cp950"
    assert loaded.layers.has_entry(WALL) and loaded.layers.has_entry("㐀層")


def test_read_dxf_rejects_non_dxf(tmp_path: Path) -> None:
    path = tmp_path / "x.dxf"
    path.write_text("hello\nworld\n", encoding="utf-8")
    with pytest.raises(OSError, match="not a DXF file"):
        L.read_dxf(path)
    with pytest.raises(OSError):
        L.read_dxf(tmp_path / "missing.dxf")


def test_handler_wraps_load_errors(tmp_path: Path) -> None:
    path = tmp_path / "x.dxf"
    path.write_text("not a drawing", encoding="utf-8")
    with pytest.raises(CorruptFileError, match="Invalid DXF"):
        DxfHandler().open(path)
    with pytest.raises(CorruptFileError):
        DxfHandler().open(b"AutoCAD Binary DXF\r\n\x1a\x00\xff\xff")


def test_handler_open_bytes_decodes(tmp_path: Path) -> None:
    data = _legacy(tmp_path / "a.dxf").read_bytes()
    doc = DxfHandler().open(data)
    assert [layer.name for layer in doc.layers()] == ["0", "Defpoints", WALL]
    assert doc.path is None


def test_escaped_text_content_is_decoded_on_load(tmp_path: Path) -> None:
    """Text, attribute tags and block descriptions come back as Unicode too."""
    src = ezdxf.new("R12")
    src.modelspace().add_text("標題 A", dxfattribs={"height": 2})
    path = tmp_path / "t.dxf"
    src.saveas(path)
    doc = DxfHandler().open(path)
    assert doc.text() == "標題 A"
    assert next(iter(doc.native.modelspace())).dxf.text == "標題 A"
