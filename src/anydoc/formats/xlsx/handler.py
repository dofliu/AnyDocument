"""Read-oriented XLSX document and handler built on openpyxl."""

from __future__ import annotations

import io
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any, ClassVar

from openpyxl import Workbook, load_workbook
from openpyxl.cell.cell import Cell
from openpyxl.worksheet.worksheet import Worksheet

from anydoc.core.errors import CorruptFileError
from anydoc.core.model import Capability, Metadata, Part
from anydoc.core.registry import FormatHandler
from anydoc.formats.xlsx import data as _data
from anydoc.formats.xlsx import edit as _edit
from anydoc.formats.xlsx import style as _style
from anydoc.formats.xlsx.formulas import FormulaEngine

_XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


@dataclass(frozen=True)
class SheetInfo:
    """A worksheet: ``dimensions`` is the used range (e.g. ``A1:E4``) or ``None`` if empty."""

    index: int
    name: str
    state: str
    dimensions: str | None
    max_row: int
    max_column: int
    merged_ranges: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class CellInfo:
    """A non-empty cell.

    ``type`` is one of ``string``, ``number``, ``boolean``, ``date``, ``formula``, ``error``.
    For formulas ``formula`` holds the expression and ``value`` the cached result (or ``None``
    if the file carries no cached value); otherwise ``formula`` is ``None``.
    """

    coordinate: str
    row: int
    column: int
    value: Any
    type: str
    formula: str | None = None
    number_format: str = "General"
    bold: bool = False
    italic: bool = False
    font_name: str | None = None
    font_size: float | None = None
    fill_color: str | None = None


@dataclass(frozen=True)
class DefinedNameInfo:
    """A defined name; ``scope`` is the sheet name or ``None`` for workbook scope."""

    name: str
    value: str
    scope: str | None = None


def _cell_type(cell: Cell) -> str:
    if cell.data_type == "f":
        return "formula"
    if cell.data_type == "e":
        return "error"
    if cell.data_type == "b":
        return "boolean"
    if isinstance(cell.value, datetime | date | time | timedelta):
        return "date"
    if cell.data_type == "n":
        return "number"
    return "string"


def _color(color: Any) -> str | None:
    rgb = getattr(color, "rgb", None)
    return rgb if isinstance(rgb, str) else None


def _fill_color(cell: Cell) -> str | None:
    fill = cell.fill
    if fill is None or fill.fill_type != "solid":
        return None
    return _color(fill.fgColor)


def _text_of(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, datetime | date | time):
        return value.isoformat()
    return str(value)


