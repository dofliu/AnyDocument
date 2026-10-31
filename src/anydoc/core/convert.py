"""Conversion graph: converters as weighted edges between format ids, searched with Dijkstra."""

from __future__ import annotations

import heapq
import itertools
import shutil
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from anydoc.core.errors import AnyDocError, UnsupportedFormatError

if TYPE_CHECKING:
    from anydoc.core.registry import HandlerRegistry

ConvertFunc = Callable[[Path, Path], None]


class ConversionPathError(AnyDocError):
    """Raised when no usable conversion path exists between two formats."""


@dataclass(frozen=True)
class Converter:
    """One edge of the conversion graph: ``func(src_path, dst_path)`` turns ``src`` into ``dst``."""

    src: str
    dst: str
    func: ConvertFunc
    cost: float = 1.0
    requires: tuple[str, ...] = ()
    name: str = ""

    def available(self) -> bool:
        """Return whether every external tool this converter needs is installed."""
        from anydoc.external import is_available

        return all(is_available(tool) for tool in self.requires)


class ConversionGraph:
    """Directed graph of :class:`Converter` edges with lowest-cost path search."""

    def __init__(self) -> None:
        self._edges: list[Converter] = []

    def add(self, converter: Converter) -> Converter:
        """Register a converter. Costs must be positive."""
        if converter.cost <= 0:
            raise ValueError("converter cost must be positive")
        self._edges.append(converter)
        return converter

    def register(
        self,
        src: str,
        dst: str,
        *,
        cost: float = 1.0,
        requires: tuple[str, ...] = (),
        name: str = "",
    ) -> Callable[[ConvertFunc], ConvertFunc]:
        """Decorator form of :meth:`add`."""

        def deco(func: ConvertFunc) -> ConvertFunc:
            self.add(Converter(src, dst, func, cost, requires, name or func.__name__))
            return func

        return deco

    def converters(self) -> list[Converter]:
        """Return all registered converters."""
        return list(self._edges)

    def formats(self) -> set[str]:
        """Return every format id that appears as a source or destination."""
        return {c.src for c in self._edges} | {c.dst for c in self._edges}

    def targets_from(self, src: str, *, skip_unavailable: bool = True) -> set[str]:
        """Return the formats reachable from ``src`` (excluding ``src``)."""
        seen = {src}
        stack = [src]
        while stack:
            cur = stack.pop()
            for c in self._edges:
                if c.src == cur and c.dst not in seen and (not skip_unavailable or c.available()):
                    seen.add(c.dst)
                    stack.append(c.dst)
        return seen - {src}

    def find_path(self, src: str, dst: str, *, skip_unavailable: bool = True) -> list[Converter]:
        """Return the lowest-cost converter chain from ``src`` to ``dst`` (empty if equal).

        Edges whose external tools are missing are skipped unless ``skip_unavailable`` is false.

        Raises:
            ConversionPathError: if no path exists.
        """
        if src == dst:
            return []
        counter = itertools.count()
        best: dict[str, float] = {src: 0.0}
        prev: dict[str, Converter] = {}
        heap: list[tuple[float, int, str]] = [(0.0, next(counter), src)]
        while heap:
            dist, _, node = heapq.heappop(heap)
            if node == dst:
                break
            if dist > best.get(node, float("inf")):
                continue
            for c in self._edges:
                if c.src != node or (skip_unavailable and not c.available()):
                    continue
                nd = dist + c.cost
                if nd < best.get(c.dst, float("inf")):
                    best[c.dst] = nd
                    prev[c.dst] = c
                    heapq.heappush(heap, (nd, next(counter), c.dst))
        if dst not in prev:
            hint = self._missing_tools_hint(src, dst) if skip_unavailable else ""
            raise ConversionPathError(f"No conversion path from {src} to {dst}{hint}")
        path: list[Converter] = []
        node = dst
        while node != src:
            edge = prev[node]
            path.append(edge)
            node = edge.src
        path.reverse()
        return path

    def _missing_tools_hint(self, src: str, dst: str) -> str:
        try:
            path = self.find_path(src, dst, skip_unavailable=False)
        except ConversionPathError:
            return ""
        from anydoc.external import is_available

        missing = sorted({t for c in path for t in c.requires if not is_available(t)})
        return f" (requires missing external tool: {', '.join(missing)})" if missing else ""

    def convert(
        self, src_path: Path, dst_path: Path, src_format: str, dst_format: str
    ) -> list[Converter]:
        """Convert ``src_path`` into ``dst_path`` along the best path; return the chain used."""
        path = self.find_path(src_format, dst_format)
        dst_path.parent.mkdir(parents=True, exist_ok=True)
        if not path:
            shutil.copyfile(src_path, dst_path)
            return path
        with tempfile.TemporaryDirectory(prefix="anydoc-convert-") as tmp:
            current = src_path
            for i, step in enumerate(path):
                last = i == len(path) - 1
                out = dst_path if last else Path(tmp) / f"step{i}.{step.dst}"
                step.func(current, out)
                if not out.exists():
                    label = step.name or f"{step.src}->{step.dst}"
                    raise ConversionPathError(f"Converter {label} produced no output")
                current = out
        return path


graph = ConversionGraph()
"""Process-wide default conversion graph; converters register themselves here."""


def convert(src: str | Path, dst: str | Path, *, graph_: ConversionGraph | None = None) -> Path:
    """Convert ``src`` to the format implied by ``dst``'s extension and write it to ``dst``.

    The source format is detected from content; the target from the extension.

    Raises:
        FileNotFoundError: if ``src`` does not exist.
        UnsupportedFormatError: if the target extension is unknown.
        ConversionPathError: if no converter chain exists.
    """
    from anydoc.core.detect import detect
    from anydoc.core.registry import registry

    g = graph_ or graph
    src_p, dst_p = Path(src), Path(dst)
    src_format = detect(src_p).format
    dst_format = _format_for_extension(dst_p, g, registry)
    g.convert(src_p, dst_p, src_format, dst_format)
    return dst_p


def _format_for_extension(dst: Path, g: ConversionGraph, registry: HandlerRegistry) -> str:
    ext = dst.suffix.lower().lstrip(".")
    if not ext:
        raise UnsupportedFormatError(f"Cannot infer target format: no extension on {dst}")
    registry.load_entry_points()
    handlers = registry.by_extension(ext)
    if handlers:
        return str(handlers[0].format_id)
    if ext in g.formats():
        return ext
    raise UnsupportedFormatError(f"Unknown target format: .{ext}")
