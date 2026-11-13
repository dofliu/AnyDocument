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


pptx_app = typer.Typer(help="PowerPoint layouts, themes and outline builds.", no_args_is_help=True)
app.add_typer(pptx_app, name="pptx")


def _open_pptx(path: Path) -> Any:
    from anydoc.formats.pptx import PptxHandler

    if not path.is_file():
        raise _fail(f"file not found: {path}", 2)
    try:
        return PptxHandler().open(path)
    except AnyDocError as exc:
        raise _fail(str(exc)) from None


@pptx_app.command("build")
def pptx_build(
    outline: Annotated[Path, typer.Argument(help="Outline JSON file.")],
    output: Annotated[Path, typer.Argument(help="Output .pptx path.")],
    template: Annotated[
        Path | None,
        typer.Option("--template", "-t", help="PPTX template (masters, layouts, theme)."),
    ] = None,
) -> None:
    """Build a presentation from an outline JSON, optionally using a template."""
    from anydoc.formats.pptx import outline as _outline

    for path in (outline, template):
        if path is not None and not path.is_file():
            raise _fail(f"file not found: {path}", 2)
    try:
        _outline.outline_to_pptx(outline, output, template=template)
    except (AnyDocError, ValueError, IndexError, OSError) as exc:
        raise _fail(str(exc)) from None
    typer.echo(str(output))


@pptx_app.command("outline")
def pptx_outline(
    file: FileArg,
    output: Annotated[
        Path | None, typer.Option("--output", "-o", help="Write the JSON to this file.")
    ] = None,
) -> None:
    """Export a presentation as an outline JSON (titles, bullets, tables, charts, notes)."""
    data = _open_pptx(file).to_outline()
    if output is None:
        _dump(data)
    else:
        output.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        typer.echo(str(output))


@pptx_app.command("layouts")
def pptx_layouts(file: FileArg) -> None:
    """List slide layouts with their placeholders and the slides using them (JSON)."""
    from dataclasses import asdict

    _dump([asdict(item) for item in _open_pptx(file).layout_info()])


@pptx_app.command("theme")
def pptx_theme(
    file: FileArg,
    apply: Annotated[
        Path | None, typer.Option("--apply", help="Copy the theme of this presentation.")
    ] = None,
    color: Annotated[
        list[str] | None,
        typer.Option("--color", "-c", help="Set a theme colour, e.g. accent1=1F4E79."),
    ] = None,
    major_font: Annotated[str | None, typer.Option("--major-font", help="Heading font.")] = None,
    minor_font: Annotated[str | None, typer.Option("--minor-font", help="Body font.")] = None,
    output: Annotated[
        Path | None, typer.Option("--output", "-o", help="Output path (required to change).")
    ] = None,
) -> None:
    """Show the theme (JSON), or change it and write the result to --output."""
    from dataclasses import asdict

    doc = _open_pptx(file)
    changing = bool(apply or color or major_font or minor_font)
    if changing and output is None:
        raise _fail("--output is required when changing the theme", 2)
    try:
        if apply is not None:
            doc.apply_theme(apply)
        if color:
            pairs = [item.partition("=") for item in color]
            if any(not sep or not value for _, sep, value in pairs):
                raise ValueError("--color expects NAME=RRGGBB")
            doc.set_theme_colors({name.strip(): value.strip() for name, _, value in pairs})
        if major_font or minor_font:
            doc.set_theme_fonts(major=major_font, minor=minor_font)
        if changing and output is not None:
            doc.save(output)
    except (AnyDocError, ValueError, OSError) as exc:
        raise _fail(str(exc)) from None
    _dump(asdict(doc.theme()))


csv_app = typer.Typer(
    help="CSV/TSV data operations: filter, sort, dedupe, select, join, group, stats.",
    no_args_is_help=True,
)
app.add_typer(csv_app, name="csv")

CsvOutput = Annotated[
    Path | None,
    typer.Option("--output", "-o", help="Write the result to this file (default: stdout)."),
]
CsvEncoding = Annotated[
    str | None,
    typer.Option("--encoding", help="Output encoding (default: same as the input file)."),
]
_CSV_ERRORS = (AnyDocError, ValueError, LookupError, OSError)


def _open_csv(path: Path) -> Any:
    from anydoc.formats.csv import CsvHandler, TsvHandler

    if not path.is_file():
        raise _fail(f"file not found: {path}", 2)
    handler = TsvHandler() if path.suffix.lower() in (".tsv", ".tab") else CsvHandler()
    try:
        return handler.open(path)
    except AnyDocError as exc:
        raise _fail(str(exc)) from None


def _emit_csv(doc: Any, output: Path | None, encoding: str | None) -> None:
    """Write ``doc`` to ``output`` (delimiter follows the extension) or print it as UTF-8."""
    if output is None:
        typer.echo(doc.to_bytes(encoding="utf-8").decode("utf-8"), nl=False)
        return
    suffix = output.suffix.lower()
    delimiter = None
    if suffix in (".tsv", ".tab"):
        delimiter = "\t"
    elif suffix == ".csv" and doc.delimiter == "\t":
        delimiter = ","
    doc.save(output, encoding=encoding, delimiter=delimiter)
    typer.echo(str(output))


def _pairs(items: list[str], option: str) -> dict[str, str]:
    pairs = [item.partition("=") for item in items]
    if any(not sep or not old.strip() or not new.strip() for old, sep, new in pairs):
        raise ValueError(f"{option} expects OLD=NEW")
    return {old.strip(): new.strip() for old, _, new in pairs}


@csv_app.command("filter")
def csv_filter(
    file: FileArg,
    where: Annotated[
        list[str],
        typer.Option("--where", "-w", help="Condition, e.g. '分數>=80', 'name~wang', 'city=台北'."),
    ],
    any_: Annotated[bool, typer.Option("--any", help="Match any condition (OR).")] = False,
    invert: Annotated[bool, typer.Option("--invert", help="Keep non-matching rows.")] = False,
    output: CsvOutput = None,
    encoding: CsvEncoding = None,
) -> None:
    """Keep the rows matching the conditions (operators: = != > >= < <= ~ !~ =~)."""
    doc = _open_csv(file)
    try:
        removed = doc.filter_rows(list(where), match="any" if any_ else "all", invert=invert)
        typer.echo(f"kept {len(doc.rows)} rows, removed {removed}", err=True)
        _emit_csv(doc, output, encoding)
    except _CSV_ERRORS as exc:
        raise _fail(str(exc)) from None


