"""PDF AcroForm support: list, fill and flatten form fields.

Functions take and return PDF ``bytes`` like :mod:`anydoc.formats.pdf.pages`. Text and
choice fields are filled through pypdf; checkboxes and radio groups are switched by setting
``/V`` and the widgets' ``/AS`` appearance state. Flattening draws the field values onto the
page content (CJK capable) and removes the widgets and the ``/AcroForm`` dictionary.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from pypdf import PdfWriter
from pypdf.generic import ArrayObject, BooleanObject, NameObject, TextStringObject

from anydoc.core.errors import OperationNotSupportedError
from anydoc.formats.pdf.pages import _dump, _reader

__all__ = ["FormField", "fill_form", "flatten_form", "has_form", "list_form_fields"]

_FF_READONLY = 1
_FF_REQUIRED = 2
_FF_RADIO = 1 << 15
_FF_PUSHBUTTON = 1 << 16
_FF_EDIT = 1 << 18
_FF_MULTILINE = 1 << 12


@dataclass(frozen=True)
class FormField:
    """One logical form field (a radio group is a single field)."""

    name: str
    type: str  # text | checkbox | radio | choice | button | signature
    value: str | bool | list[str] | None
    page: int | None  # 0-based page of the first widget
    rect: tuple[float, float, float, float] | None
    options: list[str] = field(default_factory=list)
    read_only: bool = False
    required: bool = False

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serialisable dict."""
        return asdict(self)


def _full_name(obj: Any) -> str:
    parts: list[str] = []
    seen = 0
    while obj is not None and seen < 50:
        o = obj.get_object()
        t = o.get("/T")
        if t is not None:
            parts.append(str(t))
        obj = o.get("/Parent")
        seen += 1
    return ".".join(reversed(parts))


def _inherited(obj: Any, key: str) -> Any:
    seen = 0
    while obj is not None and seen < 50:
        o = obj.get_object()
        if key in o:
            return o[key]
        obj = o.get("/Parent")
        seen += 1
    return None


def _kind(obj: Any) -> str:
    ft = _inherited(obj, "/FT")
    ff = int(_inherited(obj, "/Ff") or 0)
    if ft == "/Tx":
        return "text"
    if ft == "/Ch":
        return "choice"
    if ft == "/Sig":
        return "signature"
    if ft == "/Btn":
        if ff & _FF_PUSHBUTTON:
            return "button"
        return "radio" if ff & _FF_RADIO else "checkbox"
    return "text"


def _on_state(widget: Any) -> str | None:
    ap = widget.get_object().get("/AP")
    if ap is None:
        return None
    normal = ap.get_object().get("/N")
    if normal is None:
        return None
    for key in normal.get_object():
        if key != "/Off":
            return str(key)
    return None


def _widgets(reader: Any) -> list[tuple[int, Any]]:
    out: list[tuple[int, Any]] = []
    for i, page in enumerate(reader.pages):
        for ref in page.get("/Annots") or []:
            annot = ref.get_object()
            if annot.get("/Subtype") == "/Widget":
                out.append((i, annot))
    return out


def _rect(annot: Any) -> tuple[float, float, float, float] | None:
    r = annot.get("/Rect")
    if r is None:
        return None
    x0, y0, x1, y1 = (float(v) for v in r)
    return (min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1))


def _value(obj: Any, kind: str) -> str | bool | list[str] | None:
    v = _inherited(obj, "/V")
    if v is None:
        return False if kind == "checkbox" else None
    v = v.get_object() if hasattr(v, "get_object") else v
    if kind == "checkbox":
        return str(v) not in ("/Off", "")
    if kind == "radio":
        return None if str(v) == "/Off" else str(v).lstrip("/")
    if isinstance(v, list):
        return [str(x) for x in v]
    return str(v)


def _options(obj: Any, kind: str, widgets: list[Any]) -> list[str]:
    if kind == "radio":
        states: list[str] = []
        for w in widgets:
            s = _on_state(w)
            if s is not None:
                states.append(s.lstrip("/"))
        return states
    opt = _inherited(obj, "/Opt")
    if opt is None:
        return []
    out: list[str] = []
    for o in opt.get_object():
        o = o.get_object()
        out.append(str(o[0]) if isinstance(o, list) else str(o))
    return out


