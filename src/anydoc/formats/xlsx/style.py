"""XLSX formatting and layout primitives operating on an ``openpyxl.Workbook`` (T062)."""

from __future__ import annotations

import re
from collections.abc import Iterator
from typing import Any, cast

from openpyxl.cell.cell import Cell
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter
from openpyxl.utils.cell import column_index_from_string, coordinate_from_string
from openpyxl.worksheet.worksheet import Worksheet

NUMBER_FORMATS: dict[str, str] = {
    "general": "General",
    "integer": "0",
    "number": "0.00",
    "thousands": "#,##0",
    "thousands_decimal": "#,##0.00",
    "percent": "0%",
    "percent_decimal": "0.00%",
    "currency": '"$"#,##0.00',
    "scientific": "0.00E+00",
    "date": "yyyy-mm-dd",
    "datetime": "yyyy-mm-dd hh:mm:ss",
    "time": "hh:mm:ss",
    "text": "@",
}

BORDER_STYLES = frozenset(
    {
        "thin",
        "medium",
        "thick",
        "dashed",
        "dotted",
        "double",
        "hair",
        "mediumDashed",
        "dashDot",
        "mediumDashDot",
        "dashDotDot",
        "mediumDashDotDot",
        "slantDashDot",
    }
)
_SIDES = ("left", "right", "top", "bottom")
_HALIGN = frozenset({"general", "left", "center", "right", "fill", "justify", "centerContinuous"})
_VALIGN = frozenset({"top", "center", "bottom", "justify", "distributed"})
_COLOR_NAMES = {
    "black": "000000",
    "white": "FFFFFF",
    "red": "FF0000",
    "green": "00B050",
    "blue": "0000FF",
    "yellow": "FFFF00",
    "orange": "FFA500",
    "gray": "808080",
    "grey": "808080",
    "purple": "7030A0",
    "lightgray": "D9D9D9",
}
_REF = re.compile(r"[A-Za-z]{1,3}\d+")


def color_hex(color: str) -> str:
    """Normalize ``#RRGGBB``/``RRGGBB``/``AARRGGBB``/a basic name to an 8-digit ARGB string."""
    raw = color.strip().lstrip("#")
    raw = _COLOR_NAMES.get(raw.lower(), raw)
    if not re.fullmatch(r"[0-9A-Fa-f]{6}([0-9A-Fa-f]{2})?", raw):
        raise ValueError(f"Invalid color: {color!r}")
    return raw.upper() if len(raw) == 8 else "FF" + raw.upper()


def _rgb(argb: Any) -> str | None:
    value = getattr(argb, "rgb", None)
    return value[2:] if isinstance(value, str) and len(value) == 8 else None


def _split(ref: str) -> tuple[int, int]:
    if re.fullmatch(r"[A-Za-z]{1,3}0+", ref):
        raise ValueError(f"Invalid cell reference: {ref!r}")
    col, row = coordinate_from_string(ref.upper())
    if row < 1:
        raise ValueError(f"Invalid cell reference: {ref!r}")
    return column_index_from_string(col), row


def iter_cells(ws: Worksheet, ref: str) -> Iterator[Cell]:
    """Yield the cells of ``A1``, ``A1:C3``, a column span ``A:C`` or a row span ``2:4``."""
    text = ref.strip().upper().replace("$", "")
    lo, sep, hi = text.partition(":")
    if sep and not hi:
        raise ValueError(f"Invalid range: {ref!r}")
    hi = hi or lo
    if _REF.fullmatch(lo) and _REF.fullmatch(hi):
        (c1, r1), (c2, r2) = _split(lo), _split(hi)
    elif lo.isalpha() and hi.isalpha() and len(lo) <= 3 and len(hi) <= 3:
        c1, c2 = column_index_from_string(lo), column_index_from_string(hi)
        r1, r2 = 1, max(ws.max_row, 1)
    elif lo.isdigit() and hi.isdigit():
        r1, r2 = int(lo), int(hi)
        c1, c2 = 1, max(ws.max_column, 1)
    else:
        raise ValueError(f"Invalid range: {ref!r}")
    c1, c2, r1, r2 = min(c1, c2), max(c1, c2), min(r1, r2), max(r1, r2)
    if r1 < 1:
        raise ValueError(f"Invalid range: {ref!r}")
    for r in range(r1, r2 + 1):
        for c in range(c1, c2 + 1):
            yield cast(Cell, ws.cell(row=r, column=c))


