"""PPTX handler: slides, layouts, shape tree, notes, text extraction and editing."""

from anydoc.formats.pptx.charts import ChartInfo, ChartSeries
from anydoc.formats.pptx.handler import PptxDocument, PptxHandler, ShapeInfo, SlideInfo
from anydoc.formats.pptx.shapes import ParagraphFormat, RunFormat, ShapeFormat
from anydoc.formats.pptx.tables import TableCellFormat, TableInfo
from anydoc.formats.pptx.themes import LayoutInfo, MasterInfo, PlaceholderInfo, TextStyle, ThemeInfo

__all__ = [
    "ChartInfo",
    "ChartSeries",
    "LayoutInfo",
    "MasterInfo",
    "ParagraphFormat",
    "PlaceholderInfo",
    "PptxDocument",
    "PptxHandler",
    "RunFormat",
    "ShapeFormat",
    "ShapeInfo",
    "SlideInfo",
    "TableCellFormat",
    "TableInfo",
    "TextStyle",
    "ThemeInfo",
]
