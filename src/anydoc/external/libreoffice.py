"""LibreOffice headless conversion helper."""

from __future__ import annotations

from pathlib import Path

from anydoc.external.tools import DEFAULT_TIMEOUT, ExternalToolError, run_tool, temp_workdir


def convert_with_libreoffice(
    source: Path, target_ext: str, outdir: Path, *, timeout: float = DEFAULT_TIMEOUT
) -> Path:
    """Convert ``source`` to ``target_ext`` (e.g. ``"docx"``, ``"pdf"``) inside ``outdir``.

    Uses a throw-away user profile so concurrent runs do not clash. Returns the output path.

    Raises:
        ExternalToolMissingError: if LibreOffice is not installed.
        ExternalToolError: on failure, timeout, or when no output file was produced.
    """
    ext = target_ext.lower().lstrip(".")
    with temp_workdir("anydoc-lo-profile-") as profile:
        run_tool(
            "libreoffice",
            [
                f"-env:UserInstallation={profile.resolve().as_uri()}",
                "--headless",
                "--convert-to",
                ext,
                "--outdir",
                outdir,
                source,
            ],
            timeout=timeout,
        )
    out = outdir / f"{source.stem}.{ext}"
    if not out.is_file():
        raise ExternalToolError(f"LibreOffice produced no .{ext} output for {source.name}")
    return out
