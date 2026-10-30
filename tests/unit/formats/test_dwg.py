"""Tests for DWG support: version detection, conversion and graceful degradation (T021)."""

from __future__ import annotations

import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

import anydoc
from anydoc.core.errors import CorruptFileError, ExternalToolMissingError
from anydoc.core.model import Capability
from anydoc.core.registry import registry
from anydoc.external import convert_dwg_to_dxf
from anydoc.external import dwg as ext_dwg
from anydoc.external.tools import ExternalToolError
from anydoc.formats.dwg import DwgDocument, DwgHandler, dwg_release, dwg_version

DWG_BYTES = b"AC1032" + b"\x00" * 64


@pytest.fixture(autouse=True)
def _handler() -> None:
    registry.register(DwgHandler, replace=True)


@pytest.fixture
def dwg_file(tmp_path: Path) -> Path:
    p = tmp_path / "plan.dwg"
    p.write_bytes(DWG_BYTES)
    return p


def _tools(monkeypatch: pytest.MonkeyPatch, available: set[str]) -> None:
    monkeypatch.setattr(ext_dwg, "is_available", lambda t: t in available)
    monkeypatch.setattr("anydoc.external.tools.find_tool", lambda t: Path("/fake/" + t))
    monkeypatch.setattr("anydoc.external.tools.require_tool", lambda t: Path("/fake/" + t))


def _fake_runner(dxf_source: Path, calls: list[list[str]]) -> Callable[..., Any]:
    """Pretend to be dwg2dxf / ODAFileConverter by emitting a real DXF."""

    def run(argv: list[str], **_: Any) -> subprocess.CompletedProcess[str]:
        calls.append(argv)
        if "dwg2dxf" in argv[0] or argv[0].endswith("libredwg"):
            out = Path(argv[argv.index("-o") + 1])
        else:
            out = Path(argv[2]) / (Path(argv[7]).stem + ".dxf")
        out.write_bytes(dxf_source.read_bytes())
        return subprocess.CompletedProcess(argv, 0, "", "")

    return run


# -- version / sniff -----------------------------------------------------------


def test_version_and_release() -> None:
    assert dwg_version(DWG_BYTES) == "AC1032"
    assert dwg_release("AC1032") == "R2018"
    assert dwg_release("AC1027") == "R2013"
    assert dwg_release("AC1009") == "R11/R12"
    assert dwg_release("ACXXXX") == "ACXXXX"
    assert dwg_version(b"AC9999") is None
    assert dwg_version(b"") is None


def test_handler_metadata() -> None:
    assert DwgHandler.format_id == "dwg"
    assert DwgHandler.extensions == ("dwg",)
    assert Capability.READ in DwgHandler.capabilities


def test_sniff() -> None:
    assert DwgHandler.sniff(DWG_BYTES, None) == 1.0
    assert DwgHandler.sniff(b"xx", Path("a.dwg")) == 0.5
    assert DwgHandler.sniff(b"xx", Path("a.txt")) == 0.0


def test_detect_dwg(dwg_file: Path) -> None:
    assert anydoc.detect(dwg_file).format == "dwg"


# -- degradation without tools -----------------------------------------------


def test_open_without_tool_returns_metadata_only(
    monkeypatch: pytest.MonkeyPatch, dwg_file: Path
) -> None:
    _tools(monkeypatch, set())
    doc = DwgHandler().open(dwg_file)
    assert isinstance(doc, DwgDocument)
    assert doc.version == "AC1032"
    assert doc.release == "R2018"
    assert not doc.converted
    assert doc.dxf is None
    assert doc.parts() == []
    assert doc.text() == ""
    assert doc.metadata.format == "dwg"
    assert doc.metadata.extra["release"] == "R2018"
    assert doc.metadata.extra["converted"] is False
    doc.close()


def test_anydoc_open_dwg(monkeypatch: pytest.MonkeyPatch, dwg_file: Path) -> None:
    _tools(monkeypatch, set())
    doc = anydoc.open(dwg_file)
    assert doc.format == "dwg"
    assert doc.metadata.extra["version"] == "AC1032"


