"""OCR for scanned PDFs: produce a searchable PDF with the Tesseract CLI.

Pages are rendered to PNG with pypdfium2, recognised by ``tesseract <img> <base> pdf`` (which
emits a one-page PDF with an invisible text layer) and the pages are merged back with pypdf.
Page indexes are 0-based. Pages that already contain text are kept untouched unless
``skip_text=False``. Requires the optional external tool ``tesseract`` and its language data
(``chi_tra`` and ``eng`` by default).
"""

from __future__ import annotations

import io
import subprocess
from collections.abc import Callable, Iterable
from typing import Any

import pypdfium2
from pypdf import PdfReader, PdfWriter

from anydoc.core.errors import CorruptFileError
from anydoc.external.tools import ExternalToolError, require_tool, run_tool, temp_workdir
from anydoc.formats.pdf.pages import _check, _dump, _reader

__all__ = ["DEFAULT_LANG", "list_ocr_languages", "ocr_pdf"]

DEFAULT_LANG = "chi_tra+eng"

Runner = Callable[..., "subprocess.CompletedProcess[str]"]


def list_ocr_languages(*, runner: Runner = subprocess.run) -> list[str]:
    """Return the language codes installed for Tesseract (excluding ``osd``)."""
    require_tool("tesseract")
    res = run_tool("tesseract", ["--list-langs"], timeout=30, runner=runner)
    langs = [ln.strip() for ln in (res.stdout or res.stderr).splitlines()[1:] if ln.strip()]
    return [ln for ln in langs if ln != "osd"]


def _page_has_text(page: Any) -> bool:
    try:
        return bool((page.extract_text() or "").strip())
    except Exception:
        return False


def _render(data: bytes, index: int, dpi: float, password: str) -> bytes:
    try:
        pdf = pypdfium2.PdfDocument(data, password=password or None)
    except Exception as exc:
        raise CorruptFileError(f"Cannot render PDF: {exc}") from exc
    try:
        page = pdf[index]
        try:
            image = page.render(scale=dpi / 72.0).to_pil()
        finally:
            page.close()
    finally:
        pdf.close()
    buf = io.BytesIO()
    image.save(buf, format="PNG", dpi=(dpi, dpi))
    return buf.getvalue()


def ocr_pdf(
    data: bytes,
    pages: Iterable[int] | None = None,
    *,
    lang: str = DEFAULT_LANG,
    dpi: float = 300.0,
    skip_text: bool = True,
    password: str = "",
    timeout: float = 300.0,
    runner: Runner = subprocess.run,
) -> bytes:
    """Return a searchable copy of the PDF ``data`` by OCR-ing the selected pages.

    ``pages`` selects 0-based page indexes (default: all). With ``skip_text`` (default) pages
    that already have a text layer are left unchanged. ``lang`` is a Tesseract language
    string such as ``"chi_tra+eng"``.

    Raises:
        ExternalToolMissingError: if Tesseract is not installed.
        ExternalToolError: if a language is not installed or Tesseract fails.
        ValueError: for a non-positive ``dpi`` or an empty ``lang``.
        IndexError: for an out-of-range page.
    """
    if dpi <= 0:
        raise ValueError("dpi must be positive")
    wanted = [x for x in lang.split("+") if x]
    if not wanted:
        raise ValueError("lang must not be empty")
    require_tool("tesseract")
    installed = set(list_ocr_languages(runner=runner))
    missing = [x for x in wanted if x not in installed]
    if missing:
        raise ExternalToolError(
            f"Tesseract language data not installed: {', '.join(missing)} "
            f"(installed: {', '.join(sorted(installed)) or 'none'})"
        )

    reader = _reader(data, password)
    count = len(reader.pages)
    targets = _check(range(count) if pages is None else pages, count)
    writer = PdfWriter()
    done: dict[int, PdfReader] = {}
    # Decrypted source for rendering/copying when the input is password protected.
    source = data
    if reader.is_encrypted:
        plain = PdfWriter(clone_from=reader)
        source = _dump(plain)
    for i in sorted(set(targets)):
        if skip_text and _page_has_text(reader.pages[i]):
            continue
        png = _render(source, i, dpi, "")
        with temp_workdir("anydoc-ocr-") as tmp:
            img = tmp / "page.png"
            img.write_bytes(png)
            base = tmp / "out"
            run_tool(
                "tesseract",
                [img, base, "-l", "+".join(wanted), "--dpi", str(int(dpi)), "pdf"],
                timeout=timeout,
                runner=runner,
            )
            out = base.with_suffix(".pdf")
            if not out.is_file():
                raise ExternalToolError(f"Tesseract produced no PDF for page {i + 1}")
            done[i] = PdfReader(io.BytesIO(out.read_bytes()))
    base_reader = PdfReader(io.BytesIO(source))
    for i in range(count):
        if i in done:
            ocr_page = done[i].pages[0]
            orig = base_reader.pages[i]
            w, h = float(orig.mediabox.width), float(orig.mediabox.height)
            ow, oh = float(ocr_page.mediabox.width), float(ocr_page.mediabox.height)
            if ow > 0 and oh > 0 and (abs(ow - w) > 0.5 or abs(oh - h) > 0.5):
                ocr_page.scale_to(w, h)
            writer.add_page(ocr_page)
        else:
            writer.add_page(base_reader.pages[i])
    if base_reader.metadata:
        writer.add_metadata({k: str(v) for k, v in base_reader.metadata.items()})
    return _dump(writer)