class XlsxDocument:
    """An opened XLSX workbook; ``save`` rewrites it via openpyxl (formulas preserved)."""

    format = "xlsx"

    def __init__(self, wb: Any, cached: Any, *, path: Path | None = None) -> None:
        self._wb = wb
        self._cached = cached
        self.path = path
        self._dirty = False
        self._calc: dict[str, dict[str, Any]] | None = None
        self.metadata = self._build_metadata()

    @property
    def native(self) -> Any:
        """The underlying ``openpyxl.Workbook`` (formulas, not cached values)."""
        return self._wb

    # -- structure ---------------------------------------------------------

    def _sheet(self, sheet: int | str) -> Worksheet:
        return _edit.get_sheet(self._wb, sheet)

    def _cached_sheet(self, ws: Worksheet) -> Worksheet:
        if self._dirty:
            # Re-derive the cached-values view from the edited workbook (new formulas
            # have no cached result until a spreadsheet application recalculates them).
            self._cached.close()
            self._cached = load_workbook(io.BytesIO(self.to_bytes()), data_only=True)
            self._dirty = False
        cached: Worksheet = self._cached[ws.title]
        return cached

    # -- editing (T060) ----------------------------------------------------

    def _touch(self) -> None:
        self._dirty = True
        self._calc = None
        self.metadata = self._build_metadata()

    # -- formula calculation (T061) ----------------------------------------

    def calculate(self) -> dict[str, dict[str, Any]]:
        """Evaluate every formula with the built-in engine.

        Returns ``{sheet name: {coordinate: value}}`` for formula cells; errors appear as
        text such as ``"#DIV/0!"``.  The results are also used by ``cells()``/``rows()``
        for formulas that carry no cached value (e.g. newly written ones).
        """
        engine = FormulaEngine(self._wb)
        self._calc = engine.calculate()
        return {sheet: dict(values) for sheet, values in self._calc.items()}

    def evaluate(self, formula: str, sheet: int | str = 0) -> Any:
        """Evaluate formula text such as ``"=SUM(A1:A3)"`` in the context of a sheet."""
        return FormulaEngine(self._wb).evaluate(formula, self._sheet(sheet).title)

    def _computed(self, ws: Worksheet, coordinate: str, cached: Any) -> Any:
        """Cached value, or (after ``calculate()``) the engine's result when none is cached."""
        if cached is not None or self._calc is None:
            return cached
        return self._calc.get(ws.title, {}).get(coordinate)

    def set_cell(self, sheet: int | str, coordinate: str, value: Any) -> None:
        """Write a cell (``=...`` strings become formulas; ``None`` clears the value)."""
        _edit.set_cell(self._sheet(sheet), coordinate, value)
        self._touch()

    def set_range(self, sheet: int | str, top_left: str, rows: Sequence[Sequence[Any]]) -> str:
        """Write a 2-D block starting at ``top_left``; return the range written."""
        result = _edit.set_range(self._sheet(sheet), top_left, rows)
        self._touch()
        return result

    def clear_range(self, sheet: int | str, cell_range: str) -> int:
        """Clear values in a cell or range; return how many non-empty cells were cleared."""
        n = _edit.clear_range(self._sheet(sheet), cell_range)
        self._touch()
        return n

    def add_sheet(self, title: str, index: int | None = None) -> int:
        """Add an empty sheet and return its index."""
        ws = _edit.add_sheet(self._wb, title, index)
        self._touch()
        return int(self._wb.worksheets.index(ws))

    def delete_sheet(self, sheet: int | str) -> None:
        """Delete a sheet (the last remaining sheet cannot be deleted)."""
        _edit.delete_sheet(self._wb, sheet)
        self._touch()

    def rename_sheet(self, sheet: int | str, title: str) -> None:
        """Rename a sheet, updating formula references to it."""
        _edit.rename_sheet(self._wb, sheet, title)
        self._touch()

    def copy_sheet(self, sheet: int | str, title: str) -> int:
        """Duplicate a sheet right after the source and return the new index."""
        ws = _edit.copy_sheet(self._wb, sheet, title)
        self._touch()
        return int(self._wb.worksheets.index(ws))

    def move_sheet(self, sheet: int | str, index: int) -> None:
        """Move a sheet to a 0-based position."""
        _edit.move_sheet(self._wb, sheet, index)
        self._touch()

    def insert_rows(self, sheet: int | str, at: int, amount: int = 1) -> None:
        """Insert blank rows before 1-based row ``at``."""
        _edit.insert_rows(self._sheet(sheet), at, amount)
        self._touch()

    def delete_rows(self, sheet: int | str, at: int, amount: int = 1) -> None:
        """Delete rows starting at 1-based row ``at``."""
        _edit.delete_rows(self._sheet(sheet), at, amount)
        self._touch()

    def insert_cols(self, sheet: int | str, at: int, amount: int = 1) -> None:
        """Insert blank columns before 1-based column ``at``."""
        _edit.insert_cols(self._sheet(sheet), at, amount)
        self._touch()

    def delete_cols(self, sheet: int | str, at: int, amount: int = 1) -> None:
        """Delete columns starting at 1-based column ``at``."""
        _edit.delete_cols(self._sheet(sheet), at, amount)
        self._touch()

    # -- formatting and layout (T062) --------------------------------------

    def set_number_format(self, sheet: int | str, cell_range: str, fmt: str) -> int:
        """Apply a number format (preset such as ``percent`` or a code) to a range."""
        n = _style.set_number_format(self._sheet(sheet), cell_range, fmt)
        self._touch()
        return n

    def set_font(self, sheet: int | str, cell_range: str, **options: Any) -> int:
        """Change font attributes (name, size, bold, italic, underline, strike, color)."""
        n = _style.set_font(self._sheet(sheet), cell_range, **options)
        self._touch()
        return n

    def set_fill(self, sheet: int | str, cell_range: str, color: str | None) -> int:
        """Set a solid fill color (``None`` removes it)."""
        n = _style.set_fill(self._sheet(sheet), cell_range, color)
        self._touch()
        return n

    def set_alignment(self, sheet: int | str, cell_range: str, **options: Any) -> int:
        """Change alignment (horizontal, vertical, wrap, indent, rotation, shrink)."""
        n = _style.set_alignment(self._sheet(sheet), cell_range, **options)
        self._touch()
        return n

    def set_border(
        self, sheet: int | str, cell_range: str, style: str | None = "thin", **options: Any
    ) -> int:
        """Set borders (``color``, ``sides``, ``outline`` options); ``style=None`` removes."""
        n = _style.set_border(self._sheet(sheet), cell_range, style, **options)
        self._touch()
        return n

    def set_column_width(self, sheet: int | str, column: str | int, width: float) -> None:
        """Set a column width in characters."""
        _style.set_column_width(self._sheet(sheet), column, width)
        self._touch()

    def set_row_height(self, sheet: int | str, row: int, height: float) -> None:
        """Set a row height in points."""
        _style.set_row_height(self._sheet(sheet), row, height)
        self._touch()

    def auto_fit_columns(self, sheet: int | str = 0) -> dict[str, float]:
        """Size columns to their content; return ``{column letter: width}``."""
        widths = _style.auto_fit_columns(self._sheet(sheet))
        self._touch()
        return widths

    def freeze_panes(self, sheet: int | str, cell: str | None) -> None:
        """Freeze rows above / columns left of ``cell`` (``None`` unfreezes)."""
        _style.freeze_panes(self._sheet(sheet), cell)
        self._touch()

    def set_hidden(
        self,
        sheet: int | str,
        *,
        rows: tuple[int, int] | None = None,
        cols: tuple[str, str] | None = None,
        hidden: bool = True,
    ) -> None:
        """Hide or unhide a row span and/or column span."""
        _style.set_hidden(self._sheet(sheet), rows=rows, cols=cols, hidden=hidden)
        self._touch()

    def set_sheet_state(self, sheet: int | str, state: str) -> None:
        """Set sheet visibility to ``visible``, ``hidden`` or ``veryHidden``."""
        ws = self._sheet(sheet)
        if state != "visible" and all(
            w.sheet_state != "visible" for w in self._wb.worksheets if w is not ws
        ):
            raise ValueError("At least one sheet must remain visible")
        _style.set_sheet_state(ws, state)
        self._touch()

    # -- sorting, filtering, duplicates (T063) -----------------------------

    def sort_range(
        self,
        sheet: int | str,
        cell_range: str,
        keys: Sequence[Any],
        *,
        has_header: bool = False,
        case_sensitive: bool = False,
    ) -> int:
        """Sort a range by column keys (``"B"`` or ``("B", False)`` for descending)."""
        n = _data.sort_range(
            self._sheet(sheet),
            cell_range,
            keys,
            has_header=has_header,
            case_sensitive=case_sensitive,
        )
        self._touch()
        return n

    def remove_duplicates(
        self,
        sheet: int | str,
        cell_range: str,
        columns: Sequence[str | int] | None = None,
        *,
        has_header: bool = True,
        case_sensitive: bool = False,
    ) -> int:
        """Remove duplicate rows from a range; return the number of rows removed."""
        n = _data.remove_duplicates(
            self._sheet(sheet),
            cell_range,
            columns,
            has_header=has_header,
            case_sensitive=case_sensitive,
        )
        self._touch()
        return n

    def set_auto_filter(
        self, sheet: int | str, cell_range: str, criteria: dict[str | int, Any] | None = None
    ) -> list[int]:
        """Install an AutoFilter, optionally filtering by values; return hidden row numbers."""
        hidden = _data.set_auto_filter(self._sheet(sheet), cell_range, criteria)
        self._touch()
        return hidden

    def clear_auto_filter(self, sheet: int | str) -> int:
        """Remove the AutoFilter and show the rows it hid."""
        n = _data.clear_auto_filter(self._sheet(sheet))
        self._touch()
        return n

    def auto_filter(self, sheet: int | str = 0) -> dict[str, Any] | None:
        """Return the sheet's AutoFilter description, or ``None``."""
        return _data.get_auto_filter(self._sheet(sheet))

    def cell_format(self, sheet: int | str, coordinate: str) -> dict[str, Any]:
        """Return number format, font, fill, border and alignment of one cell."""
        return _style.describe_cell(self._sheet(sheet), coordinate)

    def layout(self, sheet: int | str = 0) -> dict[str, Any]:
        """Return column widths, row heights, hidden rows/columns, freeze pane and state."""
        return _style.describe_layout(self._sheet(sheet))

    def to_bytes(self) -> bytes:
        """Serialize the workbook to XLSX bytes."""
        buf = io.BytesIO()
        self._wb.save(buf)
        return buf.getvalue()

    @staticmethod
    def _is_empty(ws: Worksheet) -> bool:
        return not any(c.value is not None for row in ws.iter_rows() for c in row)

    def sheets(self) -> list[SheetInfo]:
        """Return every worksheet with state, used range and merged ranges."""
        result: list[SheetInfo] = []
        for i, ws in enumerate(self._wb.worksheets):
            empty = self._is_empty(ws)
            result.append(
                SheetInfo(
                    index=i,
                    name=ws.title,
                    state=ws.sheet_state,
                    dimensions=None if empty else ws.dimensions,
                    max_row=0 if empty else ws.max_row,
                    max_column=0 if empty else ws.max_column,
                    merged_ranges=sorted(str(r) for r in ws.merged_cells.ranges),
                )
            )
        return result

    def cells(self, sheet: int | str = 0) -> list[CellInfo]:
        """Return non-empty cells of a sheet in row-major order, with formulas and styles."""
        ws = self._sheet(sheet)
        cached_ws = self._cached_sheet(ws)
        out: list[CellInfo] = []
        for row in ws.iter_rows():
            for cell in row:
                if cell.value is None:
                    continue
                kind = _cell_type(cell)
                formula = value = None
                if kind == "formula":
                    formula = str(cell.value)
                    value = self._computed(ws, cell.coordinate, cached_ws[cell.coordinate].value)
                else:
                    value = cell.value
                out.append(
                    CellInfo(
                        coordinate=cell.coordinate,
                        row=cell.row,
                        column=cell.column,
                        value=value,
                        type=kind,
                        formula=formula,
                        number_format=cell.number_format,
                        bold=bool(cell.font.b),
                        italic=bool(cell.font.i),
                        font_name=cell.font.name,
                        font_size=float(cell.font.sz) if cell.font.sz is not None else None,
                        fill_color=_fill_color(cell),
                    )
                )
        return out

    def rows(self, sheet: int | str = 0) -> list[list[Any]]:
        """Return the used range as rows of values (formulas replaced by cached values)."""
        src = self._sheet(sheet)
        ws = self._cached_sheet(src)
        if self._is_empty(ws) and self._is_empty(src):
            return []
        return [
            [
                self._computed(src, c.coordinate, c.value)
                if src[c.coordinate].data_type == "f"
                else c.value
                for c in row
            ]
            for row in ws.iter_rows()
        ]

    def merged_cells(self, sheet: int | str = 0) -> list[str]:
        """Return merged ranges (e.g. ``A1:B2``) of a sheet."""
        return sorted(str(r) for r in self._sheet(sheet).merged_cells.ranges)

    def style_summary(self, sheet: int | str = 0) -> dict[str, Any]:
        """Summarize styling: number formats, fonts, fills and bold/italic counts."""
        formats: dict[str, int] = {}
        fonts: dict[str, int] = {}
        fills: dict[str, int] = {}
        bold = italic = 0
        for c in self.cells(sheet):
            formats[c.number_format] = formats.get(c.number_format, 0) + 1
            if c.font_name:
                fonts[c.font_name] = fonts.get(c.font_name, 0) + 1
            if c.fill_color:
                fills[c.fill_color] = fills.get(c.fill_color, 0) + 1
            bold += c.bold
            italic += c.italic
        return {
            "number_formats": formats,
            "fonts": fonts,
            "fills": fills,
            "bold_cells": bold,
            "italic_cells": italic,
        }

    def defined_names(self) -> list[DefinedNameInfo]:
        """Return workbook- and sheet-scoped defined names."""
        names = [DefinedNameInfo(n, d.attr_text or "") for n, d in self._wb.defined_names.items()]
        for ws in self._wb.worksheets:
            names += [
                DefinedNameInfo(n, d.attr_text or "", ws.title) for n, d in ws.defined_names.items()
            ]
        return names

    # -- Document protocol -------------------------------------------------

    def _build_metadata(self) -> Metadata:
        props = self._wb.properties
        sheets = self.sheets()
        formula_count = sum(
            1 for ws in self._wb.worksheets for r in ws.iter_rows() for c in r if c.data_type == "f"
        )
        return Metadata(
            format="xlsx",
            mime=_XLSX_MIME,
            title=props.title or None,
            author=props.creator or None,
            created=props.created,
            modified=props.modified,
            page_count=None,
            extra={
                "subject": props.subject or None,
                "keywords": props.keywords or None,
                "last_modified_by": props.lastModifiedBy or None,
                "sheet_count": len(sheets),
                "sheet_names": [s.name for s in sheets],
                "formula_count": formula_count,
                "defined_names": [n.name for n in self.defined_names()],
            },
        )

    def parts(self) -> list[Part]:
        """Return one part per worksheet."""
        return [Part(kind="sheet", index=s.index, name=s.name) for s in self.sheets()]

    def text(self) -> str:
        """Return each sheet as its name followed by tab-separated rows (cached values)."""
        blocks: list[str] = []
        for s in self.sheets():
            lines = ["\t".join(_text_of(v) for v in row) for row in self.rows(s.index)]
            blocks.append("\n".join([s.name, *lines]))
        return "\n\n".join(blocks)

    def save(self, path: Path | None = None) -> Path:
        """Write the workbook to ``path`` (default: the source path) and return it."""
        target = path or self.path
        if target is None:
            raise ValueError("No path given and document has no source path")
        self._wb.save(str(target))
        return target

    def close(self) -> None:
        """Close the underlying workbooks."""
        self._wb.close()
        self._cached.close()


