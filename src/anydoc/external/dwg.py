"""DWG to DXF conversion through ODA File Converter or LibreDWG."""

from __future__ import annotations

import shutil
from collections.abc import Callable
from pathlib import Path
from typing import Any

from anydoc.core.errors import ExternalToolMissingError
from anydoc.external.tools import (
    DEFAULT_TIMEOUT,
    ExternalToolError,
    is_available,
    run_tool,
    temp_workdir,
)

DWG_CONVERTERS = ("oda", "libredwg")


def available_dwg_converter() -> str | None:
    """Return the name of the first installed DWG converter (``oda`` or ``libredwg``)."""
    return next((t for t in DWG_CONVERTERS if is_available(t)), None)


def convert_dwg_to_dxf(
    source: Path,
    outdir: Path,
    *,
    timeout: float = DEFAULT_TIMEOUT,
    runner: Callable[..., Any] | None = None,
) -> Path:
    """Convert the DWG file ``source`` to DXF inside ``outdir`` and return the DXF path.

    ODA File Converter is preferred, LibreDWG's ``dwg2dxf`` is the fallback.

    Raises:
        ExternalToolMissingError: if neither converter is installed.
        ExternalToolError: on failure, timeout, or when no output file was produced.
    """
    tool = available_dwg_converter()
    if tool is None:
        raise ExternalToolMissingError(
            "oda", "No DWG converter found (ODA File Converter or LibreDWG)"
        )
    extra: dict[str, Any] = {"runner": runner} if runner is not None else {}
    out = outdir / f"{source.stem}.dxf"
    if tool == "oda":
        with temp_workdir("anydoc-oda-") as work:
            indir = work / "in"
            indir.mkdir()
            shutil.copyfile(source, indir / source.name)
            run_tool(
                "oda",
                [indir, outdir, "ACAD2018", "DXF", "0", "1", source.name],
                timeout=timeout,
                **extra,
            )
    else:
        run_tool("libredwg", ["-y", "-o", out, source], timeout=timeout, **extra)
    if not out.is_file():
        raise ExternalToolError(f"{tool} produced no DXF output for {source.name}")
    return out
