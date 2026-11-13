"""DXF blocks: define, insert, read/write attributes and explode block references.

All functions operate on an ``ezdxf`` drawing. Block names are matched
case-insensitively, as in AutoCAD; block references (INSERT entities) are addressed
by their handle. Anonymous blocks (names starting with ``*``, e.g. dimension
geometry) and layout blocks are never listed or changed.

A block definition holds entities relative to its ``base_point``; a block reference
places the definition at ``insert`` with a scale and rotation (degrees,
counter-clockwise). Attribute definitions (ATTDEF) in the block become attributes
(ATTRIB) of every new reference, filled from the given values or the default;
constant attribute definitions are part of the block geometry and get no ATTRIB.
"""

from __future__ import annotations

import math
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from ezdxf.lldxf import validator
from ezdxf.math import Matrix44

from anydoc.formats.dxf import entities as entity_ops
from anydoc.formats.dxf.entities import Point, decode_text
from anydoc.formats.dxf.layers import _iter_entities

__all__ = [
    "AttDef",
    "BlockDetail",
    "BlockRef",
    "add_attdef",
    "define_block",
    "delete_block",
    "explode",
    "get_attributes",
    "get_block",
    "get_insert",
    "insert_block",
    "list_blocks",
    "list_inserts",
    "modify_insert",
    "purge_blocks",
    "rename_block",
    "set_attributes",
]

Scale = float | Sequence[float]

_DIMSTYLE_BLOCKS = ("dimblk", "dimblk1", "dimblk2", "dimldrblk")
_EXPLODE_ATTRIBUTES = ("text", "drop")


@dataclass(frozen=True)
class AttDef:
    """An attribute definition (ATTDEF) of a block."""

    tag: str
    default: str
    prompt: str
    insert: tuple[float, float, float]
    height: float
    rotation: float
    invisible: bool
    constant: bool
    layer: str
    handle: str

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serialisable dict."""
        return {
            "tag": self.tag,
            "default": self.default,
            "prompt": self.prompt,
            "insert": list(self.insert),
            "height": self.height,
            "rotation": self.rotation,
            "invisible": self.invisible,
            "constant": self.constant,
            "layer": self.layer,
            "handle": self.handle,
        }


@dataclass(frozen=True)
class BlockDetail:
    """A block definition.

    ``entity_counts`` counts the entities by DXF type; ``references`` is the number
    of INSERT entities (in all layouts and blocks) that refer to the block.
    """

    name: str
    base_point: tuple[float, float, float]
    entity_count: int
    entity_counts: dict[str, int]
    attdefs: tuple[AttDef, ...]
    references: int
    description: str = ""

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serialisable dict."""
        return {
            "name": self.name,
            "base_point": list(self.base_point),
            "entity_count": self.entity_count,
            "entity_counts": dict(self.entity_counts),
            "attdefs": [a.to_dict() for a in self.attdefs],
            "references": self.references,
            "description": self.description,
        }


