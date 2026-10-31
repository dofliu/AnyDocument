"""AnyDocument: universal document viewer, converter and editor toolkit."""

from __future__ import annotations

from pathlib import Path

from anydoc.core.detect import Detection, detect
from anydoc.core.errors import UnsupportedFormatError
from anydoc.core.model import Document
from anydoc.core.registry import registry

__version__ = "0.1.0"

__all__ = ["Detection", "__version__", "detect", "extract_text", "open"]


def open(path: str | Path) -> Document:  # noqa: A001
    """Detect the format of ``path`` and open it with the registered handler.

    Raises:
        UnsupportedFormatError: if the format is unknown or no handler is registered for it.
    """
    p = Path(path)
    detection = detect(p)
    handler = registry.get(detection.format)
    if handler is None:
        registry.load_entry_points()
        handler = registry.get(detection.format)
    if handler is None:
        raise UnsupportedFormatError(f"No handler registered for format: {detection.format}")
    return handler().open(p)


def extract_text(path: str | Path) -> str:
    """Open ``path`` with its handler, return its text in reading order and close it.

    Raises:
        UnsupportedFormatError: if the format is unknown or no handler is registered for it.
        FileNotFoundError: if ``path`` does not exist.
    """
    doc = open(path)
    try:
        return doc.text()
    finally:
        doc.close()
