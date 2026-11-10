"""XLSX chart creation and inspection on an ``openpyxl.Workbook`` (T064)."""

from __future__ import annotations

from typing import Any

from openpyxl.chart import (
    BarChart,
    LineChart,
    PieChart,
    Reference,
    ScatterChart,
    Series,
)
from openpyxl.chart.axis import ChartLines
from openpyxl.utils import get_column_letter
from openpyxl.utils.cell import range_boundaries
from openpyxl.worksheet.worksheet import Worksheet

CHART_KINDS = ("bar", "column", "line", "pie", "scatter")


def _chart_list(ws: Worksheet) -> list[Any]:
    """Return openpyxl's private per-sheet chart list."""
    charts: list[Any] = ws._charts  # type: ignore[attr-defined]
    return charts


def _bounds(ref: str) -> tuple[int, int, int, int]:
    text = ref.strip().upper().replace("$", "")
    if ":" not in text:
        text = f"{text}:{text}"
    try:
        bounds = range_boundaries(text)
    except (ValueError, TypeError):
        raise ValueError(f"Invalid range: {ref!r}") from None
    if any(b is None or b < 1 for b in bounds):
        raise ValueError(f"Range must be bounded (A1:C9): {ref!r}")
    c1, r1, c2, r2 = (int(b) for b in bounds if b is not None)
    return c1, r1, c2, r2


def _ref(ws: Worksheet, c1: int, r1: int, c2: int, r2: int) -> Reference:
    return Reference(ws, min_col=c1, min_row=r1, max_col=c2, max_row=r2)


def _set_title(obj: Any, text: str | None) -> None:
    if text:
        obj.title = text


def add_chart(
    ws: Worksheet,
    kind: str,
    data: str,
    *,
    categories: str | None = None,
    anchor: str = "H2",
    title: str | None = None,
    x_title: str | None = None,
    y_title: str | None = None,
    titles_from_data: bool = True,
    series_in_rows: bool = False,
    stacked: bool = False,
    width: float = 15.0,
    height: float = 7.5,
    legend: str | None = "r",
) -> int:
    """Add a chart of ``kind`` (bar/column/line/pie/scatter) and return its index.

    ``data`` is the series range (e.g. ``B1:C6``); with ``titles_from_data`` the first
    row (or first column when ``series_in_rows``) holds the series names. ``categories``
    is the label/X range. For ``scatter`` without ``categories`` the first data column
    (row) provides the X values and every other one a Y series.
    """
    kind = kind.lower()
    if kind not in CHART_KINDS:
        raise ValueError(f"Unknown chart kind {kind!r}; expected one of {CHART_KINDS}")
    if width <= 0 or height <= 0:
        raise ValueError("Chart width and height must be positive")
    c1, r1, c2, r2 = _bounds(data)
    if series_in_rows and kind == "pie":
        raise ValueError("Pie charts do not support series_in_rows")
    min_size = 2 if titles_from_data else 1
    span = (c2 - c1 + 1) if series_in_rows else (r2 - r1 + 1)
    if span < min_size:
        raise ValueError("Data range has no data points")

    chart: Any
    if kind == "scatter":
        chart = ScatterChart()
        chart.style = 13
        _fill_scatter(ws, chart, (c1, r1, c2, r2), categories, titles_from_data, series_in_rows)
    else:
        if kind in ("bar", "column"):
            chart = BarChart()
            chart.type = "bar" if kind == "bar" else "col"
            if stacked:
                chart.grouping = "stacked"
                chart.overlap = 100
        elif kind == "line":
            chart = LineChart()
        else:
            chart = PieChart()
        chart.add_data(
            _ref(ws, c1, r1, c2, r2),
            titles_from_data=titles_from_data,
            from_rows=series_in_rows,
        )
        if categories:
            k1, kr1, k2, kr2 = _bounds(categories)
            chart.set_categories(_ref(ws, k1, kr1, k2, kr2))

    _set_title(chart, title)
    if kind != "pie":
        _set_title(chart.x_axis, x_title)
        _set_title(chart.y_axis, y_title)
        # openpyxl >= 3.1 hides axes unless ``delete`` is cleared explicitly.
        chart.x_axis.delete = False
        chart.y_axis.delete = False
        chart.y_axis.majorGridlines = ChartLines()
    if legend is None:
        chart.legend = None
    else:
        if legend not in ("r", "l", "t", "b", "tr"):
            raise ValueError(f"Invalid legend position {legend!r}")
        chart.legend.position = legend
    chart.width = width
    chart.height = height
    ws.add_chart(chart, anchor.upper())
    return len(_chart_list(ws)) - 1