@csv_app.command("sort")
def csv_sort(
    file: FileArg,
    by: Annotated[
        list[str], typer.Option("--by", "-k", help="Sort column; append :desc to reverse.")
    ],
    descending: Annotated[bool, typer.Option("--desc", help="Descending by default.")] = False,
    ignore_case: Annotated[bool, typer.Option("--ignore-case", help="Ignore letter case.")] = False,
    output: CsvOutput = None,
    encoding: CsvEncoding = None,
) -> None:
    """Sort rows by one or more columns (numbers and dates by value, blanks last)."""
    doc = _open_csv(file)
    try:
        doc.sort_rows(list(by), descending=descending, ignore_case=ignore_case)
        _emit_csv(doc, output, encoding)
    except _CSV_ERRORS as exc:
        raise _fail(str(exc)) from None


@csv_app.command("dedupe")
def csv_dedupe(
    file: FileArg,
    column: Annotated[
        list[str] | None,
        typer.Option("--column", "-c", help="Compare only these columns (default: all)."),
    ] = None,
    keep: Annotated[str, typer.Option("--keep", help="Occurrence to keep: first or last.")] = (
        "first"
    ),
    ignore_case: Annotated[bool, typer.Option("--ignore-case", help="Ignore letter case.")] = False,
    output: CsvOutput = None,
    encoding: CsvEncoding = None,
) -> None:
    """Remove duplicate rows."""
    doc = _open_csv(file)
    try:
        removed = doc.dedupe(column or None, keep=keep, ignore_case=ignore_case)
        typer.echo(f"kept {len(doc.rows)} rows, removed {removed}", err=True)
        _emit_csv(doc, output, encoding)
    except _CSV_ERRORS as exc:
        raise _fail(str(exc)) from None


@csv_app.command("select")
def csv_select(
    file: FileArg,
    column: Annotated[
        list[str] | None,
        typer.Option("--column", "-c", help="Column to keep (repeat; sets the order)."),
    ] = None,
    drop: Annotated[
        list[str] | None, typer.Option("--drop", "-d", help="Column to remove (repeat).")
    ] = None,
    rename: Annotated[
        list[str] | None, typer.Option("--rename", "-r", help="Rename a column: OLD=NEW.")
    ] = None,
    output: CsvOutput = None,
    encoding: CsvEncoding = None,
) -> None:
    """Select, drop and/or rename columns (applied in that order)."""
    if not (column or drop or rename):
        raise _fail("give at least one of --column, --drop or --rename", 2)
    doc = _open_csv(file)
    try:
        if column:
            doc.select_columns(list(column))
        if drop:
            doc.drop_columns(list(drop))
        if rename:
            doc.rename_columns(_pairs(list(rename), "--rename"))
        _emit_csv(doc, output, encoding)
    except _CSV_ERRORS as exc:
        raise _fail(str(exc)) from None


@csv_app.command("join")
def csv_join(
    left: Annotated[Path, typer.Argument(help="Left table.")],
    right: Annotated[Path, typer.Argument(help="Right table.")],
    on: Annotated[list[str], typer.Option("--on", help="Key column (repeat for more).")],
    right_on: Annotated[
        list[str] | None,
        typer.Option("--right-on", help="Key column in the right table, if named differently."),
    ] = None,
    how: Annotated[str, typer.Option("--how", help="inner, left, right or outer.")] = "inner",
    suffix: Annotated[
        str, typer.Option("--suffix", help="Suffix for clashing right column names.")
    ] = "_right",
    output: CsvOutput = None,
    encoding: CsvEncoding = None,
) -> None:
    """Join two tables on key column(s)."""
    left_doc, right_doc = _open_csv(left), _open_csv(right)
    try:
        result = left_doc.join(
            right_doc,
            list(on),
            right_on=list(right_on) if right_on else None,
            how=how,
            suffix=suffix,
        )
        _emit_csv(result, output, encoding)
    except _CSV_ERRORS as exc:
        raise _fail(str(exc)) from None


@csv_app.command("group")
def csv_group(
    file: FileArg,
    agg: Annotated[
        list[str],
        typer.Option(
            "--agg", "-a", help="Aggregate: count, sum:COL, mean:COL, NAME=max:COL, ... (repeat)."
        ),
    ],
    by: Annotated[
        list[str] | None, typer.Option("--by", "-b", help="Group column (repeat; omit for totals).")
    ] = None,
    no_sort: Annotated[
        bool, typer.Option("--no-sort", help="Keep groups in first-seen order.")
    ] = False,
    output: CsvOutput = None,
    encoding: CsvEncoding = None,
) -> None:
    """Group rows and compute statistics (count/sum/mean/min/max/median/first/last/nunique)."""
    doc = _open_csv(file)
    try:
        result = doc.group_by(list(by or []), list(agg), sort=not no_sort)
        _emit_csv(result, output, encoding)
    except _CSV_ERRORS as exc:
        raise _fail(str(exc)) from None


@csv_app.command("stats")
def csv_stats(
    file: FileArg,
    column: Annotated[
        list[str] | None,
        typer.Option("--column", "-c", help="Only these columns (default: all)."),
    ] = None,
) -> None:
    """Show per-column statistics as JSON (type, count, empty, unique, min, max, sum, mean)."""
    doc = _open_csv(file)
    try:
        _dump(doc.describe(column or None))
    except _CSV_ERRORS as exc:
        raise _fail(str(exc)) from None


dxf_app = typer.Typer(
    help="DXF layer management, entity editing, blocks, dimensions and measurement.",
    no_args_is_help=True,
)
app.add_typer(dxf_app, name="dxf")

DxfOutput = Annotated[Path, typer.Option("--output", "-o", help="Output DXF path.")]
LayerArg = Annotated[str, typer.Argument(help="Layer name (case-insensitive).")]
_DXF_ERRORS = (AnyDocError, ValueError, LookupError, OSError)


def _open_dxf(path: Path) -> Any:
    from anydoc.formats.dxf import DxfHandler

    if not path.is_file():
        raise _fail(f"file not found: {path}", 2)
    try:
        return DxfHandler().open(path)
    except AnyDocError as exc:
        raise _fail(str(exc)) from None


def _dxf_error(exc: BaseException) -> typer.Exit:
    # KeyError quotes its message; show the plain text instead.
    return _fail(str(exc.args[0]) if isinstance(exc, KeyError) and exc.args else str(exc))


def _layer_dict(info: Any, usage: dict[str, int], current: str) -> dict[str, Any]:
    return {
        "name": info.name,
        "color": info.color,
        "true_color": info.true_color,
        "linetype": info.linetype,
        "on": info.on,
        "frozen": info.frozen,
        "locked": info.locked,
        "current": info.name.lower() == current.lower(),
        "entities": usage.get(info.name, 0),
    }


@dxf_app.command("layers")
def dxf_layers(file: FileArg) -> None:
    """List layers as JSON (colour, linetype, on/frozen/locked, current, entity count)."""
    doc = _open_dxf(file)
    usage = doc.layer_usage()
    _dump([_layer_dict(info, usage, doc.current_layer) for info in doc.layers()])


