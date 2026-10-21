"""Core abstractions: document model and error hierarchy."""

from anydoc.core.errors import (
    AnyDocError,
    CorruptFileError,
    ExternalToolMissingError,
    OperationNotSupportedError,
    SecurityLimitError,
    UnsupportedFormatError,
)
from anydoc.core.model import Capability, Document, Metadata, Part

__all__ = [
    "AnyDocError",
    "Capability",
    "CorruptFileError",
    "Document",
    "ExternalToolMissingError",
    "Metadata",
    "OperationNotSupportedError",
    "Part",
    "SecurityLimitError",
    "UnsupportedFormatError",
]
