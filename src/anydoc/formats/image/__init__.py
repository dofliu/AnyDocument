"""Image handlers: raster formats via Pillow and SVG via direct XML parsing."""

from anydoc.formats.image.handler import (
    BmpHandler,
    GifHandler,
    ImageDocument,
    JpegHandler,
    PngHandler,
    TiffHandler,
    WebpHandler,
)
from anydoc.formats.image.svg import SvgDocument, SvgHandler

__all__ = [
    "BmpHandler",
    "GifHandler",
    "ImageDocument",
    "JpegHandler",
    "PngHandler",
    "SvgDocument",
    "SvgHandler",
    "TiffHandler",
    "WebpHandler",
]
