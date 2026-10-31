"""Built-in converters registered on the global conversion graph."""

from __future__ import annotations

from anydoc.converters.office_pdf import OFFICE_PDF_SOURCES, register_office_pdf

__all__ = ["OFFICE_PDF_SOURCES", "load_builtin_converters", "register_office_pdf"]

_loaded = False


def load_builtin_converters() -> None:
    """Register all built-in converters on the global graph (idempotent)."""
    global _loaded
    if _loaded:
        return
    _loaded = True
    register_office_pdf()