def _collect(reader: Any) -> dict[str, tuple[Any, list[tuple[int, Any]]]]:
    """Map field name -> (field-dict, [(page, widget), ...])."""
    fields: dict[str, tuple[Any, list[tuple[int, Any]]]] = {}
    for page, widget in _widgets(reader):
        owner = widget
        parent = widget.get("/Parent")
        if parent is not None and "/T" not in widget:
            owner = parent.get_object()
        name = _full_name(owner)
        if not name:
            continue
        if name not in fields:
            fields[name] = (owner, [])
        fields[name][1].append((page, widget))
    return fields


def has_form(data: bytes, password: str = "") -> bool:
    """Return whether the PDF contains AcroForm fields."""
    return bool(_collect(_reader(data, password)))


def list_form_fields(data: bytes, password: str = "") -> list[FormField]:
    """List the AcroForm fields of a PDF (empty list when there is no form)."""
    reader = _reader(data, password)
    result: list[FormField] = []
    for name, (owner, widgets) in _collect(reader).items():
        kind = _kind(owner)
        ff = int(_inherited(owner, "/Ff") or 0)
        first_page, first = widgets[0]
        result.append(
            FormField(
                name=name,
                type=kind,
                value=_value(owner, kind),
                page=first_page,
                rect=_rect(first),
                options=_options(owner, kind, [w for _, w in widgets]),
                read_only=bool(ff & _FF_READONLY),
                required=bool(ff & _FF_REQUIRED),
            )
        )
    return result


def _truthy(value: object) -> bool:
    if isinstance(value, str):
        return value.strip().lower() not in ("", "0", "off", "false", "no", "n")
    return bool(value)


def fill_form(
    data: bytes,
    values: dict[str, Any],
    *,
    flatten: bool = False,
    password: str = "",
) -> bytes:
    """Fill form fields and return the new PDF bytes.

    ``values`` maps field names to: text (``str``) for text fields, ``bool`` for checkboxes,
    the option name for radio groups, and an option (or list of options) for choice fields.
    With ``flatten=True`` the filled values are burned into the page and the form removed.

    Raises:
        KeyError: a name does not match any field.
        ValueError: a value is invalid for the field, or the field is read-only.
    """
    reader = _reader(data, password)
    known = _collect(reader)
    for name in values:
        if name not in known:
            raise KeyError(f"No such form field: {name!r}")
    writer = PdfWriter(clone_from=reader)
    fields = _collect(writer)  # operate on the writer's own objects
    for name, value in values.items():
        owner, widgets = fields[name]
        kind = _kind(owner)
        if int(_inherited(owner, "/Ff") or 0) & _FF_READONLY:
            raise ValueError(f"Field {name!r} is read-only")
        if kind == "checkbox":
            _set_checkbox(owner, [w for _, w in widgets], _truthy(value), name)
        elif kind == "radio":
            _set_radio(owner, [w for _, w in widgets], str(value), name)
        elif kind == "choice":
            _set_choice(owner, value, name)
        elif kind == "text":
            owner[NameObject("/V")] = TextStringObject(str(value))
            for _, w in widgets:
                w.pop("/AP", None)  # stale appearance; regenerated by viewers / flatten
        else:
            raise ValueError(f"Field {name!r} of type {kind} cannot be filled")
    acro = writer._root_object.get("/AcroForm")
    if acro is not None:
        acro.get_object()[NameObject("/NeedAppearances")] = BooleanObject(True)
    out = _dump(writer)
    return flatten_form(out) if flatten else out


def _set_checkbox(owner: Any, widgets: list[Any], on: bool, name: str) -> None:
    state = None
    for w in widgets:
        state = _on_state(w) or state
    state = state or "/Yes"
    target = state if on else "/Off"
    owner[NameObject("/V")] = NameObject(target)
    for w in widgets:
        w[NameObject("/AS")] = NameObject(target if on and _on_state(w) else "/Off")


