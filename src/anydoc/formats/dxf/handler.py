"""Read-oriented DXF document and handler built on ezdxf."""

from __future__ import annotations

import tempfile
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any, ClassVar

from ezdxf import filemanagement as ezdxf_files
from ezdxf import units as dxf_units

from anydoc.core.errors import CorruptFileError
from anydoc.core.model import Capability, Metadata, Part
from anydoc.core.registry import FormatHandler

_DXF_MIME = "image/vnd.dxf"

_RELEASES = {
    "AC1006": "R10",
    "AC1009": "R12",
    "AC1012": "R13",
    "AC1014": "R14",
    "AC1015": "R2000",
    "AC1018": "R2004",
    "AC1021": "R2007",
    "AC1024": "R2010",
    "AC1027": "R2013",
    "AC1032": "R2018",
}


@dataclass(frozen=True)
class LayerInfo:
    """A drawing layer. ``color`` is the absolute ACI index (1-255); ``on`` is visibility."""

    name: str
    color: int
    linetype: str
    on: bool
    frozen: bool
    locked: bool


@dataclass(frozen=True)
class EntityInfo:
    """A drawing entity: DXF type, layer, handle and (for text entities) its text."""

    type: str
    layer: str
    handle: str
    text: str | None = None


@dataclass(frozen=True)
class BlockInfo:
    """A block definition (layout blocks excluded) with its entity count."""

    name: str
    entity_count: int
    base_point: tuple[float, float, float]


@dataclass(frozen=True)
class TextItem:
    """A piece of text from a TEXT, MTEXT or attribute (ATTRIB) entity."""

    kind: str
    text: str
    layer: str
    layout: str


def _entity_text(entity: Any) -> str | None:
    kind = entity.dxftype()
    if kind == "MTEXT":
        return str(entity.plain_text())
    if kind in ("TEXT", "ATTRIB", "ATTDEF"):
        return str(entity.dxf.text)
    return None