@dataclass(frozen=True)
class BlockRef:
    """A block reference (INSERT) with its placement and attribute values."""

    handle: str
    name: str
    layout: str
    layer: str
    insert: tuple[float, float, float]
    scale: tuple[float, float, float]
    rotation: float
    attributes: dict[str, str]

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serialisable dict."""
        return {
            "handle": self.handle,
            "name": self.name,
            "layout": self.layout,
            "layer": self.layer,
            "insert": list(self.insert),
            "scale": list(self.scale),
            "rotation": self.rotation,
            "attributes": dict(self.attributes),
        }


# -- helpers -----------------------------------------------------------------------


def _same(a: str, b: str) -> bool:
    return a.lower() == b.lower()


def _is_user_block(block: Any) -> bool:
    return not block.is_any_layout and not str(block.name).startswith("*")


def _block(doc: Any, name: str) -> Any:
    block = doc.blocks.get(name) if isinstance(name, str) and name else None
    if block is None or not _is_user_block(block):
        raise KeyError(f"No such block: {name}")
    return block


def _check_name(doc: Any, name: str) -> None:
    if not isinstance(name, str) or not name.strip():
        raise ValueError("Block name must not be empty")
    if len(name) > 255:
        raise ValueError("Block name must be at most 255 characters")
    if name.startswith("*"):
        raise ValueError(f"Block names starting with '*' are reserved: {name!r}")
    if name != name.strip() or not validator.is_valid_table_name(name):
        raise ValueError(f"Block name contains invalid characters: {name!r}")
    if doc.dxfversion < "AC1015" and " " in name:
        raise ValueError("Block names with spaces require DXF R2000 or later")


def _references(doc: Any, name: str) -> list[Any]:
    return [
        e
        for e in _iter_entities(doc)
        if e.dxftype() == "INSERT" and _same(str(e.dxf.get("name", "")), name)
    ]


def _reference_counts(doc: Any) -> Counter[str]:
    return Counter(
        str(e.dxf.get("name", "")).lower() for e in _iter_entities(doc) if e.dxftype() == "INSERT"
    )


def _attdef(entity: Any) -> AttDef:
    dxf = entity.dxf
    return AttDef(
        tag=decode_text(str(dxf.get("tag", ""))),
        default=decode_text(str(dxf.get("text", ""))),
        prompt=decode_text(str(dxf.get("prompt", ""))),
        insert=entity_ops._pt(entity.get_placement()[1]),
        height=float(dxf.get("height", 0.0)),
        rotation=float(dxf.get("rotation", 0.0)),
        invisible=bool(entity.is_invisible),
        constant=bool(entity.is_const),
        layer=str(dxf.get("layer", "0")),
        handle=str(dxf.handle),
    )


def _block_detail(block: Any, references: int) -> BlockDetail:
    counts = Counter(str(e.dxftype()) for e in block)
    return BlockDetail(
        name=str(block.name),
        base_point=entity_ops._pt(block.block.dxf.base_point),
        entity_count=len(block),
        entity_counts=dict(sorted(counts.items())),
        attdefs=tuple(_attdef(e) for e in block if e.dxftype() == "ATTDEF"),
        references=references,
        description=decode_text(str(block.block.dxf.get("description", ""))),
    )


def _scale(value: Any) -> tuple[float, float, float]:
    """Validate a scale: one factor or ``(sx, sy)`` / ``(sx, sy, sz)``, all non-zero."""
    if isinstance(value, str):
        raise ValueError(f"Scale must be a number or (sx, sy[, sz]), got {value!r}")
    if isinstance(value, Sequence):
        if len(value) not in (2, 3):
            raise ValueError(f"Scale must be a number or (sx, sy[, sz]), got {value!r}")
        factors = [entity_ops._number(v, "Scale") for v in value]
        if len(factors) == 2:
            factors.append(1.0)
    else:
        factors = [entity_ops._number(value, "Scale")] * 3
    if any(f == 0 for f in factors):
        raise ValueError(f"Scale factors must not be 0, got {value!r}")
    return factors[0], factors[1], factors[2]


def _text_value(value: Any, what: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{what} must be a string, got {value!r}")
    if "\n" in value or "\r" in value:
        raise ValueError(f"{what} must be a single line")
    return value


def _insert_entity(doc: Any, handle: str) -> Any:
    entity = entity_ops._get(doc, handle)
    if entity.dxftype() != "INSERT":
        raise ValueError(f"Entity {entity.dxf.handle} is a {entity.dxftype()}, not an INSERT")
    return entity


def _attribute_values(insert: Any) -> dict[str, str]:
    return {
        decode_text(str(a.dxf.get("tag", ""))): decode_text(str(a.dxf.get("text", "")))
        for a in insert.attribs
    }


def _block_ref(insert: Any) -> BlockRef:
    dxf = insert.dxf
    return BlockRef(
        handle=str(dxf.handle),
        name=str(dxf.get("name", "")),
        layout=entity_ops._owner_name(insert),
        layer=str(dxf.get("layer", "0")),
        insert=entity_ops._pt(dxf.insert),
        scale=(
            float(dxf.get("xscale", 1.0)),
            float(dxf.get("yscale", 1.0)),
            float(dxf.get("zscale", 1.0)),
        ),
        rotation=float(dxf.get("rotation", 0.0)),
        attributes=_attribute_values(insert),
    )


def _uses(doc: Any, name: str, target: str, seen: set[str] | None = None) -> bool:
    """Return whether block ``name`` is, or (indirectly) contains a reference to, ``target``."""
    if _same(name, target):
        return True
    seen = set() if seen is None else seen
    if name.lower() in seen:
        return False
    seen.add(name.lower())
    block = doc.blocks.get(name)
    if block is None:
        return False
    return any(
        e.dxftype() == "INSERT" and _uses(doc, str(e.dxf.get("name", "")), target, seen)
        for e in block
    )


# -- block definitions ---------------------------------------------------------------


def list_blocks(doc: Any) -> list[BlockDetail]:
    """Return every user block definition (layout and anonymous blocks excluded)."""
    refs = _reference_counts(doc)
    return [
        _block_detail(block, refs[str(block.name).lower()])
        for block in doc.blocks
        if _is_user_block(block)
    ]


def get_block(doc: Any, name: str) -> BlockDetail:
    """Return one block definition by name; raises ``KeyError`` when it does not exist."""
    block = _block(doc, name)
    return _block_detail(block, len(_references(doc, str(block.name))))


def define_block(
    doc: Any,
    name: str,
    *,
    base_point: Point = (0.0, 0.0),
    handles: Iterable[str] | str | None = None,
    keep: bool = False,
    replace: bool = False,
    description: str = "",
) -> BlockDetail:
    """Create a block definition, optionally from existing entities.

    ``handles`` names entities of one layout that become the block content: they
    are moved into the block (or copied with ``keep=True``) with their coordinates
    unchanged, so ``base_point`` should be a point of that geometry. With
    ``replace=True`` a reference to the new block is inserted at ``base_point`` in
    their place, leaving the drawing visually unchanged. Entities on locked layers
    cannot be moved. Without ``handles`` an empty block is created; fill it with the
    ``block=`` option of the ``add_*`` functions and ``add_attdef``.
    """
    _check_name(doc, name)
    if doc.blocks.get(name) is not None:
        raise ValueError(f"Block already exists: {name}")
    base = entity_ops._point(base_point, "Base point")
    text = _text_value(description, "Description")
    if keep and replace:
        raise ValueError("Use either keep or replace, not both")
    if handles is None and replace:
        raise ValueError("replace needs the handles of the entities to replace")
    sources: list[Any] = []
    if handles is not None:
        sources = entity_ops._resolve(doc, handles, check_locked=not keep)
        if not sources:
            raise ValueError("No entities given for the block")
        owners = {entity_ops._owner_name(e) for e in sources}
        if len(owners) > 1:
            raise ValueError("Entities of a block must come from a single layout")
    owner: Any = sources[0].get_layout() if sources else None

    block = doc.blocks.new(name, base_point=base)
    if text:
        block.block.dxf.description = text
    for source in sources:
        if keep:
            try:
                clone = doc.entitydb.duplicate_entity(source)
            except Exception as exc:  # ezdxf raises DXFTypeError for non-copyable entities
                doc.blocks.delete_block(name, safe=False)
                raise ValueError(
                    f"Entity {source.dxf.handle} ({source.dxftype()}) cannot be copied"
                ) from exc
            block.add_entity(clone)
        else:
            owner.move_to_layout(source, block)
    if replace and owner is not None:
        owner.add_blockref(str(block.name), base, dxfattribs={"layer": "0"})
    return get_block(doc, name)


def add_attdef(
    doc: Any,
    block: str,
    tag: str,
    insert: Point,
    *,
    default: str = "",
    prompt: str = "",
    height: float = 2.5,
    rotation: float = 0.0,
    invisible: bool = False,
    constant: bool = False,
    layer: str | None = None,
    color: int | str | None = None,
) -> str:
    """Add an attribute definition to a block and return its handle.

    ``tag`` identifies the attribute (no whitespace, unique in the block,
    case-insensitive); ``default`` is the value of new references that do not set
    it; ``prompt`` is the text CAD applications ask with. ``invisible`` hides the
    attribute; a ``constant`` attribute always shows ``default`` and cannot be set
    per reference. Existing references are not updated.
    """
    layout = _block(doc, block)
    if not isinstance(tag, str) or not tag or any(ch.isspace() for ch in tag):
        raise ValueError(f"Attribute tag must be non-empty without whitespace, got {tag!r}")
    if any(_same(a.tag, tag) for a in get_block(doc, block).attdefs):
        raise ValueError(f"Block {layout.name} already has an attribute tagged {tag}")
    point = entity_ops._point(insert, "Insert point")
    size = entity_ops._positive(height, "Text height")
    angle = entity_ops._number(rotation, "Rotation")
    value = _text_value(default, "Default value")
    question = _text_value(prompt, "Prompt")
    attribs, rgb = entity_ops._common_attribs(doc, layer, color, None)
    if layer is None:
        attribs["layer"] = "0"
    attribs.update({"height": size, "rotation": angle, "prompt": question})
    attdef = layout.add_attdef(tag, point, value, dxfattribs=attribs)
    attdef.is_invisible = bool(invisible)
    attdef.is_const = bool(constant)
    return entity_ops._finish(attdef, rgb)


def rename_block(doc: Any, old: str, new: str) -> BlockDetail:
    """Rename a block and update every reference to it (in layouts and blocks)."""
    block = _block(doc, old)
    old_name = str(block.name)
    _check_name(doc, new)
    if old_name == new:
        return get_block(doc, new)
    existing = doc.blocks.get(new)
    if existing is not None and existing is not block:
        raise ValueError(f"Block already exists: {new}")
    references = _references(doc, old_name)
    doc.blocks.rename_block(old_name, new)
    for reference in references:
        reference.dxf.name = new
    return get_block(doc, new)


def _protected_names(doc: Any) -> set[str]:
    """Lower-case names of blocks that dimension styles use as arrowheads."""
    names: set[str] = set()
    for style in doc.dimstyles:
        for key in _DIMSTYLE_BLOCKS:
            value = style.dxf.get(key)
            if value:
                names.add(str(value).lower())
    return names


def delete_block(doc: Any, name: str, *, delete_references: bool = False) -> int:
    """Delete a block definition and return the number of references removed.

    A block that is still referenced raises ``ValueError`` unless
    ``delete_references=True``, which deletes its INSERT entities (everywhere,
    including inside other blocks) first; references on locked layers block the
    deletion.
    """
    block = _block(doc, name)
    canonical = str(block.name)
    references = _references(doc, canonical)
    if references and not delete_references:
        raise ValueError(
            f"Block {canonical} has {len(references)} references; "
            "use delete_references to delete them too"
        )
    if canonical.lower() in _protected_names(doc):
        raise ValueError(f"Block {canonical} is used by a dimension style")
    for reference in references:
        entity_ops._check_unlocked(doc, reference)
    for reference in references:
        owner = reference.get_layout()
        if owner is not None:
            owner.delete_entity(reference)
    doc.blocks.delete_block(canonical, safe=False)
    return len(references)


def purge_blocks(doc: Any) -> list[str]:
    """Delete every unreferenced user block and return the removed names.

    Runs until nothing is left to remove, so blocks only used by purged blocks go
    too. Arrowhead blocks (``_NAME``) and blocks used by dimension styles are kept.
    """
    removed: list[str] = []
    protected = _protected_names(doc)
    while True:
        used = _reference_counts(doc)
        unused = [
            str(block.name)
            for block in doc.blocks
            if _is_user_block(block)
            and not str(block.name).startswith("_")
            and str(block.name).lower() not in protected
            and not used[str(block.name).lower()]
            and not (int(block.block.dxf.get("flags", 0)) & 0b111100)  # xrefs / overlays
        ]
        if not unused:
            return removed
        for name in unused:
            doc.blocks.delete_block(name, safe=False)
            removed.append(name)


# -- block references ----------------------------------------------------------------


def _resolve_values(
    block_name: str, attdefs: Sequence[AttDef], values: Mapping[str, str] | None
) -> dict[str, str]:
    """Map attribute values onto the canonical tags of the block's variable attributes."""
    variable = {a.tag.lower(): a.tag for a in attdefs if not a.constant}
    constant = {a.tag.lower() for a in attdefs if a.constant}
    out: dict[str, str] = {}
    for tag, value in (values or {}).items():
        key = str(tag).lower()
        if key in constant:
            raise ValueError(f"Attribute {tag} of block {block_name} is constant")
        if key not in variable:
            raise KeyError(f"Block {block_name} has no attribute tagged {tag}")
        out[variable[key]] = _text_value(value, f"Attribute {tag}")
    return out