@dxf_app.command("layer-add")
def dxf_layer_add(
    file: FileArg,
    name: LayerArg,
    output: DxfOutput,
    color: Annotated[
        str, typer.Option("--color", "-c", help="ACI index 1-255, colour name or #RRGGBB.")
    ] = "7",
    linetype: Annotated[
        str, typer.Option("--linetype", "-l", help="Linetype name, e.g. DASHED.")
    ] = "Continuous",
    off: Annotated[bool, typer.Option("--off", help="Create the layer switched off.")] = False,
    frozen: Annotated[bool, typer.Option("--frozen", help="Create the layer frozen.")] = False,
    locked: Annotated[bool, typer.Option("--locked", help="Create the layer locked.")] = False,
) -> None:
    """Add a layer."""
    doc = _open_dxf(file)
    try:
        doc.add_layer(
            name, color=color, linetype=linetype, on=not off, frozen=frozen, locked=locked
        )
        typer.echo(str(doc.save(output)))
    except _DXF_ERRORS as exc:
        raise _dxf_error(exc) from None


@dxf_app.command("layer-set")
def dxf_layer_set(
    file: FileArg,
    name: LayerArg,
    output: DxfOutput,
    color: Annotated[
        str | None, typer.Option("--color", "-c", help="ACI index 1-255, colour name or #RRGGBB.")
    ] = None,
    linetype: Annotated[
        str | None, typer.Option("--linetype", "-l", help="Linetype name, e.g. DASHED.")
    ] = None,
    on: Annotated[
        bool | None, typer.Option("--on/--off", help="Switch the layer on or off.")
    ] = None,
    frozen: Annotated[
        bool | None, typer.Option("--freeze/--thaw", help="Freeze or thaw the layer.")
    ] = None,
    locked: Annotated[
        bool | None, typer.Option("--lock/--unlock", help="Lock or unlock the layer.")
    ] = None,
    current: Annotated[
        bool, typer.Option("--current", help="Make this the current layer.")
    ] = False,
) -> None:
    """Change layer colour, linetype and on/frozen/locked state."""
    doc = _open_dxf(file)
    try:
        if current:
            if frozen:
                raise ValueError("Cannot freeze the current layer")
            doc.set_layer(name, frozen=False if doc.layer(name).frozen else None)
            doc.set_current_layer(name)
        doc.set_layer(name, color=color, linetype=linetype, on=on, frozen=frozen, locked=locked)
        typer.echo(str(doc.save(output)))
    except _DXF_ERRORS as exc:
        raise _dxf_error(exc) from None


@dxf_app.command("layer-rename")
def dxf_layer_rename(
    file: FileArg,
    name: LayerArg,
    new_name: Annotated[str, typer.Argument(help="New layer name.")],
    output: DxfOutput,
) -> None:
    """Rename a layer and update the entities on it."""
    doc = _open_dxf(file)
    try:
        doc.rename_layer(name, new_name)
        typer.echo(str(doc.save(output)))
    except _DXF_ERRORS as exc:
        raise _dxf_error(exc) from None


@dxf_app.command("layer-delete")
def dxf_layer_delete(
    file: FileArg,
    name: LayerArg,
    output: DxfOutput,
    move_to: Annotated[
        str | None, typer.Option("--move-to", help="Move the layer's entities to this layer.")
    ] = None,
    delete_entities: Annotated[
        bool, typer.Option("--delete-entities", help="Delete the layer's entities too.")
    ] = False,
) -> None:
    """Delete a layer (in-use layers need --move-to or --delete-entities)."""
    doc = _open_dxf(file)
    try:
        count = doc.delete_layer(name, move_to=move_to, delete_entities=delete_entities)
        verb = "moved" if move_to is not None else "deleted"
        typer.echo(f"{verb} {count} entities", err=True)
        typer.echo(str(doc.save(output)))
    except _DXF_ERRORS as exc:
        raise _dxf_error(exc) from None


@dxf_app.command("layer-purge")
def dxf_layer_purge(file: FileArg, output: DxfOutput) -> None:
    """Delete every unused layer."""
    doc = _open_dxf(file)
    try:
        removed = doc.purge_layers()
        typer.echo(f"removed {len(removed)} layers: {', '.join(removed)}", err=True)
        typer.echo(str(doc.save(output)))
    except _DXF_ERRORS as exc:
        raise _dxf_error(exc) from None


# -- DXF entity editing ---------------------------------------------------------

DxfLayout = Annotated[str, typer.Option("--layout", help="Layout name.")]
DxfHandles = Annotated[
    list[str] | None, typer.Argument(help="Entity handles (see `anydoc dxf entities`).")
]
DxfSelLayer = Annotated[
    str | None, typer.Option("--layer", "-l", help="Select the entities on this layer.")
]
DxfSelType = Annotated[
    list[str] | None, typer.Option("--type", "-t", help="Select this DXF type (repeatable).")
]
DxfSelAll = Annotated[bool, typer.Option("--all", help="Select every entity of the layout.")]
DxfBase = Annotated[str, typer.Option("--base", "-b", help="Base point `x,y`.")]
DxfInBlock = Annotated[
    str | None, typer.Option("--block", help="Work in this block definition, not a layout.")
]
DxfPoints = Annotated[
    list[str] | None,
    typer.Option("--point", "-p", help="Point `x,y[,z]` (polyline: `x,y[,bulge]`); repeatable."),
]


def _dxf_point(text: str) -> tuple[float, ...]:
    try:
        values = tuple(float(part) for part in text.split(","))
    except ValueError:
        values = ()
    if len(values) not in (2, 3):
        raise ValueError(f"Invalid point (expected x,y or x,y,z): {text!r}")
    return values


def _dxf_selection(
    doc: Any,
    handles: list[str] | None,
    layout: str,
    layer: str | None,
    types: list[str] | None,
    select_all: bool,
) -> list[str]:
    """Resolve handles given explicitly or selected by --layer/--type/--all."""
    if handles:
        if layer is not None or types or select_all:
            raise ValueError("Give either handles or --layer/--type/--all, not both")
        return handles
    if layer is None and not types and not select_all:
        raise ValueError("No entities selected: give handles or --layer/--type/--all")
    selected: list[str] = doc.select(layout, layer=layer, types=types or None)
    if not selected:
        raise ValueError("No entities match the selection")
    return selected


@dxf_app.command("entities")
def dxf_entities(
    file: FileArg,
    layout: DxfLayout = "Model",
    layer: Annotated[str | None, typer.Option("--layer", "-l", help="Only this layer.")] = None,
    types: Annotated[
        list[str] | None, typer.Option("--type", "-t", help="Only this DXF type (repeatable).")
    ] = None,
    block: DxfInBlock = None,
) -> None:
    """List the entities of a layout (or block) as JSON (handle, type, layer, geometry)."""
    doc = _open_dxf(file)
    try:
        details = doc.entity_details(layout, layer=layer, types=types or None, block=block)
        _dump([e.to_dict() for e in details])
    except _DXF_ERRORS as exc:
        raise _dxf_error(exc) from None


