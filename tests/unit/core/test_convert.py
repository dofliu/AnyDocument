"""Tests for the conversion graph engine (T031)."""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path

import pytest
from typer.testing import CliRunner

import anydoc
from anydoc.cli import app
from anydoc.core.convert import ConversionGraph, ConversionPathError, Converter, graph
from anydoc.core.errors import UnsupportedFormatError
from anydoc.external import tools

runner = CliRunner()


def _append(suffix: str):  # type: ignore[no-untyped-def]
    def func(src: Path, dst: Path) -> None:
        dst.write_text(src.read_text(encoding="utf-8") + suffix, encoding="utf-8")

    return func


def _noop(src: Path, dst: Path) -> None:
    return None


def test_find_path_direct_and_identity() -> None:
    g = ConversionGraph()
    g.add(Converter("a", "b", _noop, name="ab"))
    assert [c.name for c in g.find_path("a", "b")] == ["ab"]
    assert g.find_path("a", "a") == []


def test_find_path_prefers_lowest_cost() -> None:
    g = ConversionGraph()
    g.add(Converter("a", "d", _noop, cost=10, name="direct"))
    g.add(Converter("a", "b", _noop, cost=1, name="ab"))
    g.add(Converter("b", "c", _noop, cost=1, name="bc"))
    g.add(Converter("c", "d", _noop, cost=1, name="cd"))
    assert [c.name for c in g.find_path("a", "d")] == ["ab", "bc", "cd"]


def test_find_path_no_route() -> None:
    g = ConversionGraph()
    g.add(Converter("a", "b", _noop))
    with pytest.raises(ConversionPathError, match="No conversion path from b to a"):
        g.find_path("b", "a")


def test_invalid_cost_rejected() -> None:
    with pytest.raises(ValueError):
        ConversionGraph().add(Converter("a", "b", _noop, cost=0))


@pytest.fixture
def fake_tools(monkeypatch: pytest.MonkeyPatch) -> Iterator[set[str]]:
    present: set[str] = set()
    monkeypatch.setattr(tools, "find_tool", lambda t: Path("/x") if t in present else None)
    yield present


def test_skips_edges_with_missing_tool(fake_tools: set[str]) -> None:
    g = ConversionGraph()
    g.add(Converter("a", "b", _noop, cost=1, requires=("libreoffice",), name="lo"))
    g.add(Converter("a", "c", _noop, cost=1, name="ac"))
    g.add(Converter("c", "b", _noop, cost=5, name="cb"))
    assert [c.name for c in g.find_path("a", "b")] == ["ac", "cb"]
    fake_tools.add("libreoffice")
    assert [c.name for c in g.find_path("a", "b")] == ["lo"]


def test_missing_tool_error_mentions_tool(fake_tools: set[str]) -> None:
    g = ConversionGraph()
    g.add(Converter("a", "b", _noop, requires=("libreoffice",)))
    with pytest.raises(ConversionPathError, match="libreoffice"):
        g.find_path("a", "b")
    assert [c.name for c in g.find_path("a", "b", skip_unavailable=False)] == [""]


def test_targets_from(fake_tools: set[str]) -> None:
    g = ConversionGraph()
    g.add(Converter("a", "b", _noop))
    g.add(Converter("b", "c", _noop, requires=("tesseract",)))
    assert g.targets_from("a") == {"b"}
    assert g.targets_from("a", skip_unavailable=False) == {"b", "c"}
    assert g.formats() == {"a", "b", "c"}


def test_register_decorator_and_converters() -> None:
    g = ConversionGraph()

    @g.register("a", "b", cost=2, name="custom")
    def conv(src: Path, dst: Path) -> None:
        dst.write_text("x")

    assert g.converters()[0].name == "custom"
    assert g.converters()[0].cost == 2


def test_convert_chain_runs_each_step(tmp_path: Path) -> None:
    g = ConversionGraph()
    g.add(Converter("a", "b", _append("+b")))
    g.add(Converter("b", "c", _append("+c")))
    src = tmp_path / "in.txt"
    src.write_text("中文", encoding="utf-8")
    out = tmp_path / "sub" / "out.c"
    chain = g.convert(src, out, "a", "c")
    assert len(chain) == 2
    assert out.read_text(encoding="utf-8") == "中文+b+c"


def test_convert_same_format_copies(tmp_path: Path) -> None:
    src = tmp_path / "in.txt"
    src.write_text("hi")
    out = tmp_path / "out.txt"
    assert ConversionGraph().convert(src, out, "txt", "txt") == []
    assert out.read_text() == "hi"


def test_convert_step_without_output_fails(tmp_path: Path) -> None:
    g = ConversionGraph()
    g.add(Converter("a", "b", _noop, name="broken"))
    src = tmp_path / "in.txt"
    src.write_text("hi")
    with pytest.raises(ConversionPathError, match="broken"):
        g.convert(src, tmp_path / "out.b", "a", "b")


@pytest.fixture
def text_graph() -> Iterator[ConversionGraph]:
    """Temporarily register txt->md edges on the global graph."""
    saved = graph.converters()
    graph.add(Converter("txt", "markdown", _append("\n"), name="txt2md"))
    yield graph
    graph._edges[:] = saved


def test_public_convert_uses_detected_source(
    text_graph: ConversionGraph, tmp_path: Path, sample
) -> None:  # type: ignore[no-untyped-def]
    src = sample("txt")
    out = tmp_path / "out.md"
    assert anydoc.convert(src, out) == out
    assert out.read_text(encoding="utf-8") == src.read_text(encoding="utf-8") + "\n"


def test_public_convert_unknown_extension(tmp_path: Path, sample) -> None:  # type: ignore[no-untyped-def]
    with pytest.raises(UnsupportedFormatError):
        anydoc.convert(sample("txt"), tmp_path / "out.zzz")
    with pytest.raises(UnsupportedFormatError):
        anydoc.convert(sample("txt"), tmp_path / "noext")


def test_public_convert_missing_source(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        anydoc.convert(tmp_path / "nope.txt", tmp_path / "out.md")


def test_cli_convert_success(text_graph: ConversionGraph, tmp_path: Path, sample) -> None:  # type: ignore[no-untyped-def]
    out = tmp_path / "o.md"
    result = runner.invoke(app, ["convert", str(sample("txt")), str(out)])
    assert result.exit_code == 0, result.output
    assert out.exists()


def test_cli_convert_no_path(tmp_path: Path, sample) -> None:  # type: ignore[no-untyped-def]
    result = runner.invoke(app, ["convert", str(sample("txt")), str(tmp_path / "o.pdf")])
    assert result.exit_code == 1
    assert "No conversion path" in result.output


def test_cli_convert_missing_file(tmp_path: Path) -> None:
    result = runner.invoke(app, ["convert", str(tmp_path / "x.txt"), str(tmp_path / "o.md")])
    assert result.exit_code == 2


def test_cli_convert_unknown_target(tmp_path: Path, sample) -> None:  # type: ignore[no-untyped-def]
    result = runner.invoke(app, ["convert", str(sample("txt")), str(tmp_path / "o.zzz")])
    assert result.exit_code == 1
    assert json.dumps("Unknown target format")[1:-1] in result.output
