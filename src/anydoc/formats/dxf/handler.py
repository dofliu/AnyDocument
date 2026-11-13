"""DXF document (read, layer, entity and block editing, rendering) and handler built on ezdxf."""

from __future__ import annotations

import io
import tempfile
from collections import Counter
from collections.abc import Collection, Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, ClassVar, TypeVar

from ezdxf import filemanagement as ezdxf_files
from ezdxf import units as dxf_units

from anydoc.core.errors import CorruptFileError
from anydoc.core.model import Capability, Metadata, Part
from anydoc.core.registry import FormatHandler
from anydoc.formats.dxf import blocks as block_ops
from anydoc.formats.dxf import entities as entity_ops
from anydoc.formats.dxf import layers as layer_ops
from anydoc.formats.dxf import measure as measure_ops
from anydoc.formats.dxf.blocks import BlockDetail, BlockRef, Scale
from anydoc.formats.dxf.entities import EntityDetail, Point, decode_text
from anydoc.formats.dxf.layers import LayerInfo
from anydoc.formats.dxf.measure import BoundingBox, Distance, Measurement, PickHit
from anydoc.formats.dxf.render import render_layout

_DXF_MIME = "image/vnd.dxf"

_T = TypeVar("_T")

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
        return decode_text(str(entity.plain_text()))
    if kind in ("TEXT", "ATTRIB", "ATTDEF"):
        return decode_text(str(entity.dxf.text))
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
        return layer_ops.list_layers(self._doc)

    def layer(self, name: str) -> LayerInfo:
        """Return one layer by (case-insensitive) name; ``KeyError`` when missing."""
        return layer_ops.get_layer(self._doc, name)

    def layer_usage(self) -> dict[str, int]:
        """Count the entities on each layer across all layouts and blocks."""
        return layer_ops.layer_usage(self._doc)

    @property
    def current_layer(self) -> str:
        """The current layer name (``$CLAYER``)."""
        return layer_ops.current_layer(self._doc)

    def set_current_layer(self, name: str) -> str:
        """Make ``name`` the current layer and return its canonical name."""
        return layer_ops.set_current_layer(self._doc, name)

    def add_layer(
        self,
        name: str,
        *,
        color: int | str = 7,
        linetype: str = "Continuous",
        on: bool = True,
        frozen: bool = False,
        locked: bool = False,
    ) -> LayerInfo:
        """Create a layer; see ``anydoc.formats.dxf.layers.add_layer``."""
        info = layer_ops.add_layer(
            self._doc, name, color=color, linetype=linetype, on=on, frozen=frozen, locked=locked
        )
        self.refresh()
        return info

    def set_layer(
        self,
        name: str,
        *,
        color: int | str | None = None,
        linetype: str | None = None,
        on: bool | None = None,
        frozen: bool | None = None,
        locked: bool | None = None,
    ) -> LayerInfo:
        """Change layer properties; ``None`` leaves a property untouched."""
        return layer_ops.set_layer(
            self._doc, name, color=color, linetype=linetype, on=on, frozen=frozen, locked=locked
        )

    def rename_layer(self, old: str, new: str) -> LayerInfo:
        """Rename a layer and update the entities that refer to it."""
        return layer_ops.rename_layer(self._doc, old, new)

    def delete_layer(
        self, name: str, *, move_to: str | None = None, delete_entities: bool = False
    ) -> int:
        """Delete a layer; returns the number of entities moved or deleted."""
        count = layer_ops.delete_layer(
            self._doc, name, move_to=move_to, delete_entities=delete_entities
        )
        self.refresh()
        return count

    def purge_layers(self) -> list[str]:
        """Delete every unused layer and return the removed names."""
        removed = layer_ops.purge_layers(self._doc)
        self.refresh()
        return removed

    # -- entity editing ----------------------------------------------------

    def entity(self, handle: str) -> EntityDetail:
        """Return one entity (with geometry) by handle; ``KeyError`` when missing."""
        return entity_ops.get_entity(self._doc, handle)

    def entity_details(
        self,
        layout: str = "Model",
        *,
        layer: str | None = None,
        types: Collection[str] | str | None = None,
        block: str | None = None,
    ) -> list[EntityDetail]:
        """Return the entities of a layout (or block) with geometry, filtered by layer/type(s)."""
        return entity_ops.list_entities(
            self._doc, layout=layout, block=block, layer=layer, types=types
        )

    def select(
        self,
        layout: str = "Model",
        *,
        layer: str | None = None,
        types: Collection[str] | str | None = None,
        block: str | None = None,
    ) -> list[str]:
        """Return the handles of the entities in a layout (or block) matching the filters."""
        return entity_ops.select(self._doc, layout=layout, block=block, layer=layer, types=types)

    def add_line(self, start: Point, end: Point, **options: Any) -> str:
        """Add a LINE and return its handle; see ``anydoc.formats.dxf.entities.add_line``."""
        return self._edited(entity_ops.add_line(self._doc, start, end, **options))

    def add_circle(self, center: Point, radius: float, **options: Any) -> str:
        """Add a CIRCLE and return its handle."""
        return self._edited(entity_ops.add_circle(self._doc, center, radius, **options))

    def add_arc(
        self, center: Point, radius: float, start_angle: float, end_angle: float, **options: Any
    ) -> str:
        """Add a counter-clockwise ARC (angles in degrees) and return its handle."""
        return self._edited(
            entity_ops.add_arc(self._doc, center, radius, start_angle, end_angle, **options)
        )

    def add_polyline(self, points: Iterable[Point], **options: Any) -> str:
        """Add an LWPOLYLINE (``closed=True`` to close it) and return its handle."""
        return self._edited(entity_ops.add_polyline(self._doc, points, **options))

    def add_text(self, text: str, insert: Point, **options: Any) -> str:
        """Add a single-line TEXT and return its handle."""
        return self._edited(entity_ops.add_text(self._doc, text, insert, **options))

    def add_mtext(self, text: str, insert: Point, **options: Any) -> str:
        """Add a multi-line MTEXT and return its handle."""
        return self._edited(entity_ops.add_mtext(self._doc, text, insert, **options))

    def modify_entity(self, handle: str, **properties: Any) -> EntityDetail:
        """Change entity properties; see ``anydoc.formats.dxf.entities.modify_entity``."""
        return self._edited(entity_ops.modify_entity(self._doc, handle, **properties))

    def delete_entities(self, handles: Iterable[str] | str) -> int:
        """Delete entities by handle and return how many were removed."""
        return self._edited(entity_ops.delete_entities(self._doc, handles))

    def move_entities(
        self, handles: Iterable[str] | str, dx: float, dy: float, dz: float = 0.0
    ) -> list[str]:
        """Translate entities by ``(dx, dy, dz)``."""
        return entity_ops.move_entities(self._doc, handles, dx, dy, dz)

    def rotate_entities(
        self, handles: Iterable[str] | str, angle: float, base: Point = (0.0, 0.0)
    ) -> list[str]:
        """Rotate entities counter-clockwise by ``angle`` degrees around ``base``."""
        return entity_ops.rotate_entities(self._doc, handles, angle, base)

    def scale_entities(
        self, handles: Iterable[str] | str, factor: float, base: Point = (0.0, 0.0)
    ) -> list[str]:
        """Scale entities uniformly by ``factor`` relative to ``base``."""
        return entity_ops.scale_entities(self._doc, handles, factor, base)

    def copy_entities(
        self,
        handles: Iterable[str] | str,
        dx: float = 0.0,
        dy: float = 0.0,
        dz: float = 0.0,
        *,
        layer: str | None = None,
    ) -> list[str]:
        """Copy entities with an offset and return the handles of the copies."""
        return self._edited(entity_ops.copy_entities(self._doc, handles, dx, dy, dz, layer=layer))

    # -- blocks --------------------------------------------------------------

    def block(self, name: str) -> BlockDetail:
        """Return one block definition (attributes, reference count); ``KeyError`` if missing."""
        return block_ops.get_block(self._doc, name)

    def block_details(self) -> list[BlockDetail]:
        """Return every user block definition with attribute definitions and reference counts."""
        return block_ops.list_blocks(self._doc)

    def define_block(
        self,
        name: str,
        *,
        base_point: Point = (0.0, 0.0),
        handles: Iterable[str] | str | None = None,
        keep: bool = False,
        replace: bool = False,
        description: str = "",
    ) -> BlockDetail:
        """Create a block, optionally from entities; see ``anydoc.formats.dxf.blocks``."""
        return self._edited(
            block_ops.define_block(
                self._doc,
                name,
                base_point=base_point,
                handles=handles,
                keep=keep,
                replace=replace,
                description=description,
            )
        )

    def add_attdef(self, block: str, tag: str, insert: Point, **options: Any) -> str:
        """Add an attribute definition to a block and return its handle."""
        return block_ops.add_attdef(self._doc, block, tag, insert, **options)

    def rename_block(self, old: str, new: str) -> BlockDetail:
        """Rename a block and update the references to it."""
        return block_ops.rename_block(self._doc, old, new)

    def delete_block(self, name: str, *, delete_references: bool = False) -> int:
        """Delete a block; returns the number of references deleted with it."""
        return self._edited(
            block_ops.delete_block(self._doc, name, delete_references=delete_references)
        )

    def purge_blocks(self) -> list[str]:
        """Delete every unreferenced block and return the removed names."""
        return self._edited(block_ops.purge_blocks(self._doc))

    def insert_block(self, name: str, insert: Point, **options: Any) -> str:
        """Insert a block reference and return its handle; see ``blocks.insert_block``."""
        return self._edited(block_ops.insert_block(self._doc, name, insert, **options))

    def inserts(
        self, layout: str = "Model", *, block: str | None = None, name: str | None = None
    ) -> list[BlockRef]:
        """Return the block references of a layout (or block), optionally of one block."""
        return block_ops.list_inserts(self._doc, layout=layout, block=block, name=name)

    def insert(self, handle: str) -> BlockRef:
        """Return one block reference by handle."""
        return block_ops.get_insert(self._doc, handle)

    def attributes(self, handle: str) -> dict[str, str]:
        """Return the attribute values of a block reference as ``{tag: text}``."""
        return block_ops.get_attributes(self._doc, handle)

    def set_attributes(self, handle: str, values: Mapping[str, str]) -> dict[str, str]:
        """Set attribute values of a block reference and return all its attributes."""
        return block_ops.set_attributes(self._doc, handle, values)

    def modify_insert(
        self,
        handle: str,
        *,
        insert: Point | None = None,
        scale: Scale | None = None,
        rotation: float | None = None,
    ) -> BlockRef:
        """Change position, scale or rotation of a block reference."""
        return block_ops.modify_insert(
            self._doc, handle, insert=insert, scale=scale, rotation=rotation
        )

    def explode(
        self, handles: Iterable[str] | str, *, attributes: str = "text", recursive: bool = False
    ) -> list[str]:
        """Explode block references into their entities and return the new handles."""
        return self._edited(
            block_ops.explode(self._doc, handles, attributes=attributes, recursive=recursive)
        )

    # -- measurement and spatial queries --------------------------------------

    @staticmethod
    def distance(p1: Point, p2: Point) -> Distance:
        """Return the distance between two points with its components and angle."""
        return measure_ops.distance(p1, p2)

    def entity_length(self, handle: str) -> float:
        """Return the length (perimeter when closed) of an entity in drawing units."""
        return measure_ops.entity_length(self._doc, handle)

    def entity_area(self, handle: str, *, assume_closed: bool = False) -> float:
        """Return the area of a closed entity; see ``anydoc.formats.dxf.measure``."""
        return measure_ops.entity_area(self._doc, handle, assume_closed=assume_closed)

    def measure(self, handles: Iterable[str] | str) -> list[Measurement]:
        """Return length, area and extents of the given entities."""
        return measure_ops.measure_entities(self._doc, handles)

    def bounding_box(
        self,
        handles: Iterable[str] | str | None = None,
        *,
        layout: str = "Model",
        layer: str | None = None,
        types: Collection[str] | str | None = None,
        visible_only: bool = False,
    ) -> BoundingBox | None:
        """Return the extents of the given entities, or of a (filtered) layout."""
        return measure_ops.bounding_box(
            self._doc,
            handles,
            layout=layout,
            layer=layer,
            types=types,
            visible_only=visible_only,
        )

    def entities_at(
        self, point: Point, *, tolerance: float | None = None, **filters: Any
    ) -> list[PickHit]:
        """Return the entities near a picked point (XY), nearest first."""
        return measure_ops.entities_at(self._doc, point, tolerance=tolerance, **filters)

    def entities_in_window(
        self, corner1: Point, corner2: Point, *, crossing: bool = False, **filters: Any
    ) -> list[str]:
        """Return the handles of the entities inside (or crossing) a window."""
        return measure_ops.entities_in_window(
            self._doc, corner1, corner2, crossing=crossing, **filters
        )

    def _edited(self, result: _T) -> _T:
        self.refresh()
        return result

    def refresh(self) -> None:
        """Rebuild ``metadata`` after the drawing was edited."""
        self.metadata = self._build_metadata()

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
                        items.append(
                            TextItem("ATTRIB", decode_text(att.dxf.text), att.dxf.layer, name)
                        )
        return [t for t in items if t.text]

    # -- rendering ---------------------------------------------------------

    def render(
        self,
        fmt: str = "svg",
        *,
        layout: str = "Model",
        layers: Collection[str] | None = None,
        dpi: float = 150.0,
        path: Path | None = None,
    ) -> bytes:
        """Render a layout to ``svg``, ``png`` or ``pdf`` and return the bytes.

        ``layers`` limits output to those layer names; ``dpi`` applies to PNG. If ``path``
        is given the bytes are also written there.
        """
        data = render_layout(self._doc, fmt, layout_name=layout, layers=layers, dpi=dpi)
        if path is not None:
            path.write_bytes(data)
        return data

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

    def to_bytes(self) -> bytes:
        """Serialise the drawing as ASCII DXF bytes in the drawing's own encoding."""
        stream = io.StringIO()
        self._doc.write(stream)
        return stream.getvalue().encode(self._doc.output_encoding, errors="dxfreplace")

    def close(self) -> None:
        """Release resources (nothing to close for ezdxf)."""


class DxfHandler(FormatHandler):
    """Handler for AutoCAD ``.dxf`` drawings (ASCII and binary)."""

    format_id: ClassVar[str] = "dxf"
    extensions: ClassVar[tuple[str, ...]] = ("dxf",)
    mime_types: ClassVar[tuple[str, ...]] = (_DXF_MIME, "application/dxf")
    capabilities: ClassVar[frozenset[Capability]] = frozenset(
        {
            Capability.READ,
            Capability.WRITE,
            Capability.EDIT,
            Capability.EXTRACT_TEXT,
            Capability.RENDER,
        }
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
