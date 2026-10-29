"""Tests for the DXF reader (T019)."""

from __future__ import annotations

import io
from collections.abc import Callable
from pathlib import Path

import ezdxf
import pytest

import anydoc
from anydoc.core.errors import CorruptFileError
from anydoc.core.model import Capability, Document
from anydoc.core.registry import registry
from anydoc.formats.dxf import DxfDocument, DxfHandler
from anydoc.formats.dxf import handler as handler_module
from tests.fixtures.generate import TITLE

Sample = Callable[[str], Path]


@pytest.fixture(autouse=True)
def _handler() -> None:
    registry.register(DxfHandler, replace=True)


def _rich_dxf(tmp_path: Path) -> Path:
    doc = ezdxf.new("R2018", setup=True)
    doc.header["$INSUNITS"] = 4
    doc.layers.add("牆", color=1, linetype="DASHED")
    hidden = doc.layers.add("隱藏", color=5)
    hidden.off()
    frozen = doc.layers.add("凍結")
    frozen.freeze()
    frozen.lock()
    blk = doc.blocks.new("DOOR", base_point=(1, 2, 0))
    blk.add_line((0, 0), (1, 0))
    blk.add_circle((0, 0), 1)
    blk.add_attdef("TAG", (0, 0), text="預設")
    msp = doc.modelspace()
    msp.add_line((0, 0), (10, 0), dxfattribs={"layer": "牆"})
    msp.add_line((0, 0), (0, 10), dxfattribs={"layer": "牆"})
    msp.add_arc((0, 0), 5, 0, 90)
    msp.add_text("單行文字", dxfattribs={"layer": "牆"})
    mtext = msp.add_mtext("多行\\P文字")
    mtext.dxf.layer = "隱藏"
    ins = msp.add_blockref("DOOR", (5, 5))
    ins.add_attrib("TAG", "門甲", (5, 5))
    paper = doc.layout("Layout1")
    paper.add_text("圖紙文字")
    path = tmp_path / "rich.dxf"
    doc.saveas(path)
    return path


def test_entry_point_and_capabilities() -> None:
    from importlib.metadata import entry_points

    eps = {e.name: e for e in entry_points(group="anydoc.formats")}
    assert eps["dxf"].load() is DxfHandler
    assert Capability.READ in DxfHandler.capabilities
    assert Capability.EXTRACT_TEXT in DxfHandler.capabilities


def test_sniff() -> None:
    assert DxfHandler.sniff(b"", Path("a.DXF")) > 0.5
    assert DxfHandler.sniff(b"", Path("a.dwg")) == 0.0
    assert DxfHandler.sniff(b"", None) == 0.0


def test_open_sample_via_public_api(sample: Sample) -> None:
    doc = anydoc.open(sample("dxf"))
    assert isinstance(doc, DxfDocument)
    assert isinstance(doc, Document)
    assert doc.format == "dxf"
    assert doc.metadata.mime == "image/vnd.dxf"
    assert TITLE in doc.text()
    assert doc.entity_counts("Model") == {"CIRCLE": 1, "LINE": 1, "TEXT": 1}


def test_version_and_units(tmp_path: Path) -> None:
    doc = DxfHandler().open(_rich_dxf(tmp_path))
    assert doc.version == "AC1032"
    assert doc.release == "R2018"
    assert doc.units == "mm"
    assert doc.metadata.extra["release"] == "R2018"


def test_unitless_and_unknown_version(sample: Sample, monkeypatch: pytest.MonkeyPatch) -> None:
    doc = DxfHandler().open(sample("dxf"))
    doc.native.header["$INSUNITS"] = 0
    assert doc.units == "unitless"
    monkeypatch.delitem(handler_module._RELEASES, "AC1024")
    assert doc.release == doc.version


def test_layers(tmp_path: Path) -> None:
    doc = DxfHandler().open(_rich_dxf(tmp_path))
    by_name = {layer.name: layer for layer in doc.layers()}
    assert {"0", "牆", "隱藏", "凍結"} <= set(by_name)
    assert by_name["牆"].color == 1
    assert by_name["牆"].linetype == "DASHED"
    assert by_name["牆"].on and not by_name["牆"].frozen and not by_name["牆"].locked
    assert not by_name["隱藏"].on
    assert by_name["凍結"].frozen and by_name["凍結"].locked
    assert doc.metadata.extra["layer_count"] == len(by_name)