def test_open_bytes_and_save(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _tools(monkeypatch, set())
    doc = DwgHandler().open(DWG_BYTES)
    with pytest.raises(ValueError):
        doc.save()
    out = doc.save(tmp_path / "copy.dwg")
    assert out.read_bytes() == DWG_BYTES


def test_save_in_place(monkeypatch: pytest.MonkeyPatch, dwg_file: Path) -> None:
    _tools(monkeypatch, set())
    assert DwgHandler().open(dwg_file).save() == dwg_file


def test_invalid_header(tmp_path: Path) -> None:
    with pytest.raises(CorruptFileError):
        DwgHandler().open(b"not a dwg at all")
    bad = tmp_path / "bad.dwg"
    bad.write_bytes(b"garbage")
    with pytest.raises(CorruptFileError):
        DwgHandler().open(bad)
    with pytest.raises(CorruptFileError):
        DwgHandler().open(tmp_path / "missing.dwg")


def test_convert_without_tool(
    monkeypatch: pytest.MonkeyPatch, dwg_file: Path, tmp_path: Path
) -> None:
    _tools(monkeypatch, set())
    assert ext_dwg.available_dwg_converter() is None
    with pytest.raises(ExternalToolMissingError):
        convert_dwg_to_dxf(dwg_file, tmp_path)


# -- conversion with a (simulated) converter -----------------------------------


@pytest.mark.parametrize("tool", ["libredwg", "oda"])
def test_open_with_converter(
    monkeypatch: pytest.MonkeyPatch,
    sample: Callable[[str], Path],
    dwg_file: Path,
    tool: str,
) -> None:
    _tools(monkeypatch, {tool})
    calls: list[list[str]] = []
    runner = _fake_runner(sample("dxf"), calls)
    real = ext_dwg.run_tool

    def run_tool(*a: Any, **kw: Any) -> Any:
        kw["runner"] = runner
        return real(*a, **kw)

    monkeypatch.setattr(ext_dwg, "run_tool", run_tool)
    doc = DwgHandler().open(dwg_file)
    assert doc.converted
    assert doc.converter == tool
    assert doc.metadata.extra["converter"] == tool
    assert doc.metadata.extra["version"] == "AC1032"
    assert doc.parts()[0].kind == "layout"
    assert doc.metadata.page_count == len(doc.parts())
    assert doc.text() == doc.dxf.text()  # type: ignore[union-attr]
    assert doc.dxf is not None and doc.dxf.layers()
    assert len(calls) == 1
    doc.close()


def test_oda_preferred_over_libredwg(monkeypatch: pytest.MonkeyPatch) -> None:
    _tools(monkeypatch, {"oda", "libredwg"})
    assert ext_dwg.available_dwg_converter() == "oda"


def test_converter_failure_is_corrupt(monkeypatch: pytest.MonkeyPatch, dwg_file: Path) -> None:
    _tools(monkeypatch, {"libredwg"})

    def boom(*_: Any, **__: Any) -> Any:
        raise ExternalToolError("dwg2dxf exited with code 1")

    monkeypatch.setattr(ext_dwg, "run_tool", boom)
    with pytest.raises(CorruptFileError):
        DwgHandler().open(dwg_file)


def test_converter_produces_no_output(
    monkeypatch: pytest.MonkeyPatch, dwg_file: Path, tmp_path: Path
) -> None:
    _tools(monkeypatch, {"libredwg"})
    monkeypatch.setattr(ext_dwg, "run_tool", lambda *a, **k: None)
    with pytest.raises(ExternalToolError):
        convert_dwg_to_dxf(dwg_file, tmp_path)


# -- real tools ----------------------------------------------------------------


@pytest.mark.requires("libredwg")
def test_real_libredwg_rejects_garbage(dwg_file: Path) -> None:
    with pytest.raises(CorruptFileError):
        DwgHandler().open(dwg_file)
