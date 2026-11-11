"""PPTX handler: slides, layouts, shape tree, notes, text extraction and editing."""

from anydoc.formats.pptx.handler import PptxDocument, PptxHandler, ShapeInfo, SlideInfo
from anydoc.formats.pptx.shapes import ParagraphFormat, RunFormat, ShapeFormat

__all__ = [
    "ParagraphFormat",
    "PptxDocument",
    "PptxHandler",
    "RunFormat",
    "ShapeFormat",
    "ShapeInfo",
    "SlideInfo",
]