def _blank_bytes(wb: Workbook) -> bytes:
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


class XlsxHandler(FormatHandler):
    """Handler for Excel ``.xlsx`` workbooks."""

    format_id: ClassVar[str] = "xlsx"
    extensions: ClassVar[tuple[str, ...]] = ("xlsx",)
    mime_types: ClassVar[tuple[str, ...]] = (_XLSX_MIME,)
    capabilities: ClassVar[frozenset[Capability]] = frozenset(
        {Capability.READ, Capability.WRITE, Capability.EDIT, Capability.EXTRACT_TEXT}
    )

    @classmethod
    def sniff(cls, head: bytes, path: Path | None) -> float:
        """Return confidence from the extension (content detection lives in ``detect``)."""
        if path is not None and path.suffix.lower() == ".xlsx":
            return 0.9
        return 0.0

    def create(self) -> XlsxDocument:
        """Create a new workbook with one empty sheet (use ``save(path)`` to write it)."""
        wb = Workbook()
        cached = load_workbook(io.BytesIO(_blank_bytes(wb)), data_only=True)
        return XlsxDocument(wb, cached)

    def open(self, source: Path | bytes) -> XlsxDocument:
        """Open an XLSX from a path or bytes; invalid packages raise ``CorruptFileError``."""
        path = source if isinstance(source, Path) else None
        if isinstance(source, Path):
            try:
                data = source.read_bytes()
            except OSError as exc:
                raise CorruptFileError(f"Cannot read {source}: {exc}") from exc
        else:
            data = source
        try:
            wb = load_workbook(io.BytesIO(data))
            cached = load_workbook(io.BytesIO(data), data_only=True)
            return XlsxDocument(wb, cached, path=path)
        except Exception as exc:  # openpyxl raises many types for bad packages
            raise CorruptFileError(f"Invalid XLSX: {exc}") from exc
