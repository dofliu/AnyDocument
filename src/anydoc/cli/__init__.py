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


PagesOpt = Annotated[str, typer.Option("--pages", "-p", help='1-based pages, e.g. "1,3-5".')]
ColorOpt = Annotated[str, typer.Option("--color", help="Text color as #RRGGBB.")]


def _pdf_stamp(source: Path, output: Path, op: Any, pages: str, **options: Any) -> None:
    """Run a stamp function ``op(data, pages=..., **options)`` and write ``output``."""
    from anydoc.formats.pdf.pages import parse_page_spec

    data = _read_pdf(source)
    try:
        indexes = parse_page_spec(pages, _pdf_page_count(data))
        output.write_bytes(op(data, pages=indexes, **options))
    except (AnyDocError, ValueError, IndexError, OSError) as exc:
        raise _fail(str(exc)) from None
    typer.echo(str(output))


@pdf_app.command("watermark")
def pdf_watermark(
    source: FileArg,
    output: OutOpt,
    text: Annotated[str | None, typer.Option("--text", "-t", help="Watermark text.")] = None,
    image: Annotated[Path | None, typer.Option("--image", "-i", help="Watermark image.")] = None,
    pages: PagesOpt = "all",
    opacity: Annotated[float, typer.Option(help="0 (invisible) to 1 (opaque).")] = 0.3,
    angle: Annotated[float, typer.Option(help="Counter-clockwise degrees.")] = 45,
    size: Annotated[float, typer.Option(help="Font size (text watermarks).")] = 48,
    scale: Annotated[float, typer.Option(help="Image width / page width.")] = 0.5,
    color: ColorOpt = "#808080",
    tile: Annotated[bool, typer.Option(help="Repeat text over the page.")] = False,
    under: Annotated[bool, typer.Option(help="Place beneath page content.")] = False,
) -> None:
    """Add a text or image watermark."""
    from anydoc.formats.pdf.stamp import add_image_watermark, add_text_watermark

    if (text is None) == (image is None):
        raise _fail("give exactly one of --text or --image", 2)
    if text is not None:
        _pdf_stamp(
            source, output, add_text_watermark, pages, text=text, font_size=size, color=color,
            opacity=opacity, angle=angle, tile=tile, under=under,
        )  # fmt: skip
    else:
        assert image is not None
        _pdf_stamp(
            source, output, add_image_watermark, pages, image=_read_pdf(image), scale=scale,
            opacity=opacity, angle=0 if angle == 45 else angle, under=under,
        )  # fmt: skip


@pdf_app.command("header-footer")
def pdf_header_footer(
    source: FileArg,
    output: OutOpt,
    header: Annotated[str | None, typer.Option(help="Header text ({page}, {total}).")] = None,
    footer: Annotated[str | None, typer.Option(help="Footer text ({page}, {total}).")] = None,
    align: Annotated[str, typer.Option(help="left, center or right.")] = "center",
    pages: PagesOpt = "all",
    size: Annotated[float, typer.Option(help="Font size.")] = 10,
    color: ColorOpt = "#000000",
    start: Annotated[int, typer.Option(help="Number of the first page.")] = 1,
) -> None:
    """Add a header and/or footer."""
    from anydoc.formats.pdf.stamp import add_header_footer

    _pdf_stamp(
        source, output, add_header_footer, pages, header=header, footer=footer, align=align,
        font_size=size, color=color, start=start,
    )  # fmt: skip


@pdf_app.command("page-numbers")
def pdf_page_numbers(
    source: FileArg,
    output: OutOpt,
    fmt: Annotated[str, typer.Option("--format", "-f", help="Text with {page}/{total}.")] = (
        "{page} / {total}"
    ),
    position: Annotated[str, typer.Option(help="e.g. bottom-center, top-right.")] = "bottom-center",
    pages: PagesOpt = "all",
    start: Annotated[int, typer.Option(help="Number of the first page.")] = 1,
    size: Annotated[float, typer.Option(help="Font size.")] = 10,
) -> None:
    """Add page numbers."""
    from anydoc.formats.pdf.stamp import add_page_numbers

    _pdf_stamp(
        source, output, add_page_numbers, pages, fmt=fmt, position=position, start=start,
        font_size=size,
    )  # fmt: skip


