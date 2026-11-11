"""Native chart creation and editing for PPTX slides (T072).

Charts are real PowerPoint charts (with an embedded workbook), so they stay editable
in PowerPoint/LibreOffice. A chart is addressed by ``(slide index, shape id)``.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from pptx.chart.data import CategoryChartData, XyChartData
from pptx.enum.chart import XL_CHART_TYPE, XL_LEGEND_POSITION
from pptx.oxml.ns import qn

from anydoc.core.errors import AnyDocError
from anydoc.formats.pptx.shapes import Length, _rgb, _size, _slide, find_shape, to_emu

Number = int | float
SeriesInput = Mapping[str, Sequence[Any]] | Sequence[tuple[str, Sequence[Any]]]

# kind -> (clustered/plain, stacked, 100% stacked)
_TYPES: dict[str, tuple[Any, Any, Any]] = {
    "column": (
        XL_CHART_TYPE.COLUMN_CLUSTERED,
        XL_CHART_TYPE.COLUMN_STACKED,
        XL_CHART_TYPE.COLUMN_STACKED_100,
    ),
    "bar": (
        XL_CHART_TYPE.BAR_CLUSTERED,
        XL_CHART_TYPE.BAR_STACKED,
        XL_CHART_TYPE.BAR_STACKED_100,
    ),
    "line": (
        XL_CHART_TYPE.LINE_MARKERS,
        XL_CHART_TYPE.LINE_MARKERS_STACKED,
        XL_CHART_TYPE.LINE_MARKERS_STACKED_100,
    ),
    "area": (
        XL_CHART_TYPE.AREA,
        XL_CHART_TYPE.AREA_STACKED,
        XL_CHART_TYPE.AREA_STACKED_100,
    ),
    "pie": (XL_CHART_TYPE.PIE, None, None),
    "doughnut": (XL_CHART_TYPE.DOUGHNUT, None, None),
    "radar": (XL_CHART_TYPE.RADAR, None, None),
    "scatter": (XL_CHART_TYPE.XY_SCATTER, None, None),
}
CHART_KINDS = tuple(_TYPES)

_STACK_NAMES = ("none", "stacked", "percent")
_LEGEND = {
    "right": XL_LEGEND_POSITION.RIGHT,
    "left": XL_LEGEND_POSITION.LEFT,
    "top": XL_LEGEND_POSITION.TOP,
    "bottom": XL_LEGEND_POSITION.BOTTOM,
    "corner": XL_LEGEND_POSITION.CORNER,
}
_NO_AXES = ("pie", "doughnut")


@dataclass(frozen=True)
class ChartSeries:
    """One data series; ``x_values`` is only set for scatter charts."""

    name: str
    values: list[float | None]
    x_values: list[float | None] | None = None


@dataclass(frozen=True)
class ChartInfo:
    """A chart as read back from the presentation.

    ``kind`` is one of ``CHART_KINDS`` (or the python-pptx type name for chart types
    this module cannot create); ``stacking`` is ``none``/``stacked``/``percent``.
    """

    shape_id: int
    kind: str
    stacking: str
    title: str | None
    categories: list[str]
    series: list[ChartSeries]
    legend: str | None
    data_labels: bool
    x_title: str | None
    y_title: str | None


# -- helpers -----------------------------------------------------------------


def _chart_frame(prs: Any, slide: int, shape_id: int) -> Any:
    shape = find_shape(prs, slide, shape_id)
    if not getattr(shape, "has_chart", False):
        raise AnyDocError(f"Shape {shape_id} ({shape.name!r}) is not a chart")
    return shape


def _classify(chart_type: Any) -> tuple[str, str]:
    for kind, variants in _TYPES.items():
        for name, variant in zip(_STACK_NAMES, variants, strict=True):
            if variant is not None and chart_type == variant:
                return kind, name
    name = getattr(chart_type, "name", str(chart_type)).lower()
    for kind in _TYPES:  # variants this module does not create (exploded pie, plain line…)
        prefix = "xy_scatter" if kind == "scatter" else kind
        if name.startswith(prefix):
            stacking = "percent" if "100" in name else "stacked" if "stacked" in name else "none"
            return kind, stacking
    return name, "none"


def _number(value: Any, what: str) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ValueError(f"{what} must be numbers (or None), got {value!r}")
    return float(value)


def _series_items(series: SeriesInput) -> list[tuple[str, list[Any]]]:
    pairs = series.items() if isinstance(series, Mapping) else series
    items = [(str(name), list(values)) for name, values in pairs]
    if not items:
        raise ValueError("A chart needs at least one series")
    return items


def _chart_data(kind: str, categories: Sequence[Any] | None, series: SeriesInput) -> Any:
    items = _series_items(series)
    if kind == "scatter":
        make_xy: Any = XyChartData  # python-pptx chart data classes are untyped
        xy = make_xy()
        for name, points in items:
            target = xy.add_series(name)
            for point in points:
                if not isinstance(point, Sequence) or isinstance(point, str) or len(point) != 2:
                    raise ValueError("Scatter series values must be (x, y) pairs")
                x, y = (_number(v, "Scatter values") for v in point)
                if x is None or y is None:
                    raise ValueError("Scatter points need both x and y")
                target.add_data_point(x, y)
        return xy
    if not categories:
        raise ValueError("categories are required for this chart kind")
    if kind == "pie" and len(items) != 1:
        raise ValueError("A pie chart takes exactly one series")
    labels = list(categories)
    make_data: Any = CategoryChartData
    data = make_data()
    data.categories = labels
    for name, values in items:
        if len(values) != len(labels):
            raise ValueError(
                f"Series {name!r} has {len(values)} values for {len(labels)} categories"
            )
        data.add_series(name, [_number(v, f"Series {name!r} values") for v in values])
    return data


def _set_title(owner: Any, text: str | None) -> None:
    """Set (non-empty) or remove (empty string) a chart/axis title; ``None`` keeps it."""
    if text is None:
        return
    if text == "":
        owner.has_title = False
        return
    owner.has_title = True
    title = owner.chart_title if hasattr(owner, "chart_title") else owner.axis_title
    title.text_frame.text = text


def _get_title(owner: Any) -> str | None:
    if not owner.has_title:
        return None
    title = owner.chart_title if hasattr(owner, "chart_title") else owner.axis_title
    return str(title.text_frame.text) or None


def _axes(chart: Any) -> tuple[Any, Any] | None:
    try:
        return chart.category_axis, chart.value_axis
    except ValueError:
        return None  # pie/doughnut charts have no axes


def _color_series(chart: Any, kind: str, colors: Sequence[str]) -> None:
    plot = chart.plots[0]
    if kind in _NO_AXES:
        points = plot.series[0].points
        for i, color in enumerate(colors[: len(list(plot.categories))]):
            fill = points[i].format.fill
            fill.solid()
            fill.fore_color.rgb = _rgb(color)
        return
    for item, color in zip(plot.series, colors, strict=False):
        rgb = _rgb(color)
        if kind in ("line", "radar"):
            item.format.line.color.rgb = rgb
        elif kind == "scatter":
            item.marker.format.fill.solid()
            item.marker.format.fill.fore_color.rgb = rgb
        else:
            item.format.fill.solid()
            item.format.fill.fore_color.rgb = rgb


# -- API ---------------------------------------------------------------------


def add_chart(
    prs: Any,
    slide: int,
    kind: str,
    categories: Sequence[Any] | None,
    series: SeriesInput,
    *,
    left: Length,
    top: Length,
    width: Length,
    height: Length,
    title: str | None = None,
    x_title: str | None = None,
    y_title: str | None = None,
    stacking: str = "none",
    legend: str | None = "right",
    data_labels: bool = False,
    number_format: str | None = None,
    colors: Sequence[str] | None = None,
) -> int:
    """Add a native chart and return its shape id.

    ``kind`` is one of ``CHART_KINDS``. ``series`` maps a series name to its values
    (one per category; a pie takes a single series); for ``scatter`` the values are
    ``(x, y)`` pairs and ``categories`` is ignored. ``stacking`` is ``none``/``stacked``/``percent``
    (column, bar, line, area). ``legend`` is a position (right/left/top/bottom/corner)
    or ``None`` to hide it. ``colors`` (``RRGGBB``) colour the series in order, or the
    slices of a pie/doughnut.
    """
    key = kind.lower()
    if key not in _TYPES:
        raise ValueError(f"Unknown chart kind: {kind!r} (expected one of {CHART_KINDS})")
    if stacking not in _STACK_NAMES:
        raise ValueError(f"Invalid stacking: {stacking!r} (expected one of {_STACK_NAMES})")
    chart_type = _TYPES[key][_STACK_NAMES.index(stacking)]
    if chart_type is None:
        raise ValueError(f"{key} charts cannot be stacked")
    if legend is not None and legend.lower() not in _LEGEND:
        raise ValueError(f"Invalid legend position: {legend!r}")
    if key == "scatter" and (data_labels or number_format is not None):
        raise ValueError("Data labels are not supported on scatter charts")
    data = _chart_data(key, categories, series)
    for color in colors or ():
        _rgb(color)  # validate before touching the slide
    frame = _slide(prs, slide).shapes.add_chart(
        chart_type,
        to_emu(left),
        to_emu(top),
        _size(width, "width"),
        _size(height, "height"),
        data,
    )
    chart = frame.chart
    chart.has_legend = False
    set_chart_options(
        prs,
        slide,
        frame.shape_id,
        title=title if title is not None else "",
        x_title=x_title,
        y_title=y_title,
        legend=legend if legend is not None else "none",
        data_labels=data_labels,
        number_format=number_format,
    )
    if colors:
        _color_series(chart, key, colors)
    return int(frame.shape_id)


def set_chart_options(
    prs: Any,
    slide: int,
    shape_id: int,
    *,
    title: str | None = None,
    x_title: str | None = None,
    y_title: str | None = None,
    legend: str | None = None,
    data_labels: bool | None = None,
    number_format: str | None = None,
    colors: Sequence[str] | None = None,
) -> None:
    """Change titles, legend, data labels and series colours of an existing chart.

    ``None`` keeps a setting. An empty string removes a title; ``legend="none"`` hides
    the legend. ``number_format`` (e.g. ``"0.0"``, ``"0%"``) applies to data labels.
    """
    chart = _chart_frame(prs, slide, shape_id).chart
    kind, _ = _classify(chart.chart_type)
    if legend is not None and legend.lower() != "none" and legend.lower() not in _LEGEND:
        raise ValueError(f"Invalid legend position: {legend!r}")
    axes = _axes(chart)
    if axes is None and (x_title or y_title):
        raise AnyDocError(f"{kind} charts have no axes")
    _set_title(chart, title)
    if axes is not None:
        _set_title(axes[0], x_title)
        _set_title(axes[1], y_title)
    if legend is not None:
        if legend.lower() == "none":
            chart.has_legend = False
        else:
            chart.has_legend = True
            chart.legend.position = _LEGEND[legend.lower()]
            chart.legend.include_in_layout = False
    plot = chart.plots[0]
    if kind == "scatter" and (data_labels or number_format is not None):
        raise AnyDocError("Data labels are not supported on scatter charts")
    if data_labels is True:
        plot.has_data_labels = True
    elif data_labels is False and number_format is None:
        for labels in plot._element.findall(qn("c:dLbls")):
            plot._element.remove(labels)
    if number_format is not None:
        plot.has_data_labels = True
        labels = plot.data_labels
        labels.number_format = number_format
        labels.number_format_is_linked = False
    if colors:
        _color_series(chart, kind, colors)


def update_chart_data(
    prs: Any,
    slide: int,
    shape_id: int,
    categories: Sequence[Any] | None,
    series: SeriesInput,
) -> None:
    """Replace a chart's categories and series (titles, legend and type are kept)."""
    chart = _chart_frame(prs, slide, shape_id).chart
    kind, _ = _classify(chart.chart_type)
    chart.replace_data(
        _chart_data("scatter" if kind == "scatter" else "category", categories, series)
    )