def set_number_format(ws: Worksheet, ref: str, fmt: str) -> int:
    """Apply a number format (preset name such as ``percent`` or a raw code); return cell count."""
    code = NUMBER_FORMATS.get(fmt.lower(), fmt)
    n = 0
    for cell in iter_cells(ws, ref):
        cell.number_format = code
        n += 1
    return n


def set_font(
    ws: Worksheet,
    ref: str,
    *,
    name: str | None = None,
    size: float | None = None,
    bold: bool | None = None,
    italic: bool | None = None,
    underline: bool | str | None = None,
    strike: bool | None = None,
    color: str | None = None,
) -> int:
    """Change font attributes of a range; ``None`` arguments keep the current value."""
    argb = color_hex(color) if color is not None else None
    keep_underline = underline is None
    new_underline: Any = "single" if underline is True else (underline or None)
    n = 0
    for cell in iter_cells(ws, ref):
        f = cell.font
        cell.font = Font(
            name=name or f.name,
            size=size if size is not None else f.size,
            bold=f.bold if bold is None else bold,
            italic=f.italic if italic is None else italic,
            underline=f.underline if keep_underline else new_underline,
            strike=f.strike if strike is None else strike,
            color=argb if argb is not None else f.color,
            vertAlign=f.vertAlign,
            charset=f.charset,
            family=f.family,
            scheme=None if name else f.scheme,
        )
        n += 1
    return n


def set_fill(ws: Worksheet, ref: str, color: str | None) -> int:
    """Set a solid background color (``None`` removes the fill)."""
    fill = PatternFill(fill_type=None)
    if color is not None:
        argb = color_hex(color)
        fill = PatternFill(fill_type="solid", start_color=argb, end_color=argb)
    n = 0
    for cell in iter_cells(ws, ref):
        cell.fill = fill
        n += 1
    return n


def set_alignment(
    ws: Worksheet,
    ref: str,
    *,
    horizontal: str | None = None,
    vertical: str | None = None,
    wrap: bool | None = None,
    indent: int | None = None,
    rotation: int | None = None,
    shrink: bool | None = None,
) -> int:
    """Change alignment of a range; ``None`` arguments keep the current value."""
    if horizontal is not None and horizontal not in _HALIGN:
        raise ValueError(f"Invalid horizontal alignment: {horizontal!r}")
    if vertical is not None and vertical not in _VALIGN:
        raise ValueError(f"Invalid vertical alignment: {vertical!r}")
    if rotation is not None and not (-90 <= rotation <= 90 or rotation == 255):
        raise ValueError("rotation must be between -90 and 90 degrees")
    if indent is not None and indent < 0:
        raise ValueError("indent must be >= 0")
    n = 0
    for cell in iter_cells(ws, ref):
        a = cell.alignment
        cell.alignment = Alignment(
            horizontal=horizontal or a.horizontal,
            vertical=vertical or a.vertical,
            wrap_text=a.wrap_text if wrap is None else wrap,
            indent=a.indent if indent is None else indent,
            text_rotation=a.text_rotation if rotation is None else rotation,
            shrink_to_fit=a.shrink_to_fit if shrink is None else shrink,
        )
        n += 1
    return n


def set_border(
    ws: Worksheet,
    ref: str,
    style: str | None = "thin",
    *,
    color: str = "000000",
    sides: tuple[str, ...] = _SIDES,
    outline: bool = False,
) -> int:
    """Set borders on ``sides`` of every cell, or only around the range when ``outline``.

    ``style=None`` removes the selected borders.
    """
    if style is not None and style not in BORDER_STYLES:
        raise ValueError(f"Invalid border style: {style!r}")
    bad = [s for s in sides if s not in _SIDES]
    if bad:
        raise ValueError(f"Invalid border side(s): {bad}")
    side = Side(style=cast(Any, style), color=color_hex(color) if style else None)
    cells = list(iter_cells(ws, ref))
    rows = [c.row for c in cells]
    cols = [c.column for c in cells]
    edges = {"top": min(rows), "bottom": max(rows), "left": min(cols), "right": max(cols)}
    for cell in cells:
        b = cell.border
        new = {s: getattr(b, s) for s in _SIDES}
        for s in sides:
            if outline:
                pos = cell.row if s in ("top", "bottom") else cell.column
                if pos != edges[s]:
                    continue
            new[s] = side
        cell.border = Border(**new)
    return len(cells)


def set_column_width(ws: Worksheet, column: str | int, width: float) -> None:
    """Set a column width in characters (letter or 1-based index)."""
    if width < 0 or width > 255:
        raise ValueError("Column width must be within 0..255")
    letter = get_column_letter(column) if isinstance(column, int) else column.upper()
    ws.column_dimensions[letter].width = width


