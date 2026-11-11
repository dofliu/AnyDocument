"""PPTX handler: slides, layouts, shape tree, notes, text extraction and editing."""

from anydoc.formats.pptx.charts import ChartInfo, ChartSeries
from anydoc.formats.pptx.handler import PptxDocument, PptxHandler, ShapeInfo, SlideInfo
from anydoc.formats.pptx.shapes import ParagraphFormat, RunFormat, ShapeFormat
from anydoc.formats.pptx.tables import TableCellFormat, TableInfo

__all__ = [
    "ChartInfo",
    "ChartSeries",
    "ParagraphFormat",
    "PptxDocument",
    "PptxHandler",
    "RunFormat",
    "ShapeFormat",
    "ShapeInfo",
    "SlideInfo",
    "TableCellFormat",
    "TableInfo",
]