class DxfDocument:
    """An opened DXF drawing; ``save`` rewrites it via ezdxf (text DXF)."""

    format = "dxf"

    def __init__(self, doc: Any, *, path: Path | None = None) -> None:
        self._doc = doc
        self.path = path
        self.metadata = self._build_metadata()

    @property
    def native(self) -> Any:
        """The underlying ``ezdxf`` drawing."""
        return self._doc

    # -- header ------------------------------------------------------------

    @property
    def version(self) -> str:
        """The DXF version code, e.g. ``AC1027``."""
        return str(self._doc.dxfversion)

    @property
    def release(self) -> str:
        """The AutoCAD release name, e.g. ``R2013``."""
        return _RELEASES.get(self.version, self.version)

    @property
    def units(self) -> str:
        """Drawing units name from ``$INSUNITS`` (``unitless`` when unset)."""
        code = int(self._doc.header.get("$INSUNITS", 0) or 0)
        return str(dxf_units.decode(code)) if code else "unitless"

    # -- structure ---------------------------------------------------------

    def layers(self) -> list[LayerInfo]:
        """Return every layer with colour, linetype and on/frozen/locked state."""
        return [
            LayerInfo(
                name=layer.dxf.name,
                color=layer.color,
                linetype=layer.dxf.linetype,
                on=layer.is_on(),
                frozen=layer.is_frozen(),
                locked=layer.is_locked(),
            )
            for layer in self._doc.layers
        ]

    def layout_names(self) -> list[str]:
        """Return layout names in tab order (``Model`` first)."""
        return [str(n) for n in self._doc.layouts.names_in_taborder()]

    def _layout(self, name: str) -> Any:
        if name not in self._doc.layouts:
            raise KeyError(f"No such layout: {name}")
        return self._doc.layouts.get(name)

    def entities(self, layout: str = "Model", *, layer: str | None = None) -> list[EntityInfo]:
        """Return the entities of a layout, optionally only those on ``layer``."""
        return [
            EntityInfo(e.dxftype(), e.dxf.layer, e.dxf.handle, _entity_text(e))
            for e in self._layout(layout)
            if layer is None or e.dxf.layer == layer
        ]

    def entity_counts(self, layout: str | None = None) -> dict[str, int]:
        """Count entities by DXF type, in one layout or across all layouts."""
        names = [layout] if layout is not None else self.layout_names()
        counts: Counter[str] = Counter()
        for name in names:
            counts.update(e.dxftype() for e in self._layout(name))
        return dict(sorted(counts.items()))

    def blocks(self) -> list[BlockInfo]:
        """Return user block definitions (model/paper space blocks are excluded)."""
        out: list[BlockInfo] = []
        for block in self._doc.blocks:
            if block.is_any_layout:
                continue
            base = block.block.dxf.base_point
            out.append(
                BlockInfo(block.name, len(block), (float(base.x), float(base.y), float(base.z)))
            )
        return out

    def texts(self, layout: str | None = None) -> list[TextItem]:
        """Return TEXT/MTEXT entities and INSERT attributes (one layout or all)."""
        names = [layout] if layout is not None else self.layout_names()
        items: list[TextItem] = []
        for name in names:
            for e in self._layout(name):
                kind = e.dxftype()
                text = _entity_text(e)
                if text is not None:
                    items.append(TextItem(kind, text, e.dxf.layer, name))
                elif kind == "INSERT":
                    for att in e.attribs:
                        items.append(TextItem("ATTRIB", att.dxf.text, att.dxf.layer, name))
        return [t for t in items if t.text]

    # -- Document protocol -------------------------------------------------

    def _build_metadata(self) -> Metadata:
        header = self._doc.header
        title = header.get("$PROJECTNAME") or None
        return Metadata(
            format="dxf",
            mime=_DXF_MIME,
            title=str(title) if title else None,
            page_count=len(self._doc.layouts.names()),
            extra={
                "version": self.version,
                "release": self.release,
                "units": self.units,
                "layer_count": len(self._doc.layers),
                "layouts": self.layout_names(),
                "block_count": len(self.blocks()),
                "entity_counts": self.entity_counts(),
            },
        )

    def parts(self) -> list[Part]:
        """Return one part per layout (model space first)."""
        return [Part(kind="layout", index=i, name=n) for i, n in enumerate(self.layout_names())]

    def text(self) -> str:
        """Return all drawing text (TEXT, MTEXT, attributes), one item per line."""
        return "\n".join(t.text for t in self.texts())

    def save(self, path: Path | None = None) -> Path:
        """Write the drawing to ``path`` (default: the source path) and return it."""
        target = path or self.path
        if target is None:
            raise ValueError("No path given and document has no source path")
        self._doc.saveas(str(target))
        return target

    def close(self) -> None:
        """Release resources (nothing to close for ezdxf)."""


class DxfHandler(FormatHandler):
    """Handler for AutoCAD ``.dxf`` drawings (ASCII and binary)."""

    format_id: ClassVar[str] = "dxf"
    extensions: ClassVar[tuple[str, ...]] = ("dxf",)
    mime_types: ClassVar[tuple[str, ...]] = (_DXF_MIME, "application/dxf")
    capabilities: ClassVar[frozenset[Capability]] = frozenset(
        {Capability.READ, Capability.EXTRACT_TEXT}
    )

    @classmethod
    def sniff(cls, head: bytes, path: Path | None) -> float:
        """Return confidence from the extension (content detection lives in ``detect``)."""
        if path is not None and path.suffix.lower() == ".dxf":
            return 0.9
        return 0.0

    def open(self, source: Path | bytes) -> DxfDocument:
        """Open a DXF from a path or bytes; invalid drawings raise ``CorruptFileError``."""
        try:
            if isinstance(source, Path):
                return DxfDocument(ezdxf_files.readfile(str(source)), path=source)
            with tempfile.TemporaryDirectory() as tmp:
                tmp_path = Path(tmp) / "in.dxf"
                tmp_path.write_bytes(source)
                return DxfDocument(ezdxf_files.readfile(str(tmp_path)))
        except Exception as exc:  # ezdxf raises DXFError, IOError, UnicodeError, ...
            raise CorruptFileError(f"Invalid DXF: {exc}") from exc