def set_row_height(ws: Worksheet, row: int, height: float) -> None:
    """Set a row height in points."""
    if row < 1 or height < 0 or height > 409:
        raise ValueError("Row must be >= 1 and height within 0..409")
    ws.row_dimensions[row].height = height


def auto_fit_columns(
    ws: Worksheet, min_width: float = 8, max_width: float = 60
) -> dict[str, float]:
    """Estimate column widths from cell text (CJK characters count double)."""
    widths: dict[str, float] = {}
    for col in ws.iter_cols():
        best = 0.0
        for cell in col:
            if cell.value is None:
                continue
            text = str(cell.value)
            best = max(best, sum(2.0 if ord(ch) > 0x2E80 else 1.0 for ch in text) + 2)
        if best:
            letter = get_column_letter(cast(int, col[0].column))
            widths[letter] = round(min(max(best, min_width), max_width), 1)
            ws.column_dimensions[letter].width = widths[letter]
    return widths


def freeze_panes(ws: Worksheet, cell: str | None) -> None:
    """Freeze rows above and columns left of ``cell`` (``None``/``"A1"`` unfreezes)."""
    if cell is None or cell.strip().upper() in ("A1", ""):
        ws.freeze_panes = None
        return
    ref = cell.strip().upper().replace("$", "")
    if not _REF.fullmatch(ref):
        raise ValueError(f"Invalid cell reference: {cell!r}")
    ws.freeze_panes = ref


def set_hidden(
    ws: Worksheet,
    *,
    rows: tuple[int, int] | None = None,
    cols: tuple[str, str] | None = None,
    hidden: bool = True,
) -> None:
    """Hide/unhide a 1-based row span ``(first, last)`` and/or a column span ``("B", "D")``."""
    if rows is not None:
        first, last = rows
        if first < 1 or last < first:
            raise ValueError(f"Invalid row span: {rows}")
        for r in range(first, last + 1):
            ws.row_dimensions[r].hidden = hidden
    if cols is not None:
        c1, c2 = (column_index_from_string(c.upper()) for c in cols)
        if c2 < c1:
            raise ValueError(f"Invalid column span: {cols}")
        for c in range(c1, c2 + 1):
            ws.column_dimensions[get_column_letter(c)].hidden = hidden


def set_sheet_state(ws: Worksheet, state: str) -> None:
    """Set sheet visibility: ``visible``, ``hidden`` or ``veryHidden``."""
    if state not in ("visible", "hidden", "veryHidden"):
        raise ValueError(f"Invalid sheet state: {state!r}")
    ws.sheet_state = cast(Any, state)


def describe_cell(ws: Worksheet, coordinate: str) -> dict[str, Any]:
    """Return the formatting of one cell as a plain dict."""
    cell = ws[coordinate.upper().replace("$", "")]
    fill = cell.fill
    b = cell.border
    a = cell.alignment
    return {
        "number_format": cell.number_format,
        "font": {
            "name": cell.font.name,
            "size": cell.font.size,
            "bold": bool(cell.font.bold),
            "italic": bool(cell.font.italic),
            "underline": cell.font.underline,
            "strike": bool(cell.font.strike),
            "color": _rgb(cell.font.color),
        },
        "fill": _rgb(fill.start_color) if fill.fill_type == "solid" else None,
        "border": {s: getattr(b, s).style for s in _SIDES},
        "alignment": {
            "horizontal": a.horizontal,
            "vertical": a.vertical,
            "wrap": bool(a.wrap_text),
            "indent": a.indent,
            "rotation": a.text_rotation,
            "shrink": bool(a.shrink_to_fit),
        },
    }


def describe_layout(ws: Worksheet) -> dict[str, Any]:
    """Return widths, heights, hidden rows/columns, freeze pane and sheet state."""
    return {
        "column_widths": {
            k: d.width for k, d in sorted(ws.column_dimensions.items()) if d.width is not None
        },
        "row_heights": {
            k: d.height for k, d in sorted(ws.row_dimensions.items()) if d.height is not None
        },
        "hidden_columns": sorted(
            c
            for k, d in ws.column_dimensions.items()
            if d.hidden
            for c in _expand_cols(k, d.min, d.max)
        ),
        "hidden_rows": sorted(k for k, d in ws.row_dimensions.items() if d.hidden),
        "freeze_panes": ws.freeze_panes,
        "state": ws.sheet_state,
    }


def _expand_cols(key: str, lo: int | None, hi: int | None) -> list[str]:
    lo = lo or column_index_from_string(key)
    hi = hi or lo
    return [get_column_letter(i) for i in range(lo, hi + 1)]
