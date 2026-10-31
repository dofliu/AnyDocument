"""Integration tests for the unified text extraction API (T030)."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest
from typer.testing import CliRunner

import anydoc
from anydoc.cli import app
from anydoc.core.errors import UnsupportedFormatError

runner = CliRunner()

TEXT_SAMPLES = [
    "txt",
    "json",
    "csv",
    "csv-bom",
    "csv-big5",
    "docx",
    "xlsx",
    "pptx",
    "pdf",
    "dxf",
    "rtf",
    "html",
    "epub",
]


@pytest.mark.parametrize("fmt", TEXT_SAMPLES)
def test_extract_text_every_format(sample: Callable[[str], Path], fmt: str) -> None:
    result = anydoc.extract_text(sample(fmt))
    assert isinstance(result, str)
    assert result.strip() != ""


@pytest.mark.parametrize("fmt", ["docx", "pdf", "xlsx", "pptx"])
def test_extract_text_matches_document_text(sample: Callable[[str], Path], fmt: str) -> None:
    path = sample(fmt)
    doc = anydoc.open(path)
    try:
        expected = doc.text()
    finally:
        doc.close()
    assert anydoc.extract_text(path) == expected


def test_extract_text_accepts_str_path(sample: Callable[[str], Path]) -> None:
    assert anydoc.extract_text(str(sample("docx"))).strip() != ""


def test_extract_text_image_has_no_text(sample: Callable[[str], Path]) -> None:
    assert anydoc.extract_text(sample("png")).strip() == ""


def test_extract_text_missing_file(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        anydoc.extract_text(tmp_path / "nope.docx")


def test_extract_text_unknown_format(tmp_path: Path) -> None:
    p = tmp_path / "x.bin"
    p.write_bytes(b"\x00\x01\x02\x03" * 8)
    with pytest.raises(UnsupportedFormatError):
        anydoc.extract_text(p)


def test_cli_text_stdout(sample: Callable[[str], Path]) -> None:
    result = runner.invoke(app, ["text", str(sample("pdf"))])
    assert result.exit_code == 0
    assert result.output.strip() != ""


def test_cli_text_output_file(sample: Callable[[str], Path], tmp_path: Path) -> None:
    out = tmp_path / "out.txt"
    result = runner.invoke(app, ["text", str(sample("docx")), "-o", str(out)])
    assert result.exit_code == 0
    assert out.read_text(encoding="utf-8") == anydoc.extract_text(sample("docx"))


def test_cli_text_missing_file(tmp_path: Path) -> None:
    result = runner.invoke(app, ["text", str(tmp_path / "nope.pdf")])
    assert result.exit_code == 2


def test_cli_text_unsupported(tmp_path: Path) -> None:
    p = tmp_path / "x.bin"
    p.write_bytes(b"\x00\x01\x02\x03" * 8)
    result = runner.invoke(app, ["text", str(p)])
    assert result.exit_code == 1
