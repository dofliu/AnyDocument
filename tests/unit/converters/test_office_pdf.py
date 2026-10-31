"""Tests for Office/ODF -> PDF converters (T032)."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from typer.testing import CliRunner

import anydoc
from anydoc.cli import app
from anydoc.converters import OFFICE_PDF_SOURCES, load_builtin_converters, register_office_pdf
from anydoc.core.convert import ConversionGraph, ConversionPathError, graph
from anydoc.external import convert_with_libreoffice, tools

runner = CliRunner()


def test_registers_every_source_format() -> None:
    g = ConversionGraph()
    register_office_pdf(g)
    assert {c.src for c in g.converters()} == set(OFFICE_PDF_SOURCES)
    assert all(c.dst == "pdf" and c.requires == ("libreoffice",) for c in g.converters())


def test_global_graph_loaded_once() -> None:
    load_builtin_converters()
    load_builtin_converters()
    pairs = [(c.src, c.dst) for c in graph.converters() if c.dst == "pdf"]
    assert len(pairs) == len(set(pairs))
    assert ("docx", "pdf") in pairs


def test_missing_libreoffice_reports_tool(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, sample
) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr(tools, "find_tool", lambda name: None)
    with pytest.raises(ConversionPathError, match="libreoffice"):
        anydoc.convert(sample("docx"), tmp_path / "out.pdf")


def test_missing_libreoffice_cli_nonzero(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, sample
) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr(tools, "find_tool", lambda name: None)
    result = runner.invoke(app, ["convert", str(sample("xlsx")), str(tmp_path / "o.pdf")])
    assert result.exit_code != 0


def _pdf_text(path: Path) -> str:
    return anydoc.extract_text(path)


@pytest.mark.requires("libreoffice")
@pytest.mark.parametrize(
    ("fmt", "needle"),
    [("docx", "測試"), ("xlsx", "測試"), ("pptx", "測試"), ("rtf", "測試")],
)
def test_real_conversion_to_pdf(fmt: str, needle: str, tmp_path: Path, sample) -> None:  # type: ignore[no-untyped-def]
    out = tmp_path / "out.pdf"
    anydoc.convert(sample(fmt), out)
    assert out.read_bytes().startswith(b"%PDF")
    doc = anydoc.open(out)
    try:
        assert doc.format == "pdf"
    finally:
        doc.close()
    assert needle in _pdf_text(out)


@pytest.mark.requires("libreoffice")
@pytest.mark.parametrize(("fmt", "mid"), [("docx", "odt"), ("xlsx", "ods"), ("pptx", "odp")])
def test_odf_to_pdf(fmt: str, mid: str, tmp_path: Path, sample) -> None:  # type: ignore[no-untyped-def]
    src = sample(fmt)
    odf = convert_with_libreoffice(src, mid, tmp_path)
    out = tmp_path / "odf.pdf"
    anydoc.convert(odf, out)
    assert out.read_bytes().startswith(b"%PDF")
    assert "測試" in _pdf_text(out)


@pytest.mark.requires("libreoffice")
def test_legacy_doc_to_pdf(tmp_path: Path, sample) -> None:  # type: ignore[no-untyped-def]
    legacy = convert_with_libreoffice(sample("docx"), "doc", tmp_path)
    out = tmp_path / "legacy.pdf"
    anydoc.convert(legacy, out)
    assert out.read_bytes().startswith(b"%PDF")


@pytest.mark.requires("libreoffice")
def test_cli_convert_docx_to_pdf(tmp_path: Path, sample) -> None:  # type: ignore[no-untyped-def]
    out = tmp_path / "cli.pdf"
    result = runner.invoke(app, ["convert", str(sample("docx")), str(out)])
    assert result.exit_code == 0, result.output
    assert out.read_bytes().startswith(b"%PDF")


@pytest.mark.requires("libreoffice")
def test_source_not_modified_and_no_leftovers(tmp_path: Path, sample) -> None:  # type: ignore[no-untyped-def]
    src = tmp_path / "in.docx"
    shutil.copyfile(sample("docx"), src)
    before = src.read_bytes()
    anydoc.convert(src, tmp_path / "x.pdf")
    assert src.read_bytes() == before
    assert sorted(p.name for p in tmp_path.iterdir()) == ["in.docx", "x.pdf"]