def insert_block(
    doc: Any,
    name: str,
    insert: Point,
    *,
    scale: Scale = 1.0,
    rotation: float = 0.0,
    attributes: Mapping[str, str] | None = None,
    layout: str = "Model",
    block: str | None = None,
    layer: str | None = None,
    color: int | str | None = None,
    linetype: str | None = None,
) -> str:
    """Insert a reference to block ``name`` at ``insert`` and return its handle.

    ``scale`` is one factor or ``(sx, sy[, sz])`` (negative values mirror);
    ``rotation`` is in degrees. ``attributes`` maps attribute tags
    (case-insensitive) to values; attributes not listed get their default.
    The reference goes into ``layout``, or into another block when ``block`` is
    given (a block cannot contain itself, directly or indirectly).
    """
    definition = _block(doc, name)
    canonical = str(definition.name)
    point = entity_ops._point(insert, "Insert point")
    sx, sy, sz = _scale(scale)
    angle = entity_ops._number(rotation, "Rotation")
    attdefs = [_attdef(e) for e in definition if e.dxftype() == "ATTDEF"]
    values = _resolve_values(canonical, attdefs, attributes)
    space = entity_ops._space(doc, layout, block)
    if block is not None and _uses(doc, canonical, str(space.name)):
        raise ValueError(f"Block {space.name} cannot contain a reference to itself ({canonical})")
    attribs, rgb = entity_ops._common_attribs(doc, layer, color, linetype)
    attribs.update({"xscale": sx, "yscale": sy, "zscale": sz, "rotation": angle})
    reference = space.add_blockref(canonical, point, dxfattribs=attribs)
    matrix = reference.matrix44()
    for entity in definition:
        if entity.dxftype() != "ATTDEF" or entity.is_const or not entity.dxf.hasattr("tag"):
            continue
        tag = str(entity.dxf.tag)
        text = values.get(decode_text(tag), str(entity.dxf.get("text", "")))
        dxfattribs = entity.dxfattribs(drop={"prompt", "handle", "owner", "tag", "text", "insert"})
        attrib = reference.add_attrib(tag, text, entity.dxf.insert, dxfattribs)
        attrib.transform(matrix)
    return entity_ops._finish(reference, rgb)