@dxf_app.command("entity-add")
def dxf_entity_add(
    file: FileArg,
    kind: Annotated[
        str, typer.Argument(help="Entity type: line, circle, arc, polyline, text or mtext.")
    ],
    output: DxfOutput,
    point: DxfPoints = None,
    radius: Annotated[float | None, typer.Option("--radius", "-r", help="Radius.")] = None,
    start_angle: Annotated[
        float | None, typer.Option("--start-angle", help="Arc start angle in degrees.")
    ] = None,
    end_angle: Annotated[
        float | None, typer.Option("--end-angle", help="Arc end angle in degrees.")
    ] = None,
    closed: Annotated[bool, typer.Option("--closed", help="Close the polyline.")] = False,
    text: Annotated[str | None, typer.Option("--text", help="Text content.")] = None,
    height: Annotated[float, typer.Option("--height", help="Text height.")] = 2.5,
    rotation: Annotated[float, typer.Option("--rotation", help="Text rotation (degrees).")] = 0.0,
    width: Annotated[
        float | None, typer.Option("--width", help="MTEXT column width for wrapping.")
    ] = None,
    layer: Annotated[
        str | None, typer.Option("--layer", "-l", help="Layer (default: current layer).")
    ] = None,
    color: Annotated[
        str | None,
        typer.Option("--color", "-c", help="ACI index, colour name, #RRGGBB, bylayer, byblock."),
    ] = None,
    linetype: Annotated[str | None, typer.Option("--linetype", help="Linetype name.")] = None,
    layout: DxfLayout = "Model",
    block: DxfInBlock = None,
) -> None:
    """Add a LINE, CIRCLE, ARC, LWPOLYLINE, TEXT or MTEXT; prints the new handle on stderr.

    Points are given with repeated --point: line needs 2, circle/arc 1 (the centre),
    polyline 2 or more, text/mtext 1 (the insertion point). --block adds the entity
    to a block definition instead of a layout.
    """
    doc = _open_dxf(file)
    common: dict[str, Any] = {
        "layout": layout,
        "block": block,
        "layer": layer,
        "color": color,
        "linetype": linetype,
    }
    try:
        points = [_dxf_point(p) for p in point or []]
        name = kind.strip().lower()

        def need(count: int, *, at_least: bool = False) -> None:
            if len(points) < count or (not at_least and len(points) != count):
                more = " or more" if at_least else ""
                raise ValueError(f"{name} needs {count}{more} --point, got {len(points)}")

        def required(value: Any, option: str) -> Any:
            if value is None:
                raise ValueError(f"{name} needs {option}")
            return value

        if name == "line":
            need(2)
            handle = doc.add_line(points[0], points[1], **common)
        elif name == "circle":
            need(1)
            handle = doc.add_circle(points[0], required(radius, "--radius"), **common)
        elif name == "arc":
            need(1)
            handle = doc.add_arc(
                points[0],
                required(radius, "--radius"),
                required(start_angle, "--start-angle"),
                required(end_angle, "--end-angle"),
                **common,
            )
        elif name in ("polyline", "lwpolyline"):
            need(2, at_least=True)
            handle = doc.add_polyline(points, closed=closed, **common)
        elif name == "text":
            need(1)
            handle = doc.add_text(
                required(text, "--text"), points[0], height=height, rotation=rotation, **common
            )
        elif name == "mtext":
            need(1)
            handle = doc.add_mtext(
                required(text, "--text").replace("\\n", "\n"),
                points[0],
                height=height,
                rotation=rotation,
                width=width,
                **common,
            )
        else:
            raise ValueError(f"Unknown entity type: {kind}")
        typer.echo(f"added {handle}", err=True)
        typer.echo(str(doc.save(output)))
    except _DXF_ERRORS as exc:
        raise _dxf_error(exc) from None


@dxf_app.command("entity-set")
def dxf_entity_set(
    file: FileArg,
    handle: Annotated[str, typer.Argument(help="Entity handle.")],
    output: DxfOutput,
    layer: Annotated[str | None, typer.Option("--layer", "-l", help="Move to this layer.")] = None,
    color: Annotated[
        str | None,
        typer.Option("--color", "-c", help="ACI index, colour name, #RRGGBB, bylayer, byblock."),
    ] = None,
    linetype: Annotated[str | None, typer.Option("--linetype", help="Linetype name.")] = None,
    start: Annotated[str | None, typer.Option("--start", help="LINE start `x,y`.")] = None,
    end: Annotated[str | None, typer.Option("--end", help="LINE end `x,y`.")] = None,
    center: Annotated[str | None, typer.Option("--center", help="CIRCLE/ARC centre.")] = None,
    radius: Annotated[float | None, typer.Option("--radius", "-r", help="Radius.")] = None,
    start_angle: Annotated[
        float | None, typer.Option("--start-angle", help="Arc start angle in degrees.")
    ] = None,
    end_angle: Annotated[
        float | None, typer.Option("--end-angle", help="Arc end angle in degrees.")
    ] = None,
    point: DxfPoints = None,
    closed: Annotated[
        bool | None, typer.Option("--closed/--open", help="Close or open the polyline.")
    ] = None,
    text: Annotated[str | None, typer.Option("--text", help="Text content.")] = None,
    insert: Annotated[str | None, typer.Option("--insert", help="Text insertion point.")] = None,
    height: Annotated[float | None, typer.Option("--height", help="Text height.")] = None,
    rotation: Annotated[
        float | None, typer.Option("--rotation", help="Text rotation (degrees).")
    ] = None,
    width: Annotated[float | None, typer.Option("--width", help="MTEXT column width.")] = None,
) -> None:
    """Change the properties of one entity (--point replaces all polyline vertices)."""
    doc = _open_dxf(file)

    def pt(value: str | None) -> tuple[float, ...] | None:
        return None if value is None else _dxf_point(value)

    try:
        is_mtext = doc.entity(handle).type == "MTEXT"
        doc.modify_entity(
            handle,
            layer=layer,
            color=color,
            linetype=linetype,
            start=pt(start),
            end=pt(end),
            center=pt(center),
            radius=radius,
            start_angle=start_angle,
            end_angle=end_angle,
            points=[_dxf_point(p) for p in point] if point else None,
            closed=closed,
            text=text.replace("\\n", "\n") if text is not None and is_mtext else text,
            insert=pt(insert),
            height=height,
            rotation=rotation,
            width=width,
        )
        typer.echo(str(doc.save(output)))
    except _DXF_ERRORS as exc:
        raise _dxf_error(exc) from None


