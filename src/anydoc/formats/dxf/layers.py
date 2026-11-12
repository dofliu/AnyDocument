"""DXF layer management: add, rename, delete and change layer properties.

All functions operate on an ``ezdxf`` drawing. Layer names are matched
case-insensitively, as in AutoCAD. Colours are AutoCAD Color Index (ACI) values
(1-255), a colour name (``red``, ``yellow``, ``green``, ``cyan``, ``blue``,
``magenta``, ``white``) or a ``#RRGGBB`` true colour.
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

from ezdxf.lldxf import validator
from ezdxf.tools import standards

__all__ = [
    "COLOR_NAMES",
    "LayerInfo",
    "add_layer",
    "current_layer",
    "delete_layer",
    "get_layer",
    "layer_usage",
    "list_layers",
    "parse_color",
    "purge_layers",
    "rename_layer",
    "set_current_layer",
    "set_layer",
]

COLOR_NAMES: dict[str, int] = {
    "red": 1,
    "yellow": 2,
    "green": 3,
    "cyan": 4,
    "blue": 5,
    "magenta": 6,
    "white": 7,
    "black": 7,
    "gray": 8,
    "grey": 8,
}

_PROTECTED = ("0", "defpoints")
_HEX_RE = re.compile(r"#?([0-9a-fA-F]{6})")


@dataclass(frozen=True)
class LayerInfo:
    """A drawing layer.

    ``color`` is the absolute ACI index (1-255); ``on`` is visibility; ``true_color``
    is ``#RRGGBB`` when the layer carries a true colour (which overrides ``color``).
    """

    name: str
    color: int
    linetype: str
    on: bool
    frozen: bool
    locked: bool
    true_color: str | None = None


def parse_color(value: int | str) -> tuple[int | None, tuple[int, int, int] | None]:
    """Parse a colour into ``(aci, rgb)``; exactly one of the two is not ``None``.

    Accepts an ACI index (int or digit string, 1-255), a name from ``COLOR_NAMES``
    or a ``#RRGGBB`` string. Raises ``ValueError`` for anything else.
    """
    if isinstance(value, bool):
        raise ValueError(f"Invalid layer colour: {value!r}")
    if isinstance(value, str):
        text = value.strip()
        if text.lower() in COLOR_NAMES:
            return COLOR_NAMES[text.lower()], None
        if text.isdigit():
            value = int(text)
        else:
            match = _HEX_RE.fullmatch(text)
            if match is None or not text.startswith("#"):
                raise ValueError(f"Invalid layer colour: {value!r}")
            digits = match.group(1)
            return None, (int(digits[0:2], 16), int(digits[2:4], 16), int(digits[4:6], 16))
    if not 1 <= value <= 255:
        raise ValueError(f"Layer colour index must be 1-255, got {value}")
    return value, None


def _info(layer: Any) -> LayerInfo:
    rgb = layer.rgb
    return LayerInfo(
        name=str(layer.dxf.name),
        color=int(layer.color),
        linetype=str(layer.dxf.linetype),
        on=bool(layer.is_on()),
        frozen=bool(layer.is_frozen()),
        locked=bool(layer.is_locked()),
        true_color=None if rgb is None else "#{:02X}{:02X}{:02X}".format(*rgb),
    )


def _get(doc: Any, name: str) -> Any:
    if not doc.layers.has_entry(name):
        raise KeyError(f"No such layer: {name}")
    return doc.layers.get(name)


def _check_name(name: str) -> None:
    if not name or not name.strip():
        raise ValueError("Layer name must not be empty")
    if len(name) > 255:
        raise ValueError("Layer name must be at most 255 characters")
    if not validator.is_valid_layer_name(name):
        raise ValueError(f"Layer name contains invalid characters: {name!r}")


def _same(a: str, b: str) -> bool:
    return a.lower() == b.lower()


def _iter_owned(doc: Any) -> Iterator[tuple[Any, Any]]:
    """Yield ``(entity, (space, insert))`` for every graphical entity.

    ``space`` is the layout or block that holds the entity; ``insert`` is the INSERT an
    attached ATTRIB belongs to (``None`` for every other entity).
    """
    for space in doc.layouts_and_blocks():
        for entity in space:
            yield entity, (space, None)
            if entity.dxftype() == "INSERT":
                for attrib in entity.attribs:
                    yield attrib, (space, entity)


def _iter_entities(doc: Any) -> Iterator[Any]:
    """Yield every graphical entity in all layouts and blocks, including INSERT attributes."""
    for entity, _ in _iter_owned(doc):
        yield entity


def _entity_layer(entity: Any) -> str:
    return str(entity.dxf.get("layer", "0"))


def _ensure_linetype(doc: Any, name: str) -> str:
    """Return the canonical name of linetype ``name``, loading a standard one if needed."""
    if doc.linetypes.has_entry(name):
        return str(doc.linetypes.get(name).dxf.name)
    for std_name, description, pattern in standards.linetypes():
        if _same(std_name, name):
            doc.linetypes.add(std_name, pattern=pattern, description=description)
            return str(std_name)
    raise ValueError(f"Unknown linetype: {name}")


def _apply_color(layer: Any, color: int | str) -> None:
    aci, rgb = parse_color(color)
    if rgb is not None:
        layer.rgb = rgb
        return
    was_off = layer.is_off()
    layer.color = aci
    layer.dxf.discard("true_color")
    if was_off:
        layer.off()


def current_layer(doc: Any) -> str:
    """Return the name of the current layer (``$CLAYER``, default ``0``)."""
    return str(doc.header.get("$CLAYER", "0") or "0")


def set_current_layer(doc: Any, name: str) -> str:
    """Make ``name`` the current layer; frozen layers cannot be current."""
    layer = _get(doc, name)
    if layer.is_frozen():
        raise ValueError(f"Cannot make frozen layer current: {layer.dxf.name}")
    doc.header["$CLAYER"] = str(layer.dxf.name)
    return str(layer.dxf.name)


def list_layers(doc: Any) -> list[LayerInfo]:
    """Return every layer of the drawing in table order."""
    return [_info(layer) for layer in doc.layers]


def get_layer(doc: Any, name: str) -> LayerInfo:
    """Return one layer; raises ``KeyError`` when it does not exist."""
    return _info(_get(doc, name))


def layer_usage(doc: Any) -> dict[str, int]:
    """Count the entities on each layer across all layouts and blocks.

    Every layer in the layer table is included (with ``0`` when unused); names used
    by entities but missing from the table are reported under the entity's spelling.
    """
    canonical = {str(layer.dxf.name).lower(): str(layer.dxf.name) for layer in doc.layers}
    counts: Counter[str] = Counter({name: 0 for name in canonical.values()})
    for entity in _iter_entities(doc):
        name = _entity_layer(entity)
        counts[canonical.setdefault(name.lower(), name)] += 1
    return dict(counts)


def add_layer(
    doc: Any,
    name: str,
    *,
    color: int | str = 7,
    linetype: str = "Continuous",
    on: bool = True,
    frozen: bool = False,
    locked: bool = False,
) -> LayerInfo:
    """Create a layer and return it.

    Raises ``ValueError`` when the name is invalid or already exists, the colour is
    invalid or the linetype is neither defined in the drawing nor a standard one.
    """
    _check_name(name)
    if doc.layers.has_entry(name):
        raise ValueError(f"Layer already exists: {name}")
    parse_color(color)
    resolved = _ensure_linetype(doc, linetype)
    layer = doc.layers.add(name, linetype=resolved)
    _apply_color(layer, color)
    if not on:
        layer.off()
    if frozen:
        layer.freeze()
    if locked:
        layer.lock()
    return _info(layer)


def set_layer(
    doc: Any,
    name: str,
    *,
    color: int | str | None = None,
    linetype: str | None = None,
    on: bool | None = None,
    frozen: bool | None = None,
    locked: bool | None = None,
) -> LayerInfo:
    """Change properties of an existing layer; ``None`` leaves a property untouched.

    The current layer cannot be frozen. All arguments are validated before any
    change is applied.
    """
    layer = _get(doc, name)
    if color is not None:
        parse_color(color)
    if frozen and _same(str(layer.dxf.name), current_layer(doc)):
        raise ValueError(f"Cannot freeze the current layer: {layer.dxf.name}")
    resolved = None if linetype is None else _ensure_linetype(doc, linetype)
    if color is not None:
        _apply_color(layer, color)
    if resolved is not None:
        layer.dxf.linetype = resolved
    if on is not None:
        if on:
            layer.on()
        else:
            layer.off()
    if frozen is not None:
        if frozen:
            layer.freeze()
        else:
            layer.thaw()
    if locked is not None:
        if locked:
            layer.lock()
        else:
            layer.unlock()
    return _info(layer)


def rename_layer(doc: Any, old: str, new: str) -> LayerInfo:
    """Rename a layer and update every entity (and ``$CLAYER``) that refers to it.

    Layers ``0`` and ``Defpoints`` cannot be renamed. Renaming to a different
    spelling of the same name (case change) is allowed.
    """
    layer = _get(doc, old)
    old_name = str(layer.dxf.name)
    _check_name(new)
    if old_name.lower() in _PROTECTED:
        raise ValueError(f"Cannot rename layer: {old_name}")
    if new == old_name:
        return _info(layer)
    was_current = _same(current_layer(doc), old_name)
    if _same(new, old_name):
        layer.dxf.name = new  # case-only change: same table key
    else:
        if doc.layers.has_entry(new):
            raise ValueError(f"Layer already exists: {new}")
        layer.rename(new)
    for entity in _iter_entities(doc):
        if _same(_entity_layer(entity), old_name):
            entity.dxf.layer = new
    if was_current:
        doc.header["$CLAYER"] = new
    return _info(layer)


def delete_layer(
    doc: Any,
    name: str,
    *,
    move_to: str | None = None,
    delete_entities: bool = False,
) -> int:
    """Delete a layer and return the number of entities moved or deleted.

    A layer that still holds entities is only deleted when ``move_to`` names the
    layer that receives them or ``delete_entities`` is true. Layers ``0`` and
    ``Defpoints`` and the current layer cannot be deleted.
    """
    layer = _get(doc, name)
    layer_name = str(layer.dxf.name)
    if layer_name.lower() in _PROTECTED:
        raise ValueError(f"Cannot delete layer: {layer_name}")
    if _same(layer_name, current_layer(doc)):
        raise ValueError(f"Cannot delete the current layer: {layer_name}")
    if move_to is not None and delete_entities:
        raise ValueError("Use either move_to or delete_entities, not both")
    target: str | None = None
    if move_to is not None:
        target = str(_get(doc, move_to).dxf.name)
        if _same(target, layer_name):
            raise ValueError("Cannot move entities to the layer being deleted")
    affected = [(e, owner) for e, owner in _iter_owned(doc) if _same(_entity_layer(e), layer_name)]
    if affected and target is None and not delete_entities:
        raise ValueError(
            f"Layer {layer_name} is in use by {len(affected)} entities; "
            "pass move_to or delete_entities"
        )
    for entity, (space, insert) in affected:
        if target is not None:
            entity.dxf.layer = target
        elif not entity.is_alive:
            continue  # attribute of an INSERT that was deleted just before
        elif insert is not None:
            insert.attribs.remove(entity)
            entity.destroy()
        else:
            space.delete_entity(entity)
    doc.layers.remove(layer_name)
    return len(affected)


def purge_layers(doc: Any) -> list[str]:
    """Delete every unused layer (except ``0``, ``Defpoints`` and the current one).

    Returns the names of the removed layers.
    """
    usage = layer_usage(doc)
    current = current_layer(doc)
    removed: list[str] = []
    for layer in list(doc.layers):
        name = str(layer.dxf.name)
        if name.lower() in _PROTECTED or _same(name, current) or usage.get(name, 0):
            continue
        doc.layers.remove(name)
        removed.append(name)
    return removed
