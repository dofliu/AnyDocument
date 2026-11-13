"""Tests for the ``anydoc dxf`` block commands (T084)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import ezdxf
import pytest
from typer.testing import CliRunner

from anydoc.cli import app
from anydoc.formats.dxf import DxfDocument, DxfHandler

runner = CliRunner()


@pytest.fixture
def drawing(tmp_path: Path) -> Path:
    doc = ezdxf.new("R2018")
    doc.layers.add("牆", color=1)
    block = doc.blocks.new("門")
    block.add_line((0, 0), (10, 0))
    block.add_attdef("編號", (0, -3), "D-00", dxfattribs={"height": 2})
    msp = doc.modelspace()
    msp.add_line((0, 0), (10, 0), dxfattribs={"layer": "牆"})
    msp.add_circle((5, 5), 2, dxfattribs={"layer": "牆"})
    path = tmp_path / "in.dxf"
    doc.saveas(path)
    return path


def _run(*args: str) -> Any:
    result = runner.invoke(app, ["dxf", *args])
    assert result.exit_code == 0, result.output
    return result


def _json(*args: str) -> Any:
    return json.loads(_run(*args).stdout)


def _open(path: Path) -> DxfDocument:
    return DxfHandler().open(path)


def _added(result: Any) -> str:
    return str(result.stderr.split("added ")[1].split()[0])


def test_blocks_lists_json(drawing: Path) -> None:
    (block,) = _json("blocks", str(drawing))
    assert block["name"] == "門" and block["references"] == 0
    assert block["entity_counts"] == {"ATTDEF": 1, "LINE": 1}
    assert block["attdefs"][0]["tag"] == "編號" and block["attdefs"][0]["default"] == "D-00"


def test_block_define_from_selection_and_replace(drawing: Path, tmp_path: Path) -> None:
    out = tmp_path / "out.dxf"
    result = _run(
        "block-define",
        str(drawing),
        "牆組",
        "-o",
        str(out),
        "--layer",
        "牆",
        "--base",
        "5,0",
        "--replace",
        "--description",
        "牆面組合",
    )
    assert result.stdout.strip() == str(out) and "defined 牆組 (2 entities)" in result.stderr
    doc = _open(out)
    block = doc.block("牆組")
    assert (block.base_point, block.references, block.description) == (
        (5.0, 0.0, 0.0),
        1,
        "牆面組合",
    )
    assert doc.entity_counts() == {"INSERT": 1}


def test_block_define_empty_by_handles_and_keep(drawing: Path, tmp_path: Path) -> None:
    out = tmp_path / "out.dxf"
    _run("block-define", str(drawing), "空", "-o", str(out))
    assert _open(out).block("空").entity_count == 0
    handle = _open(drawing).select(types="CIRCLE")[0]
    _run("block-define", str(drawing), "圓", handle, "-o", str(out), "--keep")
    doc = _open(out)
    assert doc.block("圓").entity_counts == {"CIRCLE": 1}
    assert doc.entity_counts() == {"CIRCLE": 1, "LINE": 1}


def test_entity_add_and_list_in_block(drawing: Path, tmp_path: Path) -> None:
    out = tmp_path / "out.dxf"
    _run(
        "entity-add",
        str(drawing),
        "circle",
        "-o",
        str(out),
        "-p",
        "5,5",
        "-r",
        "2",
        "--block",
        "門",
    )
    rows = _json("entities", str(out), "--block", "門")
    assert [row["type"] for row in rows] == ["LINE", "ATTDEF", "CIRCLE"]
    assert rows[2]["layout"] == "門" and rows[2]["radius"] == 2.0
    assert len(_json("entities", str(out))) == 2


def test_block_attdef(drawing: Path, tmp_path: Path) -> None:
    out = tmp_path / "out.dxf"
    result = _run(
        "block-attdef",
        str(drawing),
        "門",
        "寬度",
        "-o",
        str(out),
        "--at",
        "0,3",
        "--default",
        "90",
        "--prompt",
        "寬度?",
        "--height",
        "3",
        "--invisible",
    )
    attdef = _open(out).block("門").attdefs[-1]
    assert attdef.handle == _added(result)
    assert (attdef.tag, attdef.default, attdef.prompt) == ("寬度", "90", "寬度?")
    assert attdef.insert == (0.0, 3.0, 0.0) and attdef.height == 3.0
    assert attdef.invisible and not attdef.constant
    _run("block-attdef", str(drawing), "門", "K", "-o", str(out), "--default", "v", "--constant")
    assert _open(out).block("門").attdefs[-1].constant


def test_block_insert_inserts_and_insert_set(drawing: Path, tmp_path: Path) -> None:
    out = tmp_path / "out.dxf"
    result = _run(
        "block-insert",
        str(drawing),
        "門",
        "-o",
        str(out),
        "--at",
        "100,50",
        "--scale",
        "2",
        "--rotation",
        "90",
        "--attr",
        "編號=D-01",
        "--layer",
        "牆",
        "--color",
        "blue",
    )
    handle = _added(result)
    (row,) = _json("inserts", str(out))
    assert row == {
        "handle": handle,
        "name": "門",
        "layout": "Model",
        "layer": "牆",
        "insert": [100.0, 50.0, 0.0],
        "scale": [2.0, 2.0, 2.0],
        "rotation": 90.0,
        "attributes": {"編號": "D-01"},
    }
    assert _json("inserts", str(out), "--name", "門") == [row]
    assert _json("inserts", str(out), "--layout", "Layout1") == []
    assert _json("blocks", str(out))[0]["references"] == 1

    out2 = tmp_path / "out2.dxf"
    _run(
        "insert-set",
        str(out),
        handle,
        "-o",
        str(out2),
        "-a",
        "編號=大門=A",
        "--at",
        "0,0",
        "--scale",
        "1,3",
        "--rotation",
        "0",
    )
    (row,) = _json("inserts", str(out2))
    assert row["attributes"] == {"編號": "大門=A"}
    assert (row["insert"], row["scale"], row["rotation"]) == (
        [0.0, 0.0, 0.0],
        [1.0, 3.0, 1.0],
        0.0,
    )
    _run("insert-set", str(out2), handle, "-o", str(out2), "-a", "編號=")
    assert _json("inserts", str(out2))[0]["attributes"] == {"編號": ""}


def test_block_insert_into_block(drawing: Path, tmp_path: Path) -> None:
    out = tmp_path / "out.dxf"
    _run("block-define", str(drawing), "房間", "-o", str(out))
    _run("block-insert", str(out), "門", "-o", str(out), "--at", "1,1", "--block", "房間")
    (row,) = _json("inserts", str(out), "--block", "房間")
    assert row["layout"] == "房間" and row["scale"] == [1.0, 1.0, 1.0]
    assert _json("inserts", str(out)) == []


def test_block_rename_delete_purge(drawing: Path, tmp_path: Path) -> None:
    out = tmp_path / "out.dxf"
    _run("block-insert", str(drawing), "門", "-o", str(out))
    _run("block-rename", str(out), "門", "大門", "-o", str(out))
    assert _json("inserts", str(out))[0]["name"] == "大門"
    assert [b["name"] for b in _json("blocks", str(out))] == ["大門"]

    result = runner.invoke(app, ["dxf", "block-delete", str(out), "大門", "-o", str(out)])
    assert result.exit_code == 1 and "has 1 references" in result.output
    result = _run("block-delete", str(out), "大門", "-o", str(out), "--references")
    assert "deleted 1 references" in result.stderr
    assert _json("blocks", str(out)) == [] and _json("inserts", str(out)) == []

    result = _run("block-purge", str(drawing), "-o", str(out))
    assert "removed 門" in result.stderr and _json("blocks", str(out)) == []


def test_explode(drawing: Path, tmp_path: Path) -> None:
    src, out = tmp_path / "refs.dxf", tmp_path / "out.dxf"
    first = _added(_run("block-insert", str(drawing), "門", "-o", str(src), "-a", "編號=A"))
    _run("block-insert", str(src), "門", "-o", str(src), "--at", "50,0", "-a", "編號=B")

    result = _run("explode", str(src), first, "-o", str(out))
    assert "exploded 1 references into 2 entities" in result.stderr
    doc = _open(out)
    assert doc.entity_counts() == {"CIRCLE": 1, "INSERT": 1, "LINE": 2, "TEXT": 1}
    assert "A" in doc.text().splitlines()

    _run("explode", str(src), "-o", str(out), "--name", "門", "--drop-attributes")
    assert _open(out).entity_counts() == {"CIRCLE": 1, "LINE": 3}
    _run("explode", str(src), "-o", str(out), "--all", "--recursive")
    assert _open(out).entity_counts() == {"CIRCLE": 1, "LINE": 3, "TEXT": 2}


@pytest.mark.parametrize(
    ("args", "message"),
    [
        (["block-define", "門"], "already exists"),
        (["block-define", "新", "--replace"], "needs the handles"),
        (["block-define", "新", "--layer", "0"], "No entities match"),
        (["block-define", "新", "--base", "x"], "Invalid point"),
        (["block-attdef", "門", "編號"], "already has an attribute"),
        (["block-attdef", "沒有", "T"], "No such block: 沒有"),
        (["block-rename", "沒有", "新"], "No such block"),
        (["block-delete", "沒有"], "No such block"),
        (["block-insert", "沒有"], "No such block"),
        (["block-insert", "門", "--scale", "0"], "must not be 0"),
        (["block-insert", "門", "--scale", "a,b"], "Invalid scale"),
        (["block-insert", "門", "--attr", "編號"], "expected TAG=VALUE"),
        (["block-insert", "門", "--attr", "沒有=1"], "no attribute tagged"),
        (["insert-set", "FFFF", "-a", "編號=1"], "No such entity"),
        (["insert-set", "FFFF"], "Nothing to change"),
        (["explode"], "No block references selected"),
        (["explode", "--all"], "No block references match"),
        (["explode", "FFFF", "--all"], "not both"),
        (["explode", "FFFF"], "No such entity"),
    ],
)
def test_block_errors(drawing: Path, tmp_path: Path, args: list[str], message: str) -> None:
    out = tmp_path / "out.dxf"
    result = runner.invoke(app, ["dxf", args[0], str(drawing), *args[1:], "-o", str(out)])
    assert result.exit_code == 1, result.output
    assert message in result.output
    assert not out.exists()


def test_insert_set_on_non_insert_fails(drawing: Path, tmp_path: Path) -> None:
    out = tmp_path / "out.dxf"
    line = _open(drawing).select(types="LINE")[0]
    result = runner.invoke(
        app, ["dxf", "insert-set", str(drawing), line, "-o", str(out), "--at", "1,1"]
    )
    assert result.exit_code == 1 and "not an INSERT" in result.output
    assert not out.exists()


def test_inserts_unknown_block_and_missing_file(drawing: Path, tmp_path: Path) -> None:
    result = runner.invoke(app, ["dxf", "inserts", str(drawing), "--name", "沒有"])
    assert result.exit_code == 1 and "No such block: 沒有" in result.output
    result = runner.invoke(app, ["dxf", "blocks", str(tmp_path / "missing.dxf")])
    assert result.exit_code == 2