@dxf_app.command("entity-delete")
def dxf_entity_delete(
    file: FileArg,
    output: DxfOutput,
    handles: DxfHandles = None,
    layer: DxfSelLayer = None,
    types: DxfSelType = None,
    select_all: DxfSelAll = False,
    layout: DxfLayout = "Model",
) -> None:
    """Delete entities given by handle or selected with --layer/--type/--all."""
    doc = _open_dxf(file)
    try:
        selected = _dxf_selection(doc, handles, layout, layer, types, select_all)
        typer.echo(f"deleted {doc.delete_entities(selected)} entities", err=True)
        typer.echo(str(doc.save(output)))
    except _DXF_ERRORS as exc:
        raise _dxf_error(exc) from None


@dxf_app.command("entity-move")
def dxf_entity_move(
    file: FileArg,
    output: DxfOutput,
    handles: DxfHandles = None,
    dx: Annotated[float, typer.Option("--dx", help="Offset along X.")] = 0.0,
    dy: Annotated[float, typer.Option("--dy", help="Offset along Y.")] = 0.0,
    layer: DxfSelLayer = None,
    types: DxfSelType = None,
    select_all: DxfSelAll = False,
    layout: DxfLayout = "Model",
) -> None:
    """Move entities by (--dx, --dy)."""
    doc = _open_dxf(file)
    try:
        selected = _dxf_selection(doc, handles, layout, layer, types, select_all)
        typer.echo(f"moved {len(doc.move_entities(selected, dx, dy))} entities", err=True)
        typer.echo(str(doc.save(output)))
    except _DXF_ERRORS as exc:
        raise _dxf_error(exc) from None


@dxf_app.command("entity-copy")
def dxf_entity_copy(
    file: FileArg,
    output: DxfOutput,
    handles: DxfHandles = None,
    dx: Annotated[float, typer.Option("--dx", help="Offset of the copies along X.")] = 0.0,
    dy: Annotated[float, typer.Option("--dy", help="Offset of the copies along Y.")] = 0.0,
    to_layer: Annotated[
        str | None, typer.Option("--to-layer", help="Put the copies on this layer.")
    ] = None,
    layer: DxfSelLayer = None,
    types: DxfSelType = None,
    select_all: DxfSelAll = False,
    layout: DxfLayout = "Model",
) -> None:
    """Copy entities, offset by (--dx, --dy); prints the new handles on stderr."""
    doc = _open_dxf(file)
    try:
        selected = _dxf_selection(doc, handles, layout, layer, types, select_all)
        copies = doc.copy_entities(selected, dx, dy, layer=to_layer)
        typer.echo(f"copied {len(copies)} entities: {' '.join(copies)}", err=True)
        typer.echo(str(doc.save(output)))
    except _DXF_ERRORS as exc:
        raise _dxf_error(exc) from None


@dxf_app.command("entity-rotate")
def dxf_entity_rotate(
    file: FileArg,
    output: DxfOutput,
    angle: Annotated[float, typer.Option("--angle", "-a", help="Angle in degrees (CCW).")],
    handles: DxfHandles = None,
    base: DxfBase = "0,0",
    layer: DxfSelLayer = None,
    types: DxfSelType = None,
    select_all: DxfSelAll = False,
    layout: DxfLayout = "Model",
) -> None:
    """Rotate entities counter-clockwise around --base."""
    doc = _open_dxf(file)
    try:
        selected = _dxf_selection(doc, handles, layout, layer, types, select_all)
        count = len(doc.rotate_entities(selected, angle, _dxf_point(base)))
        typer.echo(f"rotated {count} entities", err=True)
        typer.echo(str(doc.save(output)))
    except _DXF_ERRORS as exc:
        raise _dxf_error(exc) from None


@dxf_app.command("entity-scale")
def dxf_entity_scale(
    file: FileArg,
    output: DxfOutput,
    factor: Annotated[float, typer.Option("--factor", "-f", help="Uniform scale factor (> 0).")],
    handles: DxfHandles = None,
    base: DxfBase = "0,0",
    layer: DxfSelLayer = None,
    types: DxfSelType = None,
    select_all: DxfSelAll = False,
    layout: DxfLayout = "Model",
) -> None:
    """Scale entities uniformly relative to --base."""
    doc = _open_dxf(file)
    try:
        selected = _dxf_selection(doc, handles, layout, layer, types, select_all)
        count = len(doc.scale_entities(selected, factor, _dxf_point(base)))
        typer.echo(f"scaled {count} entities", err=True)
        typer.echo(str(doc.save(output)))
    except _DXF_ERRORS as exc:
        raise _dxf_error(exc) from None


# -- DXF blocks -------------------------------------------------------------------

BlockArg = Annotated[str, typer.Argument(help="Block name (case-insensitive).")]
DxfAttrs = Annotated[
    list[str] | None,
    typer.Option("--attr", "-a", help="Attribute value as `TAG=VALUE`; repeatable."),
]


def _dxf_attrs(items: list[str] | None) -> dict[str, str]:
    values: dict[str, str] = {}
    for item in items or []:
        tag, sep, value = item.partition("=")
        if not sep or not tag.strip():
            raise ValueError(f"Invalid attribute (expected TAG=VALUE): {item!r}")
        values[tag.strip()] = value
    return values


def _dxf_scale(text: str) -> float | tuple[float, ...]:
    try:
        values = tuple(float(part) for part in text.split(","))
    except ValueError:
        values = ()
    if len(values) not in (1, 2, 3):
        raise ValueError(f"Invalid scale (expected s or sx,sy[,sz]): {text!r}")
    return values[0] if len(values) == 1 else values


@dxf_app.command("blocks")
def dxf_blocks(file: FileArg) -> None:
    """List the block definitions as JSON (base point, entities, attributes, references)."""
    _dump([block.to_dict() for block in _open_dxf(file).block_details()])


@dxf_app.command("block-define")
def dxf_block_define(
    file: FileArg,
    name: Annotated[str, typer.Argument(help="Name of the new block.")],
    output: DxfOutput,
    handles: DxfHandles = None,
    base: DxfBase = "0,0",
    keep: Annotated[
        bool, typer.Option("--keep", help="Copy the entities instead of moving them.")
    ] = False,
    replace: Annotated[
        bool, typer.Option("--replace", help="Insert a block reference in their place.")
    ] = False,
    description: Annotated[str, typer.Option("--description", help="Block description.")] = "",
    layer: DxfSelLayer = None,
    types: DxfSelType = None,
    select_all: DxfSelAll = False,
    layout: DxfLayout = "Model",
) -> None:
    """Create a block, empty or from entities (handles or --layer/--type/--all)."""
    doc = _open_dxf(file)
    try:
        selected: list[str] | None = None
        if handles or layer is not None or types or select_all:
            selected = _dxf_selection(doc, handles, layout, layer, types, select_all)
        block = doc.define_block(
            name,
            base_point=_dxf_point(base),
            handles=selected,
            keep=keep,
            replace=replace,
            description=description,
        )
        typer.echo(f"defined {block.name} ({block.entity_count} entities)", err=True)
        typer.echo(str(doc.save(output)))
    except _DXF_ERRORS as exc:
        raise _dxf_error(exc) from None