def list_inserts(
    doc: Any,
    *,
    layout: str = "Model",
    block: str | None = None,
    name: str | None = None,
) -> list[BlockRef]:
    """Return the block references in a layout (or in ``block``), optionally of one block."""
    if name is not None:
        name = str(_block(doc, name).name)
    return [
        _block_ref(e)
        for e in entity_ops._space(doc, layout, block)
        if e.dxftype() == "INSERT" and (name is None or _same(str(e.dxf.get("name", "")), name))
    ]


def get_insert(doc: Any, handle: str) -> BlockRef:
    """Return one block reference by handle (``ValueError`` if it is not an INSERT)."""
    return _block_ref(_insert_entity(doc, handle))


def get_attributes(doc: Any, handle: str) -> dict[str, str]:
    """Return the attribute values of a block reference as ``{tag: text}``."""
    return _attribute_values(_insert_entity(doc, handle))


def set_attributes(doc: Any, handle: str, values: Mapping[str, str]) -> dict[str, str]:
    """Set attribute values of a block reference and return all its attributes.

    Tags are matched case-insensitively; an unknown tag raises ``KeyError`` and a
    reference on a locked layer ``ValueError``. Everything is validated before any
    value is changed.
    """
    reference = _insert_entity(doc, handle)
    entity_ops._check_unlocked(doc, reference)
    by_tag = {decode_text(str(a.dxf.get("tag", ""))).lower(): a for a in reference.attribs}
    updates: list[tuple[Any, str]] = []
    for tag, value in values.items():
        attrib = by_tag.get(str(tag).lower())
        if attrib is None:
            raise KeyError(f"Block reference {reference.dxf.handle} has no attribute tagged {tag}")
        updates.append((attrib, _text_value(value, f"Attribute {tag}")))
    for attrib, text in updates:
        attrib.dxf.text = text
    return _attribute_values(reference)


