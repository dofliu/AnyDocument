"""Built-in converters registered on the global conversion graph."""

from __future__ import annotations

from anydoc.converters.office_pdf import OFFICE_PDF_SOURCES, register_office_pdf
from anydoc.converters.pdf_image import (
    IMAGE_FORMATS,
    images_to_pdf,
    pdf_to_images,
    register_pdf_image,
)

__all__ = [
    "IMAGE_FORMATS",
    "OFFICE_PDF_SOURCES",
    "images_to_pdf",
    "load_builtin_converters",
    "pdf_to_images",
    "register_office_pdf",
    "register_pdf_image",
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