def _points(element: Any, path: str) -> list[float | None]:
    """Read a series' cached numbers under ``path`` (``c:xVal``…), ordered by index."""
    found: dict[int, float] = {}
    for pt in element.xpath(f"{path}//c:pt"):
        value = pt.xpath("c:v")
        if value and value[0].text:
            found[int(pt.get("idx"))] = float(value[0].text)
    counts = element.xpath(f"{path}//c:ptCount/@val")
    count = int(counts[0]) if counts else (max(found) + 1 if found else 0)
    return [found.get(i) for i in range(count)]


def chart_info(prs: Any, slide: int, shape_id: int) -> ChartInfo:
    """Return kind, titles, categories, series values, legend and labels of a chart."""
    frame = _chart_frame(prs, slide, shape_id)
    chart = frame.chart
    kind, stacking = _classify(chart.chart_type)
    plot = chart.plots[0]
    series: list[ChartSeries] = []
    for item in plot.series:
        if kind == "scatter":
            series.append(
                ChartSeries(
                    name=str(item.name),
                    values=_points(item._element, "c:yVal"),
                    x_values=_points(item._element, "c:xVal"),
                )
            )
        else:
            series.append(ChartSeries(name=str(item.name), values=list(item.values)))
    legend: str | None = None
    if chart.has_legend:
        names = {v: k for k, v in _LEGEND.items()}
        legend = names.get(chart.legend.position, "right")
    axes = _axes(chart)
    return ChartInfo(
        shape_id=int(frame.shape_id),
        kind=kind,
        stacking=stacking,
        title=_get_title(chart),
        categories=[] if kind == "scatter" else [str(c) for c in plot.categories],
        series=series,
        legend=legend,
        data_labels=plot._element.find(qn("c:dLbls")) is not None,
        x_title=_get_title(axes[0]) if axes is not None else None,
        y_title=_get_title(axes[1]) if axes is not None else None,
    )
