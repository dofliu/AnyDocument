"""DXF rendering to SVG, PNG and PDF using the ``ezdxf`` drawing add-on."""

from __future__ import annotations

import io
from collections.abc import Collection
from typing import Any

from ezdxf.addons.drawing import layout, svg
from ezdxf.addons.drawing.frontend import Frontend
from ezdxf.addons.drawing.matplotlib import MatplotlibBackend
from ezdxf.addons.drawing.properties import RenderContext
from matplotlib.figure import Figure

RENDER_FORMATS = ("svg", "png", "pdf")
_BLANK_SVG = (
    b'<?xml version="1.0" encoding="UTF-8"?>\n'
    b'<svg xmlns="http://www.w3.org/2000/svg" width="0" height="0" viewBox="0 0 0 0"></svg>\n'
)


def render_layout(
    doc: Any,
    fmt: str,
    *,
    layout_name: str = "Model",
    layers: Collection[str] | None = None,
    dpi: float = 150.0,
) -> bytes:
    """Render one layout of an ``ezdxf`` drawing and return the encoded bytes.

    ``fmt`` is ``svg``, ``png`` or ``pdf``. ``layers`` restricts output to entities on the
    named layers (``None`` renders everything that is visible).
    """
    fmt = fmt.lower()
    if fmt not in RENDER_FORMATS:
        raise ValueError(f"Unsupported render format: {fmt} (expected one of {RENDER_FORMATS})")
    if dpi <= 0:
        raise ValueError("dpi must be positive")
    if layout_name not in doc.layouts:
        raise KeyError(f"No such layout: {layout_name}")
    target = doc.layouts.get(layout_name)
    wanted = None if layers is None else set(layers)
    if wanted is not None:
        unknown = sorted(n for n in wanted if n not in doc.layers)
        if unknown:
            raise KeyError(f"No such layer: {', '.join(unknown)}")

    def keep(entity: Any) -> bool:
        return wanted is None or str(entity.dxf.layer) in wanted

    context = RenderContext(doc)
    if fmt == "svg":
        backend = svg.SVGBackend()
        Frontend(context, backend).draw_layout(target, filter_func=keep)
        try:
            return backend.get_string(layout.Page(0, 0)).encode("utf-8")
        except ValueError:  # nothing drawn (e.g. every entity filtered out): empty bounding box
            return _BLANK_SVG

    fig = Figure()
    ax = fig.add_axes((0, 0, 1, 1))
    Frontend(context, MatplotlibBackend(ax)).draw_layout(target, finalize=True, filter_func=keep)
    buf = io.BytesIO()
    fig.savefig(buf, format=fmt, dpi=dpi)
    return buf.getvalue()
