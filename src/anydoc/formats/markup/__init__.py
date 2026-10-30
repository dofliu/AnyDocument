"""RTF, HTML and EPUB read-only handlers."""

from anydoc.formats.markup.epub import EpubDocument, EpubHandler
from anydoc.formats.markup.html import HtmlDocument, HtmlHandler
from anydoc.formats.markup.rtf import RtfDocument, RtfHandler

__all__ = [
    "EpubDocument",
    "EpubHandler",
    "HtmlDocument",
    "HtmlHandler",
    "RtfDocument",
    "RtfHandler",
]