def _pdf_secure(source: Path, output: Path, op: Any) -> None:
    data = _read_pdf(source)
    try:
        output.write_bytes(op(data))
    except (AnyDocError, ValueError, OSError) as exc:
        raise _fail(str(exc)) from None
    typer.echo(str(output))


@pdf_app.command("encrypt")
def pdf_encrypt(
    source: FileArg,
    output: OutOpt,
    user_password: Annotated[str, typer.Option("--user-password", help="Password to open.")] = "",
    owner_password: Annotated[
        str | None, typer.Option("--owner-password", help="Password with full rights.")
    ] = None,
    allow: Annotated[
        str | None,
        typer.Option(help="Comma list of allowed operations (print,modify,copy,annotate,...)."),
    ] = None,
    algorithm: Annotated[
        str, typer.Option(help="AES-256, AES-128, RC4-128 or RC4-40.")
    ] = "AES-256",
    password: Annotated[str, typer.Option(help="Password of an already encrypted input.")] = "",
) -> None:
    """Encrypt a PDF with passwords and optional permission restrictions."""
    from anydoc.formats.pdf.security import Permissions, encrypt_pdf

    def run(data: bytes) -> bytes:
        perms = (
            Permissions.only(*[a.strip() for a in allow.split(",") if a.strip()])
            if (allow is not None)
            else None
        )
        return encrypt_pdf(
            data, user_password, owner_password=owner_password, permissions=perms,
            algorithm=algorithm, source_password=password,
        )  # fmt: skip

    _pdf_secure(source, output, run)


@pdf_app.command("decrypt")
def pdf_decrypt(
    source: FileArg,
    output: OutOpt,
    password: Annotated[str, typer.Option(help="User or owner password.")] = "",
) -> None:
    """Remove the encryption from a PDF."""
    from anydoc.formats.pdf.security import decrypt_pdf

    _pdf_secure(source, output, lambda d: decrypt_pdf(d, password))


@pdf_app.command("permissions")
def pdf_permissions(
    source: FileArg,
    password: Annotated[str, typer.Option(help="User or owner password.")] = "",
) -> None:
    """Show encryption status and permissions as JSON."""
    from anydoc.formats.pdf.security import get_permissions, is_encrypted

    data = _read_pdf(source)
    try:
        _dump(
            {
                "encrypted": is_encrypted(data),
                "permissions": get_permissions(data, password).to_dict(),
            }
        )
    except (AnyDocError, ValueError) as exc:
        raise _fail(str(exc)) from None


@pdf_app.command("fields")
def pdf_fields(
    source: FileArg,
    password: Annotated[str, typer.Option(help="PDF password.")] = "",
) -> None:
    """List form fields of a PDF as JSON."""
    from anydoc.formats.pdf.forms import list_form_fields

    try:
        _dump([f.to_dict() for f in list_form_fields(_read_pdf(source), password)])
    except (AnyDocError, ValueError) as exc:
        raise _fail(str(exc)) from None


@pdf_app.command("fill")
def pdf_fill(
    source: FileArg,
    output: OutOpt,
    set_: Annotated[
        list[str] | None,
        typer.Option("--set", help="NAME=VALUE (repeatable); true/false for checkboxes."),
    ] = None,
    values: Annotated[
        Path | None, typer.Option("--values", help="JSON file mapping field names to values.")
    ] = None,
    flatten: Annotated[bool, typer.Option(help="Flatten the form after filling.")] = False,
    password: Annotated[str, typer.Option(help="PDF password.")] = "",
) -> None:
    """Fill form fields from --set options and/or a JSON file."""
    import json

    from anydoc.formats.pdf.forms import fill_form

    data = _read_pdf(source)
    vals: dict[str, Any] = {}
    try:
        if values is not None:
            vals.update(json.loads(values.read_text(encoding="utf-8")))
        for item in set_ or []:
            name, sep, val = item.partition("=")
            if not sep:
                raise ValueError(f"Expected NAME=VALUE, got {item!r}")
            vals[name] = {"true": True, "false": False}.get(val.lower(), val)
        output.write_bytes(fill_form(data, vals, flatten=flatten, password=password))
    except (AnyDocError, ValueError, KeyError, OSError) as exc:
        raise _fail(str(exc).strip("'\"")) from None
    typer.echo(str(output))


