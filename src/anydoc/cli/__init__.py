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


@app.command()
def text(
    file: FileArg,
    output: Annotated[
        Path | None,
        typer.Option("--output", "-o", help="Write the text to this UTF-8 file instead of stdout."),
    ] = None,
) -> None:
    """Extract a document's text in reading order."""
    from anydoc import extract_text

    try:
        content = extract_text(file)
    except FileNotFoundError:
        raise _fail(f"file not found: {file}", 2) from None
    except AnyDocError as exc:
        raise _fail(str(exc)) from None
    if output is not None:
        output.write_text(content, encoding="utf-8")
        return
    typer.echo(content)


@app.command()
def convert(
    source: Annotated[Path, typer.Argument(help="Input document.")],
    target: Annotated[Path, typer.Argument(help="Output path; its extension selects the format.")],
) -> None:
    """Convert a document to another format (target inferred from the output extension)."""
    from anydoc.core.convert import convert as convert_file

    try:
        convert_file(source, target)
    except FileNotFoundError:
        raise _fail(f"file not found: {source}", 2) from None
    except AnyDocError as exc:
        raise _fail(str(exc)) from None
    typer.echo(str(target))


@app.command()
def thumbnail(
    source: Annotated[Path, typer.Argument(help="Input document.")],
    target: Annotated[Path, typer.Argument(help="Output image (.png, .jpg or .webp).")],
    size: Annotated[int, typer.Option("--size", "-s", help="Longest side in pixels.")] = 256,
    page: Annotated[int, typer.Option("--page", "-p", help="0-based page/slide/layout.")] = 0,
) -> None:
    """Create a thumbnail image of a document."""
    from anydoc import thumbnail as make

    try:
        make(source, target, size=size, page=page)
    except FileNotFoundError:
        raise _fail(f"file not found: {source}", 2) from None
    except (AnyDocError, ValueError, IndexError) as exc:
        raise _fail(str(exc)) from None
    typer.echo(str(target))


pdf_app = typer.Typer(help="PDF page operations.", no_args_is_help=True)
app.add_typer(pdf_app, name="pdf")


def _read_pdf(path: Path) -> bytes:
    try:
        return path.read_bytes()
    except FileNotFoundError:
        raise _fail(f"file not found: {path}", 2) from None


def _pdf_page_count(data: bytes) -> int:
    import io

    from pypdf import PdfReader

    try:
        return len(PdfReader(io.BytesIO(data)).pages)
    except Exception as exc:
        raise _fail(f"Invalid PDF: {exc}") from None


def _pdf_edit(source: Path, output: Path, op: Any, spec: str) -> None:
    """Run ``op(data, page_indexes)`` on ``source`` and write the result to ``output``."""
    from anydoc.formats.pdf.pages import parse_page_spec

    data = _read_pdf(source)
    try:
        indexes = parse_page_spec(spec, _pdf_page_count(data))
        output.write_bytes(op(data, indexes))
    except (AnyDocError, ValueError, IndexError) as exc:
        raise _fail(str(exc)) from None
    typer.echo(str(output))


PagesArg = Annotated[str, typer.Argument(help='1-based pages, e.g. "1,3-5,8-".')]
OutOpt = Annotated[Path, typer.Option("--output", "-o", help="Output PDF path.")]


@pdf_app.command("merge")
def pdf_merge(
    sources: Annotated[list[Path], typer.Argument(help="PDFs to merge, in order.")],
    output: OutOpt,
) -> None:
    """Merge several PDFs into one."""
    from anydoc.formats.pdf.pages import merge_pdfs

    try:
        output.write_bytes(merge_pdfs([_read_pdf(p) for p in sources]))
    except (AnyDocError, ValueError) as exc:
        raise _fail(str(exc)) from None
    typer.echo(str(output))


@pdf_app.command("split")
def pdf_split(
    source: FileArg,
    out_dir: Annotated[Path, typer.Option("--out-dir", "-d", help="Output directory.")],
    every: Annotated[int, typer.Option("--every", "-n", help="Pages per output file.")] = 1,
) -> None:
    """Split a PDF into files of N pages each (<stem>-001.pdf, ...)."""
    from anydoc.formats.pdf.pages import split_file

    _read_pdf(source)
    try:
        paths = split_file(source, out_dir, chunk_size=every)
    except (AnyDocError, ValueError) as exc:
        raise _fail(str(exc)) from None
    for p in paths:
        typer.echo(str(p))


@pdf_app.command("extract")
def pdf_extract(source: FileArg, pages: PagesArg, output: OutOpt) -> None:
    """Extract pages into a new PDF (order and duplicates preserved)."""
    from anydoc.formats.pdf.pages import extract_pages

    _pdf_edit(source, output, lambda d, ix: extract_pages(d, ix), pages)


@pdf_app.command("delete")
def pdf_delete(source: FileArg, pages: PagesArg, output: OutOpt) -> None:
    """Delete pages."""
    from anydoc.formats.pdf.pages import delete_pages

    _pdf_edit(source, output, lambda d, ix: delete_pages(d, ix), pages)


@pdf_app.command("rotate")
def pdf_rotate(
    source: FileArg,
    output: OutOpt,
    angle: Annotated[
        int, typer.Option("--angle", "-a", help="Clockwise degrees (multiple of 90).")
    ] = 90,
    pages: Annotated[str, typer.Option("--pages", "-p", help="Pages to rotate.")] = "all",
) -> None:
    """Rotate pages clockwise."""
    from anydoc.formats.pdf.pages import rotate_pages

    _pdf_edit(source, output, lambda d, ix: rotate_pages(d, angle, ix), pages)


@pdf_app.command("reorder")
def pdf_reorder(
    source: FileArg,
    order: Annotated[str, typer.Argument(help='New order of every page, e.g. "3,1,2".')],
    output: OutOpt,
) -> None:
    """Reorder pages; the order must list every page exactly once."""
    from anydoc.formats.pdf.pages import reorder_pages

    _pdf_edit(source, output, lambda d, ix: reorder_pages(d, ix), order)


@pdf_app.command("insert-blank")
def pdf_insert_blank(
    source: FileArg,
    output: OutOpt,
    before: Annotated[
        int, typer.Option("--before", "-b", help="1-based page to insert before (0 = append).")
    ] = 0,
) -> None:
    """Insert a blank page (sized like its neighbour)."""
    from anydoc.formats.pdf.pages import insert_blank_page

    data = _read_pdf(source)
    try:
        n = _pdf_page_count(data)
        index = n if before == 0 else before - 1
        output.write_bytes(insert_blank_page(data, index))
    except (AnyDocError, ValueError, IndexError) as exc:
        raise _fail(str(exc)) from None
    typer.echo(str(output))


def main() -> None:
    """Console-script entry point."""
    app()