def test_entity_statistics_and_listing(tmp_path: Path) -> None:
    doc = DxfHandler().open(_rich_dxf(tmp_path))
    assert doc.entity_counts("Model") == {
        "ARC": 1,
        "INSERT": 1,
        "LINE": 2,
        "MTEXT": 1,
        "TEXT": 1,
    }
    assert doc.entity_counts()["TEXT"] == 2  # model + paper space
    walls = doc.entities(layer="牆")
    assert [e.type for e in walls] == ["LINE", "LINE", "TEXT"]
    assert walls[2].text == "單行文字"
    assert all(e.handle for e in walls)
    assert doc.entities("Layout1")[0].text == "圖紙文字"


def test_blocks(tmp_path: Path) -> None:
    doc = DxfHandler().open(_rich_dxf(tmp_path))
    blocks = {b.name: b for b in doc.blocks()}
    assert "DOOR" in blocks
    assert not any(n.startswith("*Model") or n.startswith("*Paper") for n in blocks)
    assert blocks["DOOR"].entity_count == 3
    assert blocks["DOOR"].base_point == (1.0, 2.0, 0.0)
    assert doc.metadata.extra["block_count"] == len(blocks)


def test_layouts_and_parts(tmp_path: Path) -> None:
    doc = DxfHandler().open(_rich_dxf(tmp_path))
    assert doc.layout_names()[0] == "Model"
    parts = doc.parts()
    assert [p.name for p in parts] == doc.layout_names()
    assert {p.kind for p in parts} == {"layout"}
    assert doc.metadata.page_count == len(parts)
    with pytest.raises(KeyError):
        doc.entities("nope")


def test_text_extraction(tmp_path: Path) -> None:
    doc = DxfHandler().open(_rich_dxf(tmp_path))
    items = doc.texts()
    kinds = {t.kind for t in items}
    assert kinds == {"TEXT", "MTEXT", "ATTRIB"}
    by_kind = {t.kind: t for t in items}
    assert by_kind["MTEXT"].text == "多行\n文字"
    assert by_kind["MTEXT"].layer == "隱藏"
    assert by_kind["ATTRIB"].text == "門甲"
    assert [t.text for t in doc.texts("Layout1")] == ["圖紙文字"]
    assert "單行文字" in doc.text() and "門甲" in doc.text()


def test_empty_text_items_dropped() -> None:
    d = ezdxf.new("R2010")
    d.modelspace().add_text("")
    assert DxfDocument(d).texts() == []


def test_bytes_source_and_save_roundtrip(tmp_path: Path) -> None:
    path = _rich_dxf(tmp_path)
    doc = DxfHandler().open(path.read_bytes())
    assert doc.path is None
    with pytest.raises(ValueError):
        doc.save()
    out = doc.save(tmp_path / "out.dxf")
    again = DxfHandler().open(out)
    assert again.text() == doc.text()
    assert again.layers() == doc.layers()
    inplace = DxfHandler().open(path)
    assert inplace.save() == path
    inplace.close()


def test_legacy_r12_opens() -> None:
    d = ezdxf.new("R12")
    d.modelspace().add_line((0, 0), (1, 1))
    stream = io.StringIO()
    d.write(stream)
    doc = DxfHandler().open(stream.getvalue().encode())
    assert doc.release == "R12"
    assert doc.entity_counts() == {"LINE": 1}


@pytest.mark.parametrize("data", [b"not a dxf at all", b"", b"0\nSECTION\n2\nHEADER\n"])
def test_garbage_is_corrupt(data: bytes) -> None:
    with pytest.raises(CorruptFileError):
        DxfHandler().open(data)


def test_unreadable_path_is_corrupt(tmp_path: Path) -> None:
    with pytest.raises(CorruptFileError):
        DxfHandler().open(tmp_path / "missing.dxf")