def modify_insert(
    doc: Any,
    handle: str,
    *,
    insert: Point | None = None,
    scale: Scale | None = None,
    rotation: float | None = None,
) -> BlockRef:
    """Change the placement of a block reference; ``None`` leaves a value untouched.

    Attached attributes follow the reference. Changing ``scale`` or ``rotation``
    requires the reference to lie in the XY plane.
    """
    reference = _insert_entity(doc, handle)
    entity_ops._check_unlocked(doc, reference)
    point = None if insert is None else entity_ops._point(insert, "Insert point")
    factors = None if scale is None else _scale(scale)
    angle = None if rotation is None else entity_ops._number(rotation, "Rotation")
    dxf = reference.dxf
    if (factors is not None or angle is not None) and not dxf.extrusion.isclose((0, 0, 1)):
        raise ValueError("Scale and rotation can only be changed for references in the XY plane")

    old_angle = float(dxf.get("rotation", 0.0))
    origin = entity_ops._pt(dxf.insert)
    matrix = Matrix44.translate(-origin[0], -origin[1], -origin[2])
    matrix @= Matrix44.z_rotate(math.radians(-old_angle))
    if factors is not None:
        old = (dxf.get("xscale", 1.0), dxf.get("yscale", 1.0), dxf.get("zscale", 1.0))
        matrix @= Matrix44.scale(*(new / cur for new, cur in zip(factors, old, strict=True)))
    matrix @= Matrix44.z_rotate(math.radians(old_angle if angle is None else angle))
    target = origin if point is None else point
    matrix @= Matrix44.translate(*target)
    for attrib in reference.attribs:
        attrib.transform(matrix)
    dxf.insert = target
    if factors is not None:
        dxf.xscale, dxf.yscale, dxf.zscale = factors
    if angle is not None:
        dxf.rotation = angle
    return _block_ref(reference)