@dxf_app.command("block-attdef")
def dxf_block_attdef(
    file: FileArg,
    name: BlockArg,
    tag: Annotated[str, typer.Argument(help="Attribute tag (no whitespace).")],
    output: DxfOutput,
    at: Annotated[str, typer.Option("--at", help="Position `x,y` in the block.")] = "0,0",
    default: Annotated[str, typer.Option("--default", help="Default value.")] = "",
    prompt: Annotated[str, typer.Option("--prompt", help="Prompt text.")] = "",
    height: Annotated[float, typer.Option("--height", help="Text height.")] = 2.5,
    rotation: Annotated[float, typer.Option("--rotation", help="Text rotation (degrees).")] = 0.0,
    invisible: Annotated[
        bool, typer.Option("--invisible", help="Do not display the attribute.")
    ] = False,
    constant: Annotated[
        bool, typer.Option("--constant", help="Fixed value (not editable per reference).")
    ] = False,
) -> None:
    """Add an attribute definition to a block; prints the new handle on stderr."""
    doc = _open_dxf(file)
    try:
        handle = doc.add_attdef(
            name,
            tag,
            _dxf_point(at),
            default=default,
            prompt=prompt,
            height=height,
            rotation=rotation,
            invisible=invisible,
            constant=constant,
        )
        typer.echo(f"added {handle}", err=True)
        typer.echo(str(doc.save(output)))
    except _DXF_ERRORS as exc:
        raise _dxf_error(exc) from None


@dxf_app.command("block-rename")
def dxf_block_rename(
    file: FileArg,
    name: BlockArg,
    new_name: Annotated[str, typer.Argument(help="New block name.")],
    output: DxfOutput,
) -> None:
    """Rename a block and update the references to it."""
    doc = _open_dxf(file)
    try:
        doc.rename_block(name, new_name)
        typer.echo(str(doc.save(output)))
    except _DXF_ERRORS as exc:
        raise _dxf_error(exc) from None


@dxf_app.command("block-delete")
def dxf_block_delete(
    file: FileArg,
    name: BlockArg,
    output: DxfOutput,
    references: Annotated[
        bool, typer.Option("--references", help="Also delete the references to the block.")
    ] = False,
) -> None:
    """Delete a block definition (a referenced block needs --references)."""
    doc = _open_dxf(file)
    try:
        count = doc.delete_block(name, delete_references=references)
        typer.echo(f"deleted {count} references", err=True)
        typer.echo(str(doc.save(output)))
    except _DXF_ERRORS as exc:
        raise _dxf_error(exc) from None


@dxf_app.command("block-purge")
def dxf_block_purge(file: FileArg, output: DxfOutput) -> None:
    """Delete every unreferenced block; the removed names are printed on stderr."""
    doc = _open_dxf(file)
    try:
        for name in doc.purge_blocks():
            typer.echo(f"removed {name}", err=True)
        typer.echo(str(doc.save(output)))
    except _DXF_ERRORS as exc:
        raise _dxf_error(exc) from None


@dxf_app.command("block-insert")
def dxf_block_insert(
    file: FileArg,
    name: BlockArg,
    output: DxfOutput,
    at: Annotated[str, typer.Option("--at", help="Insertion point `x,y[,z]`.")] = "0,0",
    scale: Annotated[
        str, typer.Option("--scale", "-s", help="Scale factor `s` or `sx,sy[,sz]`.")
    ] = "1",
    rotation: Annotated[float, typer.Option("--rotation", help="Rotation in degrees.")] = 0.0,
    attr: DxfAttrs = None,
    layer: Annotated[
        str | None, typer.Option("--layer", "-l", help="Layer (default: current layer).")
    ] = None,
    color: Annotated[
        str | None,
        typer.Option("--color", "-c", help="ACI index, colour name, #RRGGBB, bylayer, byblock."),
    ] = None,
    layout: DxfLayout = "Model",
    block: DxfInBlock = None,
) -> None:
    """Insert a block reference; prints the new handle on stderr."""
    doc = _open_dxf(file)
    try:
        handle = doc.insert_block(
            name,
            _dxf_point(at),
            scale=_dxf_scale(scale),
            rotation=rotation,
            attributes=_dxf_attrs(attr),
            layout=layout,
            block=block,
            layer=layer,
            color=color,
        )
        typer.echo(f"added {handle}", err=True)
        typer.echo(str(doc.save(output)))
    except _DXF_ERRORS as exc:
        raise _dxf_error(exc) from None


@dxf_app.command("inserts")
def dxf_inserts(
    file: FileArg,
    name: Annotated[
        str | None, typer.Option("--name", "-n", help="Only references to this block.")
    ] = None,
    layout: DxfLayout = "Model",
    block: DxfInBlock = None,
) -> None:
    """List block references as JSON (handle, block, placement, attribute values)."""
    doc = _open_dxf(file)
    try:
        _dump([ref.to_dict() for ref in doc.inserts(layout, block=block, name=name)])
    except _DXF_ERRORS as exc:
        raise _dxf_error(exc) from None


@dxf_app.command("insert-set")
def dxf_insert_set(
    file: FileArg,
    handle: Annotated[str, typer.Argument(help="Handle of the block reference.")],
    output: DxfOutput,
    attr: DxfAttrs = None,
    at: Annotated[str | None, typer.Option("--at", help="New insertion point `x,y`.")] = None,
    scale: Annotated[
        str | None, typer.Option("--scale", "-s", help="New scale `s` or `sx,sy[,sz]`.")
    ] = None,
    rotation: Annotated[
        float | None, typer.Option("--rotation", help="New rotation in degrees.")
    ] = None,
) -> None:
    """Set attribute values (--attr TAG=VALUE) and/or the placement of a block reference."""
    doc = _open_dxf(file)
    try:
        values = _dxf_attrs(attr)
        if not values and at is None and scale is None and rotation is None:
            raise ValueError("Nothing to change: give --attr, --at, --scale or --rotation")
        doc.insert(handle)  # validate the handle before changing anything
        if at is not None or scale is not None or rotation is not None:
            doc.modify_insert(
                handle,
                insert=None if at is None else _dxf_point(at),
                scale=None if scale is None else _dxf_scale(scale),
                rotation=rotation,
            )
        if values:
            doc.set_attributes(handle, values)
        typer.echo(str(doc.save(output)))
    except _DXF_ERRORS as exc:
        raise _dxf_error(exc) from None


