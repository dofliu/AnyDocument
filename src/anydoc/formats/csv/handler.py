"""CSV / TSV document and handlers."""

from __future__ import annotations

import csv
import io
from pathlib import Path
from typing import Any, ClassVar

from anydoc.core.errors import CorruptFileError
from anydoc.core.model import Capability, Metadata, Part
from anydoc.core.registry import FormatHandler
from anydoc.formats.csv.parse import (
    column_types,
    convert_value,
    decode_bytes,
    looks_like_header,
    parse_rows,
    sniff_delimiter,
)


class CsvDocument:
    """A delimited-text table (CSV or TSV) with detected encoding and delimiter."""

    def __init__(
        self,
        rows: list[list[str]],
        *,
        format: str = "csv",  # noqa: A002
        encoding: str = "utf-8",
        delimiter: str = ",",
        has_bom: bool = False,
        has_header: bool = True,
        path: Path | None = None,
    ) -> None:
        self.path = path
        self.format = format
        self.encoding = encoding
        self.delimiter = delimiter
        self.has_bom = has_bom
        self.has_header = has_header
        self.header: list[str] = rows[0] if rows and has_header else []
        self.rows: list[list[str]] = rows[1:] if rows and has_header else rows
        self.metadata = self._build_metadata()

    @property
    def column_count(self) -> int:
        """Number of columns (widest row, including the header)."""
        widths = [len(r) for r in self.rows] + [len(self.header)]
        return max(widths, default=0)

    @property
    def column_types(self) -> list[str]:
        """Inferred type name per column."""
        return column_types(self.rows, self.column_count)

    def typed_rows(self) -> list[list[Any]]:
        """Return data rows converted to Python values according to column types."""
        types = self.column_types
        return [[convert_value(c, types[i]) for i, c in enumerate(r)] for r in self.rows]

    def records(self) -> list[dict[str, Any]]:
        """Return typed rows as dicts keyed by header (or ``col1``, ``col2``, ...)."""
        names = self.header or [f"col{i + 1}" for i in range(self.column_count)]
        return [dict(zip(names, r, strict=False)) for r in self.typed_rows()]

    def _build_metadata(self) -> Metadata:
        mime = "text/tab-separated-values" if self.format == "tsv" else "text/csv"
        return Metadata(
            format=self.format,
            mime=mime,
            page_count=1,
            extra={
                "encoding": self.encoding,
                "delimiter": self.delimiter,
                "has_bom": self.has_bom,
                "has_header": self.has_header,
                "row_count": len(self.rows),
                "column_count": self.column_count,
                "columns": self.header,
                "column_types": self.column_types,
            },
        )

    def parts(self) -> list[Part]:
        """Return a single table part sized ``(columns, rows)``."""
        return [Part(kind="table", index=0, name=None, size=(self.column_count, len(self.rows)))]

    def text(self) -> str:
        """Return all rows (header first) as tab-separated lines."""
        all_rows = ([self.header] if self.has_header else []) + self.rows
        return "\n".join("\t".join(r) for r in all_rows)

    def to_bytes(self, *, encoding: str | None = None, delimiter: str | None = None) -> bytes:
        """Serialize the table; defaults reproduce the detected encoding/delimiter/BOM."""
        enc = encoding or self.encoding
        delim = delimiter or self.delimiter
        buf = io.StringIO(newline="")
        writer = csv.writer(buf, delimiter=delim, lineterminator="\n")
        if self.has_header:
            writer.writerow(self.header)
        writer.writerows(self.rows)
        data = buf.getvalue().encode(enc)
        if encoding is None and self.has_bom and enc == "utf-8":
            data = b"\xef\xbb\xbf" + data
        return data

    def save(
        self,
        path: Path | None = None,
        *,
        encoding: str | None = None,
        delimiter: str | None = None,
    ) -> Path:
        """Write the table to ``path`` (default: the source path) and return it."""
        target = path or self.path
        if target is None:
            raise ValueError("No path given and document has no source path")
        target.write_bytes(self.to_bytes(encoding=encoding, delimiter=delimiter))
        return target

    def close(self) -> None:
        """No resources to release."""


class CsvHandler(FormatHandler):
    """Handler for comma-separated (and other delimited) text files."""

    format_id: ClassVar[str] = "csv"
    extensions: ClassVar[tuple[str, ...]] = ("csv",)
    mime_types: ClassVar[tuple[str, ...]] = ("text/csv",)
    capabilities: ClassVar[frozenset[Capability]] = frozenset(
        {Capability.READ, Capability.WRITE, Capability.EXTRACT_TEXT, Capability.EDIT}
    )
    default_delimiter: ClassVar[str] = ","

    @classmethod
    def sniff(cls, head: bytes, path: Path | None) -> float:
        """Return confidence from the file extension (content is ambiguous)."""
        if path is not None and path.suffix.lower().lstrip(".") in cls.extensions:
            return 0.9
        return 0.0

    def open(self, source: Path | bytes) -> CsvDocument:
        """Open a CSV/TSV file or raw bytes, auto-detecting encoding, delimiter and header."""
        if isinstance(source, Path):
            try:
                data = source.read_bytes()
            except OSError as exc:
                raise CorruptFileError(f"Cannot read {source}: {exc}") from exc
            path: Path | None = source
        else:
            data, path = source, None
        text, encoding, has_bom = decode_bytes(data)
        delimiter = sniff_delimiter(text, self.default_delimiter)
        rows = parse_rows(text, delimiter)
        return CsvDocument(
            rows,
            format=self.format_id,
            encoding=encoding,
            delimiter=delimiter,
            has_bom=has_bom,
            has_header=looks_like_header(rows),
            path=path,
        )

    def create(self) -> CsvDocument:
        """Create an empty table."""
        return CsvDocument(
            [], format=self.format_id, delimiter=self.default_delimiter, has_header=False
        )


class TsvHandler(CsvHandler):
    """Handler for tab-separated files."""

    format_id: ClassVar[str] = "tsv"
    extensions: ClassVar[tuple[str, ...]] = ("tsv", "tab")
    mime_types: ClassVar[tuple[str, ...]] = ("text/tab-separated-values",)
    default_delimiter: ClassVar[str] = "\t"
