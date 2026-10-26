"""Locate and run optional external tools (LibreOffice, Tesseract, ODA, LibreDWG).

All tools are optional: callers should check :func:`is_available` or let
:func:`require_tool` raise :class:`~anydoc.core.errors.ExternalToolMissingError`.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from anydoc.core.errors import AnyDocError, ExternalToolMissingError

DEFAULT_TIMEOUT = 120.0


@dataclass(frozen=True)
class ToolSpec:
    """Describes how to find an external tool."""

    name: str
    executables: tuple[str, ...]
    env_var: str
    version_args: tuple[str, ...] = ("--version",)


@dataclass(frozen=True)
class ToolResult:
    """Outcome of a finished external tool run."""

    args: tuple[str, ...]
    returncode: int
    stdout: str
    stderr: str


class ExternalToolError(AnyDocError):
    """Raised when an external tool fails or times out."""


TOOLS: dict[str, ToolSpec] = {
    "libreoffice": ToolSpec("libreoffice", ("soffice", "libreoffice"), "ANYDOC_LIBREOFFICE"),
    "tesseract": ToolSpec("tesseract", ("tesseract",), "ANYDOC_TESSERACT"),
    "oda": ToolSpec(
        "oda",
        ("ODAFileConverter", "ODAFileConverter.exe", "TeighaFileConverter"),
        "ANYDOC_ODA",
        version_args=(),
    ),
    "libredwg": ToolSpec("libredwg", ("dwg2dxf",), "ANYDOC_LIBREDWG"),
}


def _spec(tool: str) -> ToolSpec:
    try:
        return TOOLS[tool.lower()]
    except KeyError:
        raise ValueError(f"unknown external tool: {tool!r}; known: {sorted(TOOLS)}") from None


def find_tool(tool: str) -> Path | None:
    """Return the executable path of ``tool``, or ``None`` if not installed.

    The environment variable ``ANYDOC_<TOOL>`` overrides the ``PATH`` lookup.
    """
    spec = _spec(tool)
    override = os.environ.get(spec.env_var)
    if override:
        found = shutil.which(override)
        return Path(found) if found else None
    for exe in spec.executables:
        found = shutil.which(exe)
        if found:
            return Path(found)
    return None


def is_available(tool: str) -> bool:
    """Return whether ``tool`` is installed and can be located."""
    return find_tool(tool) is not None


def available_tools() -> dict[str, bool]:
    """Map every known tool name to its availability."""
    return {name: is_available(name) for name in TOOLS}


def require_tool(tool: str) -> Path:
    """Return the path of ``tool`` or raise ``ExternalToolMissingError``."""
    path = find_tool(tool)
    if path is None:
        raise ExternalToolMissingError(tool)
    return path


@contextmanager
def temp_workdir(prefix: str = "anydoc-") -> Iterator[Path]:
    """Create a temporary working directory that is removed on exit."""
    with tempfile.TemporaryDirectory(prefix=prefix) as d:
        yield Path(d)


def run_tool(
    tool: str,
    args: Sequence[str | os.PathLike[str]] = (),
    *,
    timeout: float = DEFAULT_TIMEOUT,
    cwd: Path | None = None,
    check: bool = True,
    runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> ToolResult:
    """Run ``tool`` with ``args`` and return its captured output.

    Raises ``ExternalToolMissingError`` if the tool is absent and
    ``ExternalToolError`` on timeout or (when ``check``) a non-zero exit code.
    """
    exe = require_tool(tool)
    argv = [str(exe), *(os.fspath(a) for a in args)]
    try:
        proc = runner(argv, capture_output=True, text=True, timeout=timeout, cwd=cwd, check=False)
    except subprocess.TimeoutExpired:
        raise ExternalToolError(f"{tool} timed out after {timeout:g}s") from None
    except OSError as exc:
        raise ExternalToolError(f"failed to start {tool}: {exc}") from exc
    result = ToolResult(tuple(argv), proc.returncode, proc.stdout or "", proc.stderr or "")
    if check and result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip()
        raise ExternalToolError(f"{tool} exited with code {result.returncode}: {detail}")
    return result


def tool_version(tool: str) -> str | None:
    """Return the first line of the tool's version output, or ``None`` if unavailable."""
    spec = _spec(tool)
    if not spec.version_args or find_tool(tool) is None:
        return None
    try:
        res = run_tool(tool, spec.version_args, timeout=30, check=False)
    except AnyDocError:
        return None
    text = (res.stdout or res.stderr).strip()
    return text.splitlines()[0] if text else None