@dxf_app.command("explode")
def dxf_explode(
    file: FileArg,
    output: DxfOutput,
    handles: DxfHandles = None,
    name: Annotated[
        str | None, typer.Option("--name", "-n", help="Explode every reference to this block.")
    ] = None,
    select_all: Annotated[
        bool, typer.Option("--all", help="Explode every block reference of the layout.")
    ] = False,
    drop_attributes: Annotated[
        bool, typer.Option("--drop-attributes", help="Discard attributes (default: to TEXT).")
    ] = False,
    recursive: Annotated[
        bool, typer.Option("--recursive", help="Also explode nested block references.")
    ] = False,
    layout: DxfLayout = "Model",
) -> None:
    """Explode block references (handles, --name BLOCK or --all) into their entities."""
    doc = _open_dxf(file)
    try:
        if handles:
            if name is not None or select_all:
                raise ValueError("Give either handles or --name/--all, not both")
            selected = handles
        elif name is not None or select_all:
            selected = [ref.handle for ref in doc.inserts(layout, name=name)]
            if not selected:
                raise ValueError("No block references match the selection")
        else:
            raise ValueError("No block references selected: give handles, --name or --all")
        created = doc.explode(
            selected, attributes="drop" if drop_attributes else "text", recursive=recursive
        )
        typer.echo(f"exploded {len(selected)} references into {len(created)} entities", err=True)
        typer.echo(str(doc.save(output)))
    except _DXF_ERRORS as exc:
        raise _dxf_error(exc) from None


# -- DXF dimensions ---------------------------------------------------------------

_DIM_KINDS = ("linear", "aligned", "radius", "diameter")


@dxf_app.command("dims")
def dxf_dims(
    file: FileArg,
    layout: DxfLayout = "Model",
    layer: Annotated[str | None, typer.Option("--layer", "-l", help="Only this layer.")] = None,
    kind: Annotated[
        list[str] | None,
        typer.Option("--kind", "-k", help="Only this kind, e.g. linear, radius (repeatable)."),
    ] = None,
    block: DxfInBlock = None,
) -> None:
    """List dimensions as JSON (handle, kind, measurement, displayed text, points)."""
    doc = _open_dxf(file)
    try:
        dims = doc.dimensions(layout, block=block, layer=layer, kinds=kind or None)
        _dump([d.to_dict() for d in dims])
    except _DXF_ERRORS as exc:
        raise _dxf_error(exc) from None


@dxf_app.command("dim-add")
def dxf_dim_add(
    file: FileArg,
    kind: Annotated[str, typer.Argument(help="Kind: linear, aligned, radius or diameter.")],
    output: DxfOutput,
    point: DxfPoints = None,
    base: Annotated[
        str | None,
        typer.Option("--base", "-b", help="Linear: point `x,y` on the dimension line."),
    ] = None,
    distance: Annotated[
        float | None,
        typer.Option("--distance", "-d", help="Offset of the dimension line from the points."),
    ] = None,
    angle: Annotated[
        str | None,
        typer.Option(
            "--angle",
            "-a",
            help="Linear: `horizontal`, `vertical` or degrees. Radius/diameter: direction.",
        ),
    ] = None,
    radius: Annotated[float | None, typer.Option("--radius", "-r", help="Circle radius.")] = None,
    entity: Annotated[
        str | None,
        typer.Option("--entity", "-e", help="Radius/diameter: handle of a CIRCLE or ARC."),
    ] = None,
    text: Annotated[
        str | None, typer.Option("--text", help="Text override; `<>` is the measurement.")
    ] = None,
    style: Annotated[
        str | None, typer.Option("--style", help="Dimension style (default: current).")
    ] = None,
    text_height: Annotated[
        float | None, typer.Option("--text-height", help="Text height override.")
    ] = None,
    arrow_size: Annotated[
        float | None, typer.Option("--arrow-size", help="Arrow size override.")
    ] = None,
    decimals: Annotated[
        int | None, typer.Option("--decimals", help="Decimal places override (0-8).")
    ] = None,
    separator: Annotated[
        str | None, typer.Option("--separator", help="Decimal separator override: `.` or `,`.")
    ] = None,
    layer: Annotated[
        str | None, typer.Option("--layer", "-l", help="Layer (default: current layer).")
    ] = None,
    color: Annotated[
        str | None,
        typer.Option("--color", "-c", help="ACI index, colour name, #RRGGBB, bylayer, byblock."),
    ] = None,
    layout: DxfLayout = "Model",
    block: DxfInBlock = None,
) -> None:
    """Add a dimension; prints the new handle and its measurement on stderr.

    linear and aligned need two --point (the measured points) and --distance
    (linear may use --base instead); radius and diameter need --entity, or one
    --point (the centre) and --radius.
    """
    doc = _open_dxf(file)
    common: dict[str, Any] = {
        "text": text,
        "style": style,
        "text_height": text_height,
        "arrow_size": arrow_size,
        "decimals": decimals,
        "decimal_separator": separator,
        "layout": layout,
        "block": block,
        "layer": layer,
        "color": color,
    }
    try:
        name = kind.strip().lower()
        if name not in _DIM_KINDS:
            raise ValueError(f"Unknown dimension kind: {kind} (use {', '.join(_DIM_KINDS)})")
        points = [_dxf_point(p) for p in point or []]
        if name in ("linear", "aligned"):
            if len(points) != 2:
                raise ValueError(f"{name} needs exactly 2 --point values")
            if radius is not None or entity is not None:
                raise ValueError(f"--radius and --entity do not apply to {name} dimensions")
            if name == "linear":
                handle = doc.add_linear_dimension(
                    points[0],
                    points[1],
                    base=None if base is None else _dxf_point(base),
                    distance=distance,
                    angle=_dxf_dim_angle(angle, named=True),
                    **common,
                )
            else:
                if base is not None or angle is not None:
                    raise ValueError("--base and --angle do not apply to aligned dimensions")
                if distance is None:
                    raise ValueError("aligned needs --distance")
                handle = doc.add_aligned_dimension(points[0], points[1], distance, **common)
        else:
            if base is not None or distance is not None:
                raise ValueError(f"--base and --distance do not apply to {name} dimensions")
            if len(points) > 1:
                raise ValueError(f"{name} takes at most 1 --point (the centre)")
            add = doc.add_radius_dimension if name == "radius" else doc.add_diameter_dimension
            handle = add(
                points[0] if points else None,
                radius,
                entity=entity,
                angle=_dxf_dim_angle(angle, named=False),
                **common,
            )
        info = doc.dimension(handle)
        typer.echo(f"added {handle} measurement {info.measurement:g} text {info.text}", err=True)
        typer.echo(str(doc.save(output)))
    except _DXF_ERRORS as exc:
        raise _dxf_error(exc) from None


def _dxf_dim_angle(value: str | None, *, named: bool) -> float | str:
    if value is None:
        return 0.0
    if named and value.strip().lower() in ("horizontal", "vertical"):
        return value
    try:
        return float(value)
    except ValueError:
        raise ValueError(f"Invalid angle: {value!r}") from None