def _set_radio(owner: Any, widgets: list[Any], choice: str, name: str) -> None:
    states = {(_on_state(w) or "").lstrip("/") for w in widgets}
    if choice not in states:
        raise ValueError(f"Invalid option {choice!r} for radio group {name!r}: {sorted(states)}")
    owner[NameObject("/V")] = NameObject("/" + choice)
    for w in widgets:
        w[NameObject("/AS")] = NameObject(
            "/" + choice if (_on_state(w) or "").lstrip("/") == choice else "/Off"
        )


def _set_choice(owner: Any, value: Any, name: str) -> None:
    opts = _options(owner, "choice", [])
    chosen = [str(v) for v in value] if isinstance(value, list | tuple) else [str(value)]
    ff = int(_inherited(owner, "/Ff") or 0)
    if opts and not (ff & _FF_EDIT):  # only editable combos accept free text
        bad = [c for c in chosen if c not in opts]
        if bad:
            raise ValueError(f"Invalid option {bad[0]!r} for field {name!r}: {opts}")
    if len(chosen) == 1:
        owner[NameObject("/V")] = TextStringObject(chosen[0])
    else:
        owner[NameObject("/V")] = ArrayObject(TextStringObject(c) for c in chosen)
    owner.pop("/I", None)
    for w in _kids(owner):
        w.pop("/AP", None)


def _kids(owner: Any) -> list[Any]:
    kids = owner.get("/Kids")
    return [k.get_object() for k in kids] if kids else [owner]


def flatten_form(data: bytes, password: str = "") -> bytes:
    """Burn field values into the page content and remove the form (widgets + AcroForm).

    Raises:
        OperationNotSupportedError: a page with form fields is rotated.
    """
    from anydoc.formats.pdf.stamp import _register_font, _stamp

    reader = _reader(data, password)
    fields = _collect(reader)
    if not fields:
        return data
    per_page: dict[int, list[tuple[str, Any, Any]]] = {}
    for owner, widgets in fields.values():
        kind = _kind(owner)
        if kind in ("button", "signature"):
            continue
        for page, w in widgets:
            if int(reader.pages[page].rotation) % 360:
                raise OperationNotSupportedError(
                    "Flattening forms on rotated pages is not supported"
                )
            per_page.setdefault(page, []).append((kind, owner, w))

    def draw(c: Any, width: float, height: float, index: int, count: int) -> None:
        for kind, owner, w in per_page.get(index, []):
            rect = _rect(w)
            if rect is None:
                continue
            x0, y0, x1, y1 = rect
            if kind in ("checkbox", "radio"):
                if str(w.get("/AS", "/Off")) == "/Off":
                    continue
                size = min(x1 - x0, y1 - y0)
                c.setFont("ZapfDingbats", size * 0.8)
                c.drawCentredString(
                    (x0 + x1) / 2, y0 + size * 0.18, "l" if kind == "radio" else "4"
                )
                continue
            v = _value(owner, kind)
            if not v:
                continue
            text = " / ".join(v) if isinstance(v, list) else str(v)
            size = max(6.0, min(12.0, (y1 - y0) - 4))
            c.setFont(_register_font(text, None), size)
            ff = int(_inherited(owner, "/Ff") or 0)
            if ff & _FF_MULTILINE:
                y = y1 - size - 2
                for line in text.splitlines():
                    c.drawString(x0 + 2, y, line)
                    y -= size * 1.2
            else:
                c.drawString(x0 + 2, y0 + ((y1 - y0) - size) / 2 + size * 0.2, text)

    stamped = _stamp(data, None, draw, password=password)
    writer = PdfWriter(clone_from=_reader(stamped))
    for wpage in writer.pages:
        annots = wpage.get("/Annots")
        if annots is None:
            continue
        keep = [a for a in annots if a.get_object().get("/Subtype") != "/Widget"]
        if keep:
            wpage[NameObject("/Annots")] = ArrayObject(keep)
        else:
            del wpage["/Annots"]
    root = writer._root_object
    if "/AcroForm" in root:
        del root["/AcroForm"]
    return _dump(writer)
