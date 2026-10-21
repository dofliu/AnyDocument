"""Exception hierarchy for AnyDocument."""

from __future__ import annotations


class AnyDocError(Exception):
    """Base class for all AnyDocument errors."""


class UnsupportedFormatError(AnyDocError):
    """Raised when a file format is unknown or not supported."""


class CorruptFileError(AnyDocError):
    """Raised when a file is malformed or cannot be parsed."""


class ExternalToolMissingError(AnyDocError):
    """Raised when a required external tool (LibreOffice, Tesseract, ...) is unavailable."""

    def __init__(self, tool: str, message: str | None = None) -> None:
        self.tool = tool
        super().__init__(message or f"External tool not found: {tool}")


class SecurityLimitError(AnyDocError):
    """Raised when a security limit (size, compression ratio, ...) is exceeded."""


class OperationNotSupportedError(AnyDocError):
    """Raised when a format does not support the requested operation."""
