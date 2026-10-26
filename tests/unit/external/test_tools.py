"""Tests for anydoc.external (tool detection and execution)."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from anydoc.core.errors import ExternalToolMissingError
from anydoc.external import (
    TOOLS,
    ToolResult,
    available_tools,
    find_tool,
    is_available,
    require_tool,
    run_tool,
    tool_version,
)
from anydoc.external import tools as tools_mod
from anydoc.external.tools import ExternalToolError, temp_workdir
from tests.conftest import pytest_runtest_setup


@pytest.fixture
def no_tools(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(tools_mod.shutil, "which", lambda _name: None)
    for spec in TOOLS.values():
        monkeypatch.delenv(spec.env_var, raising=False)


@pytest.fixture
def fake_which(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(tools_mod.shutil, "which", lambda name: f"/fake/bin/{name}")
    for spec in TOOLS.values():
        monkeypatch.delenv(spec.env_var, raising=False)


def test_missing_tool(no_tools: None) -> None:
    assert find_tool("tesseract") is None
    assert not is_available("tesseract")
    assert available_tools() == dict.fromkeys(TOOLS, False)
    with pytest.raises(ExternalToolMissingError) as exc:
        require_tool("tesseract")
    assert exc.value.tool == "tesseract"
    with pytest.raises(ExternalToolMissingError):
        run_tool("libreoffice", ["--version"])
    assert tool_version("tesseract") is None


def test_present_tool(fake_which: None) -> None:
    assert find_tool("libreoffice") == Path("/fake/bin/soffice")
    assert find_tool("LibreDWG") == Path("/fake/bin/dwg2dxf")
    assert is_available("oda")
    assert all(available_tools().values())
    assert require_tool("tesseract") == Path("/fake/bin/tesseract")


def test_unknown_tool() -> None:
    with pytest.raises(ValueError):
        find_tool("nope")


def test_executable_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ANYDOC_LIBREOFFICE", raising=False)
    monkeypatch.setattr(
        tools_mod.shutil, "which", lambda n: "/x/libreoffice" if n == "libreoffice" else None
    )
    assert find_tool("libreoffice") == Path("/x/libreoffice")


def test_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(tools_mod.shutil, "which", lambda n: n if n == "/custom/tess" else None)
    monkeypatch.setenv("ANYDOC_TESSERACT", "/custom/tess")
    assert find_tool("tesseract") == Path("/custom/tess")
    monkeypatch.setenv("ANYDOC_TESSERACT", "/missing")
    assert find_tool("tesseract") is None


def _completed(rc: int = 0, out: str = "", err: str = "") -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess([], rc, out, err)


def test_run_tool_success(fake_which: None) -> None:
    seen: dict[str, Any] = {}

    def runner(argv: list[str], **kw: Any) -> subprocess.CompletedProcess[str]:
        seen.update(argv=argv, **kw)
        return _completed(0, "ok", "")

    res = run_tool("tesseract", ["a.png", Path("out")], timeout=5, runner=runner)
    assert res.stdout == "ok"
    assert seen["argv"] == ["/fake/bin/tesseract", "a.png", "out"]
    assert seen["timeout"] == 5


def test_run_tool_failure(fake_which: None) -> None:
    def runner(argv: list[str], **kw: Any) -> subprocess.CompletedProcess[str]:
        return _completed(3, "", "boom")

    with pytest.raises(ExternalToolError, match="code 3: boom"):
        run_tool("tesseract", runner=runner)
    assert run_tool("tesseract", check=False, runner=runner).returncode == 3


def test_run_tool_timeout_and_oserror(fake_which: None) -> None:
    def slow(argv: list[str], **kw: Any) -> subprocess.CompletedProcess[str]:
        raise subprocess.TimeoutExpired(argv, 1)

    def bad(argv: list[str], **kw: Any) -> subprocess.CompletedProcess[str]:
        raise PermissionError("nope")

    with pytest.raises(ExternalToolError, match="timed out"):
        run_tool("tesseract", timeout=1, runner=slow)
    with pytest.raises(ExternalToolError, match="failed to start"):
        run_tool("tesseract", runner=bad)


def test_run_real_process_with_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANYDOC_TESSERACT", sys.executable)
    res = run_tool("tesseract", ["-c", "print('hi')"])
    assert res.stdout.strip() == "hi"
    with pytest.raises(ExternalToolError, match="timed out"):
        run_tool("tesseract", ["-c", "import time; time.sleep(5)"], timeout=0.3)


def test_tool_version(monkeypatch: pytest.MonkeyPatch, fake_which: None) -> None:
    monkeypatch.setattr(tools_mod, "run_tool", lambda *a, **k: ToolResult((), 0, "tess 5.3\nx", ""))
    assert tool_version("tesseract") == "tess 5.3"
    assert tool_version("oda") is None  # no version args
    monkeypatch.setattr(tools_mod, "run_tool", lambda *a, **k: ToolResult((), 0, "", ""))
    assert tool_version("tesseract") is None

    def boom(*a: object, **k: object) -> None:
        raise ExternalToolError("x")

    monkeypatch.setattr(tools_mod, "run_tool", boom)
    assert tool_version("tesseract") is None


def test_temp_workdir() -> None:
    with temp_workdir() as d:
        assert d.is_dir()
        (d / "f").write_text("x")
    assert not d.exists()


def _item(*tools: str) -> Any:
    marker = SimpleNamespace(args=tools)
    return SimpleNamespace(iter_markers=lambda name: [marker])


def test_requires_hook_skips_when_missing(no_tools: None) -> None:
    with pytest.raises(pytest.skip.Exception):
        pytest_runtest_setup(_item("libreoffice"))


def test_requires_hook_passes_when_present(fake_which: None) -> None:
    pytest_runtest_setup(_item("libreoffice", "tesseract"))


@pytest.mark.requires("libreoffice")
def test_libreoffice_real_version() -> None:
    assert tool_version("libreoffice")