@pdf_app.command("flatten")
def pdf_flatten(source: FileArg, output: OutOpt) -> None:
    """Flatten form fields into static page content."""
    from anydoc.formats.pdf.forms import flatten_form

    _pdf_secure(source, output, flatten_form)


def _parse_rect(text: str) -> tuple[float, float, float, float]:
    parts = [float(v) for v in text.split(",")]
    if len(parts) != 4:
        raise ValueError(f"Rectangle must be x0,y0,x1,y1; got {text!r}")
    return (parts[0], parts[1], parts[2], parts[3])


@pdf_app.command("annots")
def pdf_annots(
    source: FileArg,
    password: Annotated[str, typer.Option(help="PDF password.")] = "",
) -> None:
    """List annotations of a PDF as JSON."""
    from anydoc.formats.pdf.annotations import list_annotations

    try:
        _dump([a.to_dict() for a in list_annotations(_read_pdf(source), password)])
    except (AnyDocError, ValueError) as exc:
        raise _fail(str(exc)) from None


@pdf_app.command("annotate")
def pdf_annotate(
    source: FileArg,
    output: OutOpt,
    kind: Annotated[str, typer.Option("--kind", help="highlight | note | box | rect | stamp")],
    page: Annotated[int, typer.Option(help="1-based page number.")] = 1,
    rect: Annotated[str, typer.Option(help="x0,y0,x1,y1 in PDF points.")] = "72,700,300,720",
    text: Annotated[str, typer.Option(help="Text / stamp label.")] = "",
    color: Annotated[str | None, typer.Option(help="Hex colour, e.g. FFFF00.")] = None,
) -> None:
    """Add a highlight, note, text box, rectangle or stamp annotation."""
    from anydoc.formats.pdf import annotations as ann

    data = _read_pdf(source)
    try:
        r = _parse_rect(rect)
        extra: dict[str, Any] = {} if color is None else {"color": color}
        p = page - 1
        if kind == "highlight":
            out = ann.add_highlight(data, p, r, contents=text, **extra)
        elif kind == "note":
            out = ann.add_text_annotation(data, p, text, point=(r[0], r[1]))
        elif kind == "box":
            out = ann.add_text_annotation(data, p, text, rect=r, **extra)
        elif kind == "rect":
            out = ann.add_rectangle(data, p, r, contents=text, **extra)
        elif kind == "stamp":
            out = ann.add_stamp(data, p, text or "DRAFT", r, **extra)
        else:
            raise ValueError(f"Unknown annotation kind: {kind}")
        output.write_bytes(out)
    except (AnyDocError, ValueError, IndexError, OSError) as exc:
        raise _fail(str(exc)) from None
    typer.echo(str(output))


@pdf_app.command("remove-annots")
def pdf_remove_annots(source: FileArg, output: OutOpt) -> None:
    """Remove all annotations (links and form fields are kept)."""
    from anydoc.formats.pdf.annotations import remove_annotations

    _pdf_secure(source, output, remove_annotations)


@pdf_app.command("bookmarks")
def pdf_bookmarks(
    source: FileArg,
    password: Annotated[str, typer.Option(help="PDF password.")] = "",
) -> None:
    """List bookmarks (outline) as JSON; pages are 1-based."""
    from anydoc.formats.pdf.bookmarks import list_bookmarks

    try:
        _dump(
            [
                {**b.to_dict(), "page": None if b.page is None else b.page + 1}
                for b in list_bookmarks(_read_pdf(source), password)
            ]
        )
    except (AnyDocError, ValueError) as exc:
        raise _fail(str(exc)) from None