@dxf_app.command("dim-set")
def dxf_dim_set(
    file: FileArg,
    handle: Annotated[str, typer.Argument(help="Dimension handle (see `anydoc dxf dims`).")],
    output: DxfOutput,
    text: Annotated[
        str | None, typer.Option("--text", help="Text override; `<>` is the measurement.")
    ] = None,
    reset: Annotated[
        bool, typer.Option("--reset", help="Remove the override and show the measurement.")
    ] = False,
) -> None:
    """Override or reset the text of a dimension and redraw it."""
    doc = _open_dxf(file)
    try:
        if (text is None) == (not reset):
            raise ValueError("Give exactly one of --text and --reset")
        info = doc.set_dimension_text(handle, None if reset else text)
        typer.echo(f"dimension {info.handle} text {info.text}", err=True)
        typer.echo(str(doc.save(output)))
    except _DXF_ERRORS as exc:
        raise _dxf_error(exc) from None


@dxf_app.command("dim-update")
def dxf_dim_update(
    file: FileArg,
    output: DxfOutput,
    handles: DxfHandles = None,
    layout: DxfLayout = "Model",
) -> None:
    """Redraw dimensions (default: all of the layout) after they were moved or scaled."""
    doc = _open_dxf(file)
    try:
        updated = doc.update_dimensions(handles or None, layout=layout)
        typer.echo(f"updated {len(updated)} dimensions", err=True)
        typer.echo(str(doc.save(output)))
    except _DXF_ERRORS as exc:
        raise _dxf_error(exc) from None


@dxf_app.command("dim-delete")
def dxf_dim_delete(
    file: FileArg,
    output: DxfOutput,
    handles: Annotated[list[str], typer.Argument(help="Dimension handles.")],
) -> None:
    """Delete dimensions together with their geometry blocks."""
    doc = _open_dxf(file)
    try:
        typer.echo(f"deleted {doc.delete_dimensions(handles)} dimensions", err=True)
        typer.echo(str(doc.save(output)))
    except _DXF_ERRORS as exc:
        raise _dxf_error(exc) from None


# -- DXF measurement --------------------------------------------------------------

DxfVisible = Annotated[
    bool, typer.Option("--visible-only", help="Skip layers that are off or frozen.")
]


@dxf_app.command("distance")
def dxf_distance(
    point: DxfPoints = None,
    closed: Annotated[
        bool, typer.Option("--closed", help="Close the path and report its area.")
    ] = False,
) -> None:
    """Measure the distance between points given with -p `x,y[,z]` (two or more).

    Prints JSON with the total length and each segment (distance, dx, dy, dz,
    angle); with --closed the closing segment and the enclosed area are included.
    """
    from anydoc.formats.dxf import measure

    try:
        points = [_dxf_point(p) for p in point or []]
        if len(points) < 2:
            raise ValueError(f"distance needs 2 or more --point, got {len(points)}")
        path = [*points, points[0]] if closed else points
        segments = [measure.distance(a, b).to_dict() for a, b in zip(path, path[1:], strict=False)]
        result: dict[str, Any] = {
            "length": measure.path_length(points, closed=closed),
            "segments": segments,
        }
        if closed:
            result["area"] = measure.polygon_area(points)
        _dump(result)
    except _DXF_ERRORS as exc:
        raise _dxf_error(exc) from None


@dxf_app.command("measure")
def dxf_measure(
    file: FileArg,
    handles: DxfHandles = None,
    layout: DxfLayout = "Model",
    layer: DxfSelLayer = None,
    types: DxfSelType = None,
    select_all: DxfSelAll = False,
) -> None:
    """Measure entities: length, area (closed shapes) and bounding box, as JSON.

    Also prints the total length, total area and overall bounding box.
    """
    doc = _open_dxf(file)
    try:
        selected = _dxf_selection(doc, handles, layout, layer, types, select_all)
        rows = doc.measure(selected)
        box = doc.bounding_box(selected)
        _dump(
            {
                "units": doc.units,
                "entities": [row.to_dict() for row in rows],
                "total_length": sum(row.length or 0.0 for row in rows),
                "total_area": sum(row.area or 0.0 for row in rows),
                "bbox": None if box is None else box.to_dict(),
            }
        )
    except _DXF_ERRORS as exc:
        raise _dxf_error(exc) from None


@dxf_app.command("bbox")
def dxf_bbox(
    file: FileArg,
    handles: DxfHandles = None,
    layout: DxfLayout = "Model",
    layer: DxfSelLayer = None,
    types: DxfSelType = None,
    visible_only: DxfVisible = False,
) -> None:
    """Print the bounding box of a layout (or of the given/filtered entities) as JSON."""
    doc = _open_dxf(file)
    try:
        if handles and (layer is not None or types or visible_only):
            raise ValueError("Give either handles or --layer/--type/--visible-only, not both")
        box = doc.bounding_box(
            handles or None,
            layout=layout,
            layer=layer,
            types=types or None,
            visible_only=visible_only,
        )
        if box is None:
            raise ValueError("No entities with extents")
        _dump({"units": doc.units, **box.to_dict()})
    except _DXF_ERRORS as exc:
        raise _dxf_error(exc) from None


@dxf_app.command("query")
def dxf_query(
    file: FileArg,
    at: Annotated[str | None, typer.Option("--at", help="Pick point `x,y`.")] = None,
    tolerance: Annotated[
        float | None,
        typer.Option("--tolerance", help="Pick distance (default: 1% of the extents diagonal)."),
    ] = None,
    window: Annotated[
        list[str] | None,
        typer.Option("--window", "-w", help="Window corner `x,y`; give it twice."),
    ] = None,
    crossing: Annotated[
        bool, typer.Option("--crossing", help="Also select entities crossing the window.")
    ] = False,
    layout: DxfLayout = "Model",
    layer: DxfSelLayer = None,
    types: DxfSelType = None,
    visible_only: DxfVisible = False,
) -> None:
    """Find entities at a point (--at) or in a rectangular window (-w twice), as JSON."""
    doc = _open_dxf(file)
    filters: dict[str, Any] = {
        "layout": layout,
        "layer": layer,
        "types": types or None,
        "visible_only": visible_only,
    }
    try:
        if (at is None) == (not window):
            raise ValueError("Give either --at or two --window corners")
        if at is not None:
            hits = doc.entities_at(_dxf_point(at), tolerance=tolerance, **filters)
            distances = {hit.handle: hit.distance for hit in hits}
            found = list(distances)
        else:
            corners = [_dxf_point(p) for p in window or []]
            if len(corners) != 2:
                raise ValueError(f"--window needs 2 corners, got {len(corners)}")
            found = doc.entities_in_window(corners[0], corners[1], crossing=crossing, **filters)
            distances = {}
        rows = []
        for handle in found:
            row = doc.entity(handle).to_dict()
            if handle in distances:
                row["distance"] = distances[handle]
            rows.append(row)
        _dump(rows)
    except _DXF_ERRORS as exc:
        raise _dxf_error(exc) from None