def explode(
    doc: Any,
    handles: Iterable[str] | str,
    *,
    attributes: str = "text",
    recursive: bool = False,
) -> list[str]:
    """Replace block references by copies of the block entities; return the new handles.

    The entities are transformed to where the reference showed them (a
    non-uniformly scaled CIRCLE/ARC becomes an ELLIPSE) and keep their own layer and
    colour. ``attributes="text"`` turns the attribute values into TEXT entities,
    ``"drop"`` discards them; attribute definitions are not copied. Nested
    references stay references unless ``recursive=True``. References on locked
    layers cannot be exploded; nothing is changed when a handle is invalid.
    """
    if attributes not in _EXPLODE_ATTRIBUTES:
        raise ValueError(
            f"attributes must be one of {', '.join(_EXPLODE_ATTRIBUTES)}, got {attributes!r}"
        )
    references = entity_ops._resolve(doc, handles)
    for reference in references:
        if reference.dxftype() != "INSERT":
            raise ValueError(
                f"Entity {reference.dxf.handle} is a {reference.dxftype()}, not an INSERT"
            )
        _check_explodable(doc, reference, recursive, set())
    out: list[str] = []
    queue = list(references)
    while queue:
        reference = queue.pop(0)
        if attributes == "drop":
            for attrib in list(reference.attribs):
                reference.delete_attrib(str(attrib.dxf.tag))
        for entity in reference.explode():
            if recursive and entity.dxftype() == "INSERT":
                queue.append(entity)
            else:
                out.append(str(entity.dxf.handle))
    return out


def _check_explodable(doc: Any, reference: Any, recursive: bool, seen: set[str]) -> None:
    dxf = reference.dxf
    name = str(dxf.get("name", ""))
    definition = doc.blocks.get(name)
    if definition is None:
        raise ValueError(f"Block reference {dxf.handle} refers to a missing block: {name}")
    # Only X/Y matter for planar geometry: a differing Z scale keeps circles circular.
    factors = [abs(float(dxf.get(key, 1.0))) for key in ("xscale", "yscale")]
    uniform = max(factors) - min(factors) <= 1e-9 * max(factors)
    if not uniform and doc.dxfversion < "AC1015":
        raise ValueError(
            f"Exploding the non-uniformly scaled reference {dxf.handle} requires DXF R2000 or later"
        )
    if recursive and name.lower() not in seen:
        seen.add(name.lower())
        for entity in definition:
            if entity.dxftype() == "INSERT":
                _check_explodable(doc, entity, recursive, seen)