@pdf_app.command("set-bookmarks")
def pdf_set_bookmarks(
    source: FileArg,
    output: OutOpt,
    spec: Annotated[
        Path,
        typer.Option(
            "--spec",
            help='JSON file: [{"title": "..", "page": 1, "level": 0}, ..] (1-based pages).',
            exists=True,
            dir_okay=False,
        ),
    ],
) -> None:
    """Replace the bookmarks with those described in a JSON file."""
    import json

    from anydoc.formats.pdf.bookmarks import set_bookmarks

    data = _read_pdf(source)
    try:
        items = json.loads(spec.read_text(encoding="utf-8"))
        marks = [
            {"title": i["title"], "page": int(i["page"]) - 1, "level": int(i.get("level", 0))}
            for i in items
        ]
        output.write_bytes(set_bookmarks(data, marks))
    except (AnyDocError, ValueError, IndexError, KeyError, TypeError, OSError) as exc:
        raise _fail(str(exc)) from None
    typer.echo(str(output))


@pdf_app.command("remove-bookmarks")
def pdf_remove_bookmarks(source: FileArg, output: OutOpt) -> None:
    """Remove all bookmarks."""
    from anydoc.formats.pdf.bookmarks import remove_bookmarks

    _pdf_secure(source, output, remove_bookmarks)


@pdf_app.command("tables")
def pdf_tables(
    source: FileArg,
    output: Annotated[
        Path | None,
        typer.Option(
            "--output", "-o", help="Write .xlsx (one sheet per table) or a folder of CSV files."
        ),
    ] = None,
    pages: Annotated[str, typer.Option(help='1-based page spec, e.g. "1,3-5" or "all".')] = "all",
    strategy: Annotated[
        str, typer.Option(help="lines (ruled tables) or text (aligned).")
    ] = "lines",
    password: Annotated[str, typer.Option(help="PDF password.")] = "",
) -> None:
    """Extract tables as JSON, or export them to XLSX / CSV files with --output."""
    from anydoc.converters.pdf_tables import pdf_tables_to_csv, pdf_tables_to_xlsx
    from anydoc.formats.pdf.pages import parse_page_spec
    from anydoc.formats.pdf.tables import extract_tables

    data = _read_pdf(source)
    try:
        idx = parse_page_spec(pages, _pdf_page_count(data))
        if output is None:
            _dump(
                [
                    {**t.to_dict(), "page": t.page + 1}
                    for t in extract_tables(data, idx, strategy=strategy, password=password)
                ]
            )
            return
        if output.suffix.lower() == ".xlsx":
            pdf_tables_to_xlsx(source, output, pages=idx, strategy=strategy, password=password)
            typer.echo(str(output))
        else:
            for p in pdf_tables_to_csv(
                source, output, pages=idx, strategy=strategy, password=password
            ):
                typer.echo(str(p))
    except (AnyDocError, ValueError, IndexError, OSError) as exc:
        raise _fail(str(exc)) from None


@pdf_app.command("ocr")
def pdf_ocr(
    source: FileArg,
    output: OutOpt,
    pages: Annotated[str, typer.Option(help='1-based page spec, e.g. "1,3-5" or "all".')] = "all",
    lang: Annotated[
        str, typer.Option(help="Tesseract languages, e.g. chi_tra+eng.")
    ] = "chi_tra+eng",
    dpi: Annotated[float, typer.Option(help="Render resolution for recognition.")] = 300.0,
    force: Annotated[bool, typer.Option(help="Also OCR pages that already have text.")] = False,
    password: Annotated[str, typer.Option(help="PDF password.")] = "",
) -> None:
    """Make a scanned PDF searchable with Tesseract OCR."""
    from anydoc.formats.pdf.ocr import ocr_pdf
    from anydoc.formats.pdf.pages import parse_page_spec

    data = _read_pdf(source)
    try:
        idx = parse_page_spec(pages, _pdf_page_count(data))
        output.write_bytes(
            ocr_pdf(data, idx, lang=lang, dpi=dpi, skip_text=not force, password=password)
        )
    except (AnyDocError, ValueError, IndexError, OSError) as exc:
        raise _fail(str(exc)) from None
    typer.echo(str(output))


