"""ODF handlers (ODT/ODS/ODP): text, table and slide structure read straight from the XML."""

from anydoc.formats.odf.handler import (
    BlockInfo,
    CellInfo,
    OdfDocument,
    OdfHandler,
    OdpDocument,
    OdpHandler,
    OdsDocument,
    OdsHandler,
    OdtDocument,
    OdtHandler,
    ShapeInfo,
    SheetInfo,
    SlideInfo,
)

__all__ = [
    "BlockInfo",
    "CellInfo",
    "OdfDocument",
    "OdfHandler",
    "OdpDocument",
    "OdpHandler",
    "OdsDocument",
    "OdsHandler",
    "OdtDocument",
    "OdtHandler",
    "ShapeInfo",
    "SheetInfo",
    "SlideInfo",
]
