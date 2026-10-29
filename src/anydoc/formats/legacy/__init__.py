"""Legacy binary Office formats (DOC/XLS/PPT), read via LibreOffice conversion to OOXML."""

from anydoc.formats.legacy.handler import DocHandler, LegacyHandler, PptHandler, XlsHandler

__all__ = ["DocHandler", "LegacyHandler", "PptHandler", "XlsHandler"]