@pdf_app.command("compress")
def pdf_compress(
    source: FileArg,
    output: OutOpt,
    quality: Annotated[int, typer.Option(help="JPEG quality for images (1-100).")] = 70,
    max_side: Annotated[
        int, typer.Option(help="Downscale images whose longest side exceeds this (0 = never).")
    ] = 1600,
    no_images: Annotated[bool, typer.Option(help="Do not touch images.")] = False,
    password: Annotated[str, typer.Option(help="PDF password.")] = "",
) -> None:
    """Shrink a PDF (image resampling, duplicate-object removal, stream compression)."""
    from anydoc.formats.pdf.compress import compress_pdf_with_stats

    data = _read_pdf(source)
    try:
        result = compress_pdf_with_stats(
            data,
            image_quality=quality,
            max_image_side=max_side or None,
            recompress_images=not no_images,
            password=password,
        )
        output.write_bytes(result.data)
    except (AnyDocError, ValueError, OSError) as exc:
        raise _fail(str(exc)) from None
    typer.echo(str(output))
    _dump(result.to_dict())


@pdf_app.command("redact")
def pdf_redact(
    source: FileArg,
    output: OutOpt,
    text: Annotated[
        list[str] | None, typer.Option("--text", "-t", help="Text to remove (repeatable).")
    ] = None,
    region: Annotated[
        list[str] | None,
        typer.Option(
            "--region", "-r", help="PAGE:x0,top,x1,bottom in points from top-left (1-based page)."
        ),
    ] = None,
    regex: Annotated[
        bool, typer.Option(help="Treat --text values as regular expressions.")
    ] = False,
    case_sensitive: Annotated[bool, typer.Option(help="Case-sensitive text search.")] = False,
    dpi: Annotated[float, typer.Option(help="Rasterisation resolution of redacted pages.")] = 200.0,
    password: Annotated[str, typer.Option(help="PDF password.")] = "",
) -> None:
    """Permanently redact text or regions (redacted pages become image-only)."""
    from anydoc.formats.pdf.redact import redact_pdf

    data = _read_pdf(source)
    try:
        regions: list[tuple[int, tuple[float, ...]]] = []
        for spec in region or []:
            page_s, _, coords = spec.partition(":")
            nums = tuple(float(v) for v in coords.split(","))
            if len(nums) != 4:
                raise ValueError(f"Invalid region {spec!r}; expected PAGE:x0,top,x1,bottom")
            regions.append((int(page_s) - 1, nums))
        if not text and not regions:
            raise ValueError("Nothing to redact: give --text and/or --region")
        result = redact_pdf(
            data,
            texts=text or [],
            regions=regions,
            regex=regex,
            case_sensitive=case_sensitive,
            dpi=dpi,
            password=password,
        )
        output.write_bytes(result.data)
    except (AnyDocError, ValueError, IndexError, OSError) as exc:
        raise _fail(str(exc)) from None
    typer.echo(str(output))
    _dump(result.to_dict())


def main() -> None:
    """Console-script entry point."""
    app()


@app.command("mailmerge")
def mailmerge(
    template: Annotated[Path, typer.Argument(help="DOCX template with {{field}} placeholders.")],
    data: Annotated[Path, typer.Argument(help="Data source (.csv, .tsv, .xlsx or .json).")],
    output: Annotated[
        Path,
        typer.Argument(help="Output: a directory (one DOCX per record), or .docx/.pdf (merged)."),
    ],
    name_field: Annotated[
        str | None, typer.Option("--name-field", "-n", help="Field used in per-record file names.")
    ] = None,
    missing: Annotated[
        str, typer.Option("--missing", help="Unknown placeholders: keep, blank or error.")
    ] = "keep",
) -> None:
    """Mail merge a DOCX template with CSV/XLSX/JSON records."""
    from anydoc.converters import mailmerge as mm

    if missing not in {"keep", "blank", "error"}:
        raise _fail(f"invalid --missing value: {missing}", 2)
    policy: Any = missing
    try:
        suffix = output.suffix.lower()
        if suffix == ".pdf":
            mm.merge_to_pdf(template, data, output, missing=policy)
            typer.echo(str(output))
        elif suffix == ".docx":
            mm.merge_to_single(template, data, output, missing=policy)
            typer.echo(str(output))
        else:
            for path in mm.merge_to_files(
                template, data, output, name_field=name_field, missing=policy
            ):
                typer.echo(str(path))
    except FileNotFoundError as exc:
        raise _fail(f"file not found: {exc.args[0]}", 2) from None
    except (AnyDocError, ValueError) as exc:
        raise _fail(str(exc)) from None
