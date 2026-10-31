"""Office / ODF to PDF conversion through headless LibreOffice."""

from __future__ import annotations

import shutil
from pathlib import Path

from anydoc.core.convert import ConversionGraph, Converter, graph
from anydoc.external import convert_with_libreoffice
from anydoc.external.tools import temp_workdir

OFFICE_PDF_SOURCES: tuple[str, ...] = (
    "docx",
    "doc",
    "odt",
    "rtf",
    "xlsx",
    "xls",
    "ods",
    "pptx",
    "ppt",
    "odp",
)
"""Format ids that LibreOffice can export to PDF."""


def make_office_to_pdf(src_format: str) -> Converter:
    """Build the ``src_format`` -> ``pdf`` converter backed by LibreOffice."""

    def office_to_pdf(src: Path, dst: Path) -> None:
        # LibreOffice picks the input filter from the extension, so stage the
        # source under a name carrying the correct one.
        with temp_workdir("anydoc-office-pdf-") as work:
            staged = work / f"input.{src_format}"
            shutil.copyfile(src, staged)
            out = convert_with_libreoffice(staged, "pdf", work)
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(out, dst)

    return Converter(
        src_format,
        "pdf",
        office_to_pdf,
        cost=2.0,
        requires=("libreoffice",),
        name=f"{src_format}->pdf (libreoffice)",
    )


def register_office_pdf(target: ConversionGraph | None = None) -> None:
    """Register every Office/ODF -> PDF converter on ``target`` (default: global graph)."""
    g = target if target is not None else graph
    for fmt in OFFICE_PDF_SOURCES:
        g.add(make_office_to_pdf(fmt))
