"""Command-line interface for AnyDocument (``anydoc``)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated, Any

import typer

from anydoc import __version__
from anydoc.core.detect import detect as detect_file
from anydoc.core.errors import AnyDocError
from anydoc.core.registry import registry

app = typer.Typer(
    name="anydoc",
    help="Universal document viewer, converter and editor toolkit.",
    no_args_is_help=True,
    add_completion=False,
)

FileArg = Annotated[Path, typer.Argument(help="Path of the document.")]


def _fail(message: str, code: int = 1) -> typer.Exit:
    typer.echo(f"Error: {message}", err=True)
    return typer.Exit(code)


def _dump(data: Any) -> None:
    typer.echo(json.dumps(data, ensure_ascii=False, indent=2))


def _version_callback(value: bool) -> None:
    if value:
        typer.echo(__version__)
        raise typer.Exit()


@app.callback()
def _root(
    version: Annotated[
        bool,
        typer.Option("--version", callback=_version_callback, is_eager=True, help="Show version."),
    ] = False,
) -> None:
    """Universal document viewer, converter and editor toolkit."""


@app.command()
def formats(
    as_json: Annotated[bool, typer.Option("--json", help="Output JSON.")] = False,
) -> None:
    """List supported formats and their capabilities."""
    registry.load_entry_points()
    rows = [
        {
            "format": h.format_id,
            "extensions": list(h.extensions),
            "mime_types": list(h.mime_types),
            "capabilities": sorted(str(c) for c in h.capabilities),
        }
        for h in registry.all()
    ]
    if as_json:
        _dump(rows)
        return
    if not rows:
        typer.echo("No formats registered.")
        return
    for row in rows:
        exts = ",".join(row["extensions"]) or "-"
        caps = ",".join(row["capabilities"]) or "-"
        typer.echo(f"{row['format']}\t{exts}\t{caps}")


@app.command()
def detect(file: FileArg) -> None:
    """Detect a file's format from its content (JSON output)."""
    try:
        result = detect_file(file)
    except FileNotFoundError:
        raise _fail(f"file not found: {file}", 2) from None
    except AnyDocError as exc:
        raise _fail(str(exc)) from None
    _dump({"path": str(file), "format": result.format, "mime": result.mime})


@app.command()
def info(file: FileArg) -> None:
    """Show a document's metadata and parts (JSON output)."""
    from anydoc import open as open_document

    try:
        doc = open_document(file)
    except FileNotFoundError:
        raise _fail(f"file not found: {file}", 2) from None
    except AnyDocError as exc:
        raise _fail(str(exc)) from None
    try:
        payload = {
            "path": str(file),
            "format": doc.format,
            "metadata": doc.metadata.to_dict(),
            "parts": [p.to_dict() for p in doc.parts()],
        }
    except AnyDocError as exc:
        raise _fail(str(exc)) from None
    finally:
        doc.close()
    _dump(payload)


def main() -> None:
    """Console-script entry point."""
    app()
