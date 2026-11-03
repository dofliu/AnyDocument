"""PDF table extraction built on pdfplumber.

Functions take PDF ``bytes``. Page indexes are 0-based. Cells are returned as ``str`` (empty
string for blank cells); multi-line cell text is joined with a single space.
"""

from __future__ import annotations

import io
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

import pdfplumber

from anydoc.core.errors import CorruptFileError
from anydoc.formats.pdf.pages import _reader

__all__ = ["PdfTable", "extract_tables"]

_STRATEGIES = {
    "lines": {"vertical_strategy": "lines", "horizontal_strategy": "lines"},
    "text": {"vertical_strategy": "text", "horizontal_strategy": "text"},
}


@dataclass(frozen=True)
class PdfTable:
    """A table found on a page.

    ``page`` is the 0-based page index, ``index`` the 0-based position of the table on that
    page, and ``bbox`` is ``(x0, top, x1, bottom)`` in PDF points measured from the top-left.
    """

    page: int
    index: int
    bbox: tuple[float, float, float, float]
    rows: list[list[str]] = field(default_factory=list)

    @property
    def row_count(self) -> int:
        """Number of rows."""
        return len(self.rows)

    @property
    def column_count(self) -> int:
        """Width of the widest row."""
        return max((len(r) for r in self.rows), default=0)

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serialisable dict."""
        return {
            "page": self.page,
            "index": self.index,
            "bbox": list(self.bbox),
            "rows": self.rows,
        }


def _clean(cell: Any) -> str:
    if cell is None:
        return ""
    return " ".join(str(cell).split())


def _normalise(raw: list[list[Any]]) -> list[list[str]]:
    rows = [[_clean(c) for c in row] for row in raw]
    width = max((len(r) for r in rows), default=0)
    return [r + [""] * (width - len(r)) for r in rows]


def extract_tables(
    data: bytes,
    pages: Iterable[int] | None = None,
    *,
    strategy: str = "lines",
    password: str = "",
    min_rows: int = 1,
    min_columns: int = 2,
) -> list[PdfTable]:
    """Extract tables from a PDF.

    Args:
        data: PDF bytes.
        pages: 0-based page indexes to scan (default: all pages).
        strategy: ``"lines"`` detects ruled tables from drawn lines/rectangles; ``"text"``
            infers columns from text alignment (for tables without borders).
        password: Password for encrypted PDFs.
        min_rows: Ignore tables with fewer rows.
        min_columns: Ignore tables with fewer columns.

    Raises:
        ValueError: Unknown strategy.
        IndexError: A requested page does not exist.
        OperationNotSupportedError: Encrypted PDF without a valid password.
        CorruptFileError: The data is not a readable PDF.
    """
    if strategy not in _STRATEGIES:
        raise ValueError(f"Unknown strategy {strategy!r}; use one of {sorted(_STRATEGIES)}")
    _reader(data, password)  # validates the PDF and the password
    try:
        pdf = pdfplumber.open(io.BytesIO(data), password=password or None)
    except Exception as exc:
        raise CorruptFileError(f"Invalid PDF: {exc}") from exc
    out: list[PdfTable] = []
    try:
        count = len(pdf.pages)
        wanted = list(range(count)) if pages is None else list(pages)
        for i in wanted:
            if not 0 <= i < count:
                raise IndexError(f"No such page: {i} (document has {count} pages)")
        settings = dict(_STRATEGIES[strategy])
        for i in wanted:
            page = pdf.pages[i]
            try:
                found = page.find_tables(table_settings=settings)
            except Exception as exc:
                raise CorruptFileError(f"Cannot extract tables from page {i + 1}: {exc}") from exc
            n = 0
            for t in found:
                rows = _normalise(t.extract())
                if len(rows) < min_rows or (rows and len(rows[0]) < min_columns):
                    continue
                b = t.bbox
                out.append(
                    PdfTable(
                        page=i,
                        index=n,
                        bbox=(float(b[0]), float(b[1]), float(b[2]), float(b[3])),
                        rows=rows,
                    )
                )
                n += 1
    finally:
        pdf.close()
    return out
