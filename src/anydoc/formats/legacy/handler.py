"""Handlers that open DOC/XLS/PPT by converting to DOCX/XLSX/PPTX with LibreOffice."""

from __future__ import annotations

from pathlib import Path
from typing import Any, ClassVar

from anydoc.core.errors import CorruptFileError
from anydoc.core.model import Capability, Document
from anydoc.core.registry import FormatHandler
from anydoc.external.libreoffice import convert_with_libreoffice
from anydoc.external.tools import ExternalToolError, require_tool, temp_workdir
from anydoc.formats.docx import DocxHandler
from anydoc.formats.pptx import PptxHandler
from anydoc.formats.xlsx import XlsxHandler

_OLE_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"


class LegacyHandler(FormatHandler):
    """Base for legacy binary Office handlers.

    The file is converted to its OOXML counterpart with LibreOffice and handed to the
    matching OOXML handler. The returned document reports the legacy ``format`` and
    ``mime``, records ``extra["converted_from"]``, and is detached from the source path
    (``save`` needs an explicit path, so the original binary file is never overwritten
    with OOXML content).
    """

    target_format: ClassVar[str]
    mime: ClassVar[str]
    capabilities: ClassVar[frozenset[Capability]] = frozenset(
        {Capability.READ, Capability.EXTRACT_TEXT, Capability.CONVERT}
    )

    @classmethod
    def _delegate(cls) -> FormatHandler:
        raise NotImplementedError

    @classmethod
    def sniff(cls, head: bytes, path: Path | None) -> float:
        """Return confidence from the extension (OLE2 subtype detection lives in ``detect``)."""
        if path is not None and path.suffix.lower().lstrip(".") in cls.extensions:
            return 0.9 if head.startswith(_OLE_MAGIC) else 0.5
        return 0.0

    def open(self, source: Path | bytes) -> Document:
        """Open a legacy file by converting it to OOXML.

        Raises:
            ExternalToolMissingError: if LibreOffice is not installed.
            CorruptFileError: if the file cannot be read or converted.
        """
        require_tool("libreoffice")
        if isinstance(source, Path):
            try:
                raw = source.read_bytes()
            except OSError as exc:
                raise CorruptFileError(f"Cannot read {source}: {exc}") from exc
        else:
            raw = source
        if not raw.startswith(_OLE_MAGIC):
            raise CorruptFileError(f"Not an OLE2 {self.format_id.upper()} file")
        with temp_workdir("anydoc-legacy-") as work:
            src = work / f"input.{self.format_id}"
            src.write_bytes(raw)
            outdir = work / "out"
            outdir.mkdir()
            try:
                converted = convert_with_libreoffice(src, self.target_format, outdir)
            except ExternalToolError as exc:
                raise CorruptFileError(f"Cannot convert {self.format_id.upper()}: {exc}") from exc
            data = converted.read_bytes()
        doc: Any = self._delegate().open(data)
        doc.format = self.format_id
        doc.path = None
        doc.metadata.format = self.format_id
        doc.metadata.mime = self.mime
        doc.metadata.extra["converted_from"] = self.format_id
        doc.metadata.extra["converted_to"] = self.target_format
        return doc  # type: ignore[no-any-return]


class DocHandler(LegacyHandler):
    """Handler for legacy Word ``.doc`` documents."""

    format_id: ClassVar[str] = "doc"
    extensions: ClassVar[tuple[str, ...]] = ("doc",)
    mime_types: ClassVar[tuple[str, ...]] = ("application/msword",)
    target_format: ClassVar[str] = "docx"
    mime: ClassVar[str] = "application/msword"

    @classmethod
    def _delegate(cls) -> FormatHandler:
        return DocxHandler()


class XlsHandler(LegacyHandler):
    """Handler for legacy Excel ``.xls`` workbooks."""

    format_id: ClassVar[str] = "xls"
    extensions: ClassVar[tuple[str, ...]] = ("xls",)
    mime_types: ClassVar[tuple[str, ...]] = ("application/vnd.ms-excel",)
    target_format: ClassVar[str] = "xlsx"
    mime: ClassVar[str] = "application/vnd.ms-excel"

    @classmethod
    def _delegate(cls) -> FormatHandler:
        return XlsxHandler()


class PptHandler(LegacyHandler):
    """Handler for legacy PowerPoint ``.ppt`` presentations."""

    format_id: ClassVar[str] = "ppt"
    extensions: ClassVar[tuple[str, ...]] = ("ppt",)
    mime_types: ClassVar[tuple[str, ...]] = ("application/vnd.ms-powerpoint",)
    target_format: ClassVar[str] = "pptx"
    mime: ClassVar[str] = "application/vnd.ms-powerpoint"

    @classmethod
    def _delegate(cls) -> FormatHandler:
        return PptxHandler()
