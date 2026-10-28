"""Tests for the Typer CLI skeleton (T006)."""

from __future__ import annotations

import json
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import ClassVar

import pytest
from typer.testing import CliRunner

from anydoc import __version__
from anydoc.cli import app
from anydoc.core.errors import CorruptFileError
from anydoc.core.model import Capability, Metadata, Part
from anydoc.core.registry import FormatHandler, registry

runner = CliRunner()


class _FakeDoc:
    def __init__(self, path: Path, fail: bool = False) -> None:
        self.path: Path | None = path
        self.format = "txt"
        self.metadata = Metadata(format="txt", mime="text/plain", title="標題", page_count=1)
        self.closed = False
        self._fail = fail

    def parts(self) -> list[Part]:
        if self._fail:
            raise CorruptFileError("broken")
        return [Part(kind="page", index=0, name="第一頁", size=(1.0, 2.0))]

    def text(self) -> str:
        return ""

    def save(self, path: Path | None = None) -> Path:
        return path or Path("x")

    def close(self) -> None:
        self.closed = True


class _FakeHandler(FormatHandler):
    format_id = "txt"
    extensions: ClassVar[tuple[str, ...]] = ("txt",)
    mime_types: ClassVar[tuple[str, ...]] = ("text/plain",)
    capabilities: ClassVar[frozenset[Capability]] = frozenset({Capability.READ})

    def open(self, source: Path | bytes) -> _FakeDoc:  # type: ignore[override]
        assert isinstance(source, Path)
        return _FakeDoc(source, fail=source.name.startswith("bad"))


@pytest.fixture
def fake_txt() -> Iterator[None]:
    saved = registry.get("txt")
    registry.register(_FakeHandler, replace=True)
    yield
    registry.unregister("txt")
    if saved is not None:
        registry.register(saved)


def test_version() -> None:
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    assert __version__ in result.output


def test_no_args_shows_help() -> None:
    result = runner.invoke(app, [])
    assert "formats" in result.output
    assert "detect" in result.output
    assert "info" in result.output


def test_formats_empty() -> None:
    result = runner.invoke(app, ["formats"])
    assert result.exit_code == 0


@pytest.mark.usefixtures("fake_txt")
def test_formats_text_and_json() -> None:
    result = runner.invoke(app, ["formats"])
    assert result.exit_code == 0
    assert "txt" in result.output
    assert "read" in result.output
    result = runner.invoke(app, ["formats", "--json"])
    data = json.loads(result.output)
    txt = next(row for row in data if row["format"] == "txt")
    assert txt["capabilities"] == ["read"]
    assert txt["mime_types"] == ["text/plain"]


def test_formats_empty_message(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(registry, "_handlers", {})
    monkeypatch.setattr(registry, "load_entry_points", lambda: [])
    result = runner.invoke(app, ["formats"])
    assert result.exit_code == 0
    assert "No formats" in result.output


def test_detect_ok(sample: Callable[[str], Path]) -> None:
    result = runner.invoke(app, ["detect", str(sample("pdf"))])
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert data["format"] == "pdf"
    assert data["mime"] == "application/pdf"


def test_detect_missing_file(tmp_path: Path) -> None:
    result = runner.invoke(app, ["detect", str(tmp_path / "nope.pdf")])
    assert result.exit_code == 2
    assert "not found" in result.output


def test_detect_unknown_format(tmp_path: Path) -> None:
    f = tmp_path / "x.bin"
    f.write_bytes(b"\x00\x01\x02\xff\xfe\x00")
    result = runner.invoke(app, ["detect", str(f)])
    assert result.exit_code == 1
    assert "Error" in result.output


@pytest.mark.usefixtures("fake_txt")
def test_info_ok(sample: Callable[[str], Path]) -> None:
    result = runner.invoke(app, ["info", str(sample("txt"))])
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert data["format"] == "txt"
    assert data["metadata"]["title"] == "標題"
    assert data["parts"][0]["name"] == "第一頁"


def test_info_no_handler(sample: Callable[[str], Path]) -> None:
    result = runner.invoke(app, ["info", str(sample("dxf"))])
    assert result.exit_code == 1
    assert "No handler" in result.output


def test_info_missing_file(tmp_path: Path) -> None:
    result = runner.invoke(app, ["info", str(tmp_path / "nope.txt")])
    assert result.exit_code == 2
    assert "not found" in result.output


@pytest.mark.usefixtures("fake_txt")
def test_info_corrupt_document(tmp_path: Path) -> None:
    f = tmp_path / "bad.txt"
    f.write_text("hello 中文", encoding="utf-8")
    result = runner.invoke(app, ["info", str(f)])
    assert result.exit_code == 1
    assert "broken" in result.output


def test_main_invokes_app(monkeypatch: pytest.MonkeyPatch) -> None:
    import sys

    from anydoc.cli import main

    monkeypatch.setattr(sys, "argv", ["anydoc", "--version"])
    with pytest.raises(SystemExit) as exc:
        main()
    assert exc.value.code == 0