def _fill_scatter(
    ws: Worksheet,
    chart: ScatterChart,
    box: tuple[int, int, int, int],
    categories: str | None,
    titles_from_data: bool,
    series_in_rows: bool,
) -> None:
    c1, r1, c2, r2 = box
    if categories is not None:
        k1, kr1, k2, kr2 = _bounds(categories)
        xs = _ref(ws, k1, kr1, k2, kr2)
        lines = _lines(c1, r1, c2, r2, series_in_rows)
    else:
        if series_in_rows:
            xs = _ref(ws, c1 + (1 if titles_from_data else 0), r1, c2, r1)
            lines = _lines(c1, r1 + 1, c2, r2, True)
        else:
            xs = _ref(ws, c1, r1 + (1 if titles_from_data else 0), c1, r2)
            lines = _lines(c1 + 1, r1, c2, r2, False)
        if not lines:
            raise ValueError("Scatter chart needs at least one Y series")
    for lc1, lr1, lc2, lr2 in lines:
        ref = _ref(ws, lc1, lr1, lc2, lr2)
        series = Series(ref, xs, title_from_data=titles_from_data)
        series.marker.symbol = "circle"
        series.graphicalProperties.line.noFill = False
        chart.series.append(series)


def _lines(c1: int, r1: int, c2: int, r2: int, rows: bool) -> list[tuple[int, int, int, int]]:
    if rows:
        return [(c1, r, c2, r) for r in range(r1, r2 + 1)]
    return [(c, r1, c, r2) for c in range(c1, c2 + 1)]


_KIND_OF = {
    "BarChart": None,
    "LineChart": "line",
    "PieChart": "pie",
    "ScatterChart": "scatter",
}


def _text(title: Any) -> str | None:
    if title is None or title.tx is None or title.tx.rich is None:
        return None
    parts: list[str] = []
    for para in title.tx.rich.p:
        for run in para.r or []:
            parts.append(run.t)
    return "".join(parts) or None


def _anchor(chart: Any) -> str:
    marker = getattr(chart.anchor, "_from", None)
    if marker is None:
        return "A1"
    return f"{get_column_letter(marker.col + 1)}{marker.row + 1}"


def list_charts(ws: Worksheet) -> list[dict[str, Any]]:
    """Describe the charts on a sheet (kind, title, anchor, series references)."""
    out: list[dict[str, Any]] = []
    for i, chart in enumerate(_chart_list(ws)):
        name = type(chart).__name__
        kind = _KIND_OF.get(name, name.removesuffix("Chart").lower())
        if kind is None:
            kind = "bar" if chart.type == "bar" else "column"
        series = []
        for s in chart.series:
            vals = s.val.numRef.f if s.val is not None else s.yVal.numRef.f
            cats = None
            if s.cat is not None:
                ref = s.cat.numRef or s.cat.strRef
                cats = ref.f if ref is not None else None
            elif s.xVal is not None:
                ref = s.xVal.numRef or s.xVal.strRef
                cats = ref.f if ref is not None else None
            name_ref = s.tx.strRef.f if s.tx is not None and s.tx.strRef is not None else None
            series.append({"values": vals, "categories": cats, "name": name_ref})
        info: dict[str, Any] = {
            "index": i,
            "kind": kind,
            "title": _text(chart.title),
            "anchor": _anchor(chart),
            "series": series,
            "stacked": getattr(chart, "grouping", None) == "stacked",
        }
        if kind != "pie":
            info["x_title"] = _text(chart.x_axis.title)
            info["y_title"] = _text(chart.y_axis.title)
        out.append(info)
    return out


def remove_charts(ws: Worksheet, index: int | None = None) -> int:
    """Remove one chart by index, or all charts when ``index`` is ``None``."""
    if index is None:
        n = len(_chart_list(ws))
        _chart_list(ws).clear()
        return n
    if not 0 <= index < len(_chart_list(ws)):
        raise ValueError(f"Chart index {index} out of range")
    del _chart_list(ws)[index]
    return 1
