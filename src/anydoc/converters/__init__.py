"""Built-in converters registered on the global conversion graph."""

from __future__ import annotations

from anydoc.converters.docx_text import (
    docx_to_html,
    docx_to_markdown,
    html_to_docx,
    markdown_to_docx,
    register_docx_text,
)
from anydoc.converters.office_pdf import OFFICE_PDF_SOURCES, register_office_pdf
from anydoc.converters.pdf_image import (
    IMAGE_FORMATS,
    images_to_pdf,
    pdf_to_images,
    register_pdf_image,
)
from anydoc.converters.tabular import (
    csv_to_csv,
    csv_to_json,
    csv_to_xlsx,
    json_to_csv,
    json_to_xlsx,
    register_tabular,
    xlsx_to_csv,
    xlsx_to_csv_files,
    xlsx_to_json,
)

__all__ = [
    "IMAGE_FORMATS",
    "OFFICE_PDF_SOURCES",
    "csv_to_csv",
    "csv_to_json",
    "csv_to_xlsx",
    "docx_to_html",
    "docx_to_markdown",
    "html_to_docx",
    "images_to_pdf",
    "json_to_csv",
    "json_to_xlsx",
    "load_builtin_converters",
    "markdown_to_docx",
    "pdf_to_images",
    "register_docx_text",
    "register_office_pdf",
    "register_pdf_image",
    "register_tabular",
    "xlsx_to_csv",
    "xlsx_to_csv_files",
    "xlsx_to_json",
]

_loaded = False


def load_builtin_converters() -> None:
    """Register all built-in converters on the global graph (idempotent)."""
    global _loaded
    if _loaded:
        return
    _loaded = True
    register_office_pdf()
    register_pdf_image()
    register_docx_text()
    register_tabular()
