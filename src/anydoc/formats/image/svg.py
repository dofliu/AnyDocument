"""SVG document and handler (parsed with defusedxml; no rasterisation)."""

from __future__ import annotations

import re
from pathlib import Path
from typing import ClassVar
from xml.etree.ElementTree import Element

from defusedxml import ElementTree as SafeET
from defusedxml.common import DefusedXmlException

from anydoc.core.errors import CorruptFileError
from anydoc.core.model import Capability, Metadata, Part
from anydoc.core.registry import FormatHandler

_SVG_NS = "http://www.w3.org/2000/svg"
_LENGTH = re.compile(r"^\s*([0-9]*\.?[0-9]+(?:[eE][-+]?[0-9]+)?)\s*(px|pt|mm|cm|in|pc)?\s*$")
_TO_PX = {
    None: 1.0,
    "px": 1.0,
    "pt": 4 / 3,
    "pc": 16.0,
    "mm": 96 / 25.4,
    "cm": 96 / 2.54,
    "in": 96.0,
}


def _length(value: str | None) -> float | None:
    if not value:
        return None
    m = _LENGTH.match(value)
    if not m:
        return None
    return float(m.group(1)) * _TO_PX[m.group(2)]


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


class SvgDocument:
    """An opened SVG; exposes size, element statistics and embedded text."""

    format = "svg"

    def __init__(self, data: bytes, *, path: Path | None = None) -> None:
        self.path = path
        self._data = data
        try:
            root: Element = SafeET.fromstring(data)
        except (SafeET.ParseError, DefusedXmlException, ValueError) as exc:
            raise CorruptFileError(f"Invalid SVG: {exc}") from exc
        if _local(root.tag) != "svg":
            raise CorruptFileError("Not an SVG document: root element is not <svg>")
        self._root = root
        view_box = root.get("viewBox") or root.get("viewbox")
        self.view_box: tuple[float, float, float, float] | None = None
        if view_box:
            nums = [
                float(n) for n in re.findall(r"[-+]?[0-9]*\.?[0-9]+(?:[eE][-+]?[0-9]+)?", view_box)
            ]
            if len(nums) == 4:
                self.view_box = (nums[0], nums[1], nums[2], nums[3])
        width = _length(root.get("width"))
        height = _length(root.get("height"))
        if self.view_box is not None:
            width = width if width is not None else self.view_box[2]
            height = height if height is not None else self.view_box[3]
        self.width = width
        self.height = height
        counts: dict[str, int] = {}
        for el in root.iter():
            name = _local(el.tag) if isinstance(el.tag, str) else ""
            if name:
                counts[name] = counts.get(name, 0) + 1
        self.element_counts = counts
        title_el = next((e for e in root.iter() if _local(str(e.tag)) == "title"), None)
        self.title = (title_el.text or "").strip() if title_el is not None else ""
        self.metadata = Metadata(
            format="svg",
            mime="image/svg+xml",
            title=self.title or None,
            page_count=1,
            extra={
                "width": self.width,
                "height": self.height,
                "view_box": list(self.view_box) if self.view_box else None,
                "elements": dict(counts),
            },
        )

    def parts(self) -> list[Part]:
        """Return a single ``image`` part sized in CSS pixels when known."""
        size = (self.width, self.height) if self.width and self.height else None
        return [Part(kind="image", index=0, name="Image", size=size)]

    def text(self) -> str:
        """Return the visible text elements (``text``/``tspan`` content) one per line."""
        lines: list[str] = []
        for el in self._root.iter():
            if isinstance(el.tag, str) and _local(el.tag) == "text":
                content = "".join(el.itertext()).strip()
                if content:
                    lines.append(" ".join(content.split()))
        return "\n".join(lines)

    def save(self, path: Path | None = None) -> Path:
        """Write the original bytes to ``path`` (default: the source path)."""
        target = path or self.path
        if target is None:
            raise ValueError("No path given and document has no source path")
        target.write_bytes(self._data)
        return target

    def close(self) -> None:
        """No resources to release."""


class SvgHandler(FormatHandler):
    """Handler for ``.svg`` vector images (read-only)."""

    format_id: ClassVar[str] = "svg"
    extensions: ClassVar[tuple[str, ...]] = ("svg",)
    mime_types: ClassVar[tuple[str, ...]] = ("image/svg+xml",)
    capabilities: ClassVar[frozenset[Capability]] = frozenset(
        {Capability.READ, Capability.EXTRACT_TEXT}
    )

    @classmethod
    def sniff(cls, head: bytes, path: Path | None) -> float:
        """Return confidence from an ``<svg`` tag in the head, else from the extension."""
        if b"<svg" in head[:2048].lower():
            return 1.0
        if path is not None and path.suffix.lower() == ".svg":
            return 0.5
        return 0.0

    def open(self, source: Path | bytes) -> SvgDocument:
        """Open an SVG from a path or bytes; invalid XML raises ``CorruptFileError``."""
        path = source if isinstance(source, Path) else None
        if isinstance(source, Path):
            try:
                data = source.read_bytes()
            except OSError as exc:
                raise CorruptFileError(f"Cannot read {source}: {exc}") from exc
        else:
            data = source
        return SvgDocument(data, path=path)
