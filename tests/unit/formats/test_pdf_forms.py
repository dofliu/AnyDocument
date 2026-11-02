"""Tests for PDF form fields: list, fill, flatten (T043)."""

from __future__ import annotations

import io
import json
from pathlib import Path

import pytest
from pypdf import PdfReader
from typer.testing import CliRunner

from anydoc.cli import app
from anydoc.core.errors import OperationNotSupportedError
from anydoc.formats.pdf import PdfHandler
from anydoc.formats.pdf import forms as fm

runner = CliRunner()


def _form(readonly: bool = False, rotate: int = 0) -> bytes:
    from reportlab.pdfgen import canvas

    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=(300, 400))
    c.drawString(20, 380, "FormTitle")
    f = c.acroForm
    f.textfield(name="name", x=20, y=330, width=150, height=20, value="")
    f.textfield(name="memo", x=20, y=300, width=150, height=40, fieldFlags="multiline")
    f.checkbox(name="agree", x=20, y=270, size=15, checked=False)
    f.radio(name="sex", value="M", x=20, y=240, size=12, selected=False)
    f.radio(name="sex", value="F", x=60, y=240, size=12, selected=True)
    f.choice(name="city", value="A", options=["A", "B"], x=20, y=200, width=100, height=20)
    if readonly:
        f.textfield(name="ro", x=20, y=150, width=100, height=20, value="x", fieldFlags="readOnly")
    c.showPage()
    c.drawString(20, 380, "Page2")
    c.save()
    data = buf.getvalue()
    if rotate:
        from pypdf import PdfWriter

        w = PdfWriter(clone_from=PdfReader(io.BytesIO(data)))
        w.pages[0].rotate(rotate)
        out = io.BytesIO()
        w.write(out)
        data = out.getvalue()
    return data


def _plain() -> bytes:
    from reportlab.pdfgen import canvas

    buf = io.BytesIO()
    c = canvas.Canvas(buf)
    c.drawString(10, 10, "x")
    c.save()
    return buf.getvalue()


def _by_name(data: bytes) -> dict[str, fm.FormField]:
    return {f.name: f for f in fm.list_form_fields(data)}


def test_list_fields() -> None:
    fields = _by_name(_form(readonly=True))
    assert set(fields) == {"name", "memo", "agree", "sex", "city", "ro"}
    assert fields["name"].type == "text" and fields["name"].page == 0
    assert fields["agree"].type == "checkbox" and fields["agree"].value is False
    assert fields["sex"].type == "radio" and fields["sex"].value == "F"
    assert sorted(fields["sex"].options) == ["F", "M"]
    assert fields["city"].type == "choice" and fields["city"].options == ["A", "B"]
    assert fields["ro"].read_only
    assert fields["name"].rect is not None
    assert json.dumps(fields["city"].to_dict())


def test_no_form() -> None:
    assert fm.list_form_fields(_plain()) == []
    assert not fm.has_form(_plain())
    assert fm.has_form(_form())
    plain = _plain()
    assert fm.flatten_form(plain) == plain


def test_fill_roundtrip_with_chinese() -> None:
    out = fm.fill_form(
        _form(),
        {"name": "王小明", "memo": "第一行\n第二行", "agree": True, "sex": "M", "city": "B"},
    )
    f = _by_name(out)
    assert f["name"].value == "王小明"
    assert f["memo"].value == "第一行\n第二行"
    assert f["agree"].value is True
    assert f["sex"].value == "M"
    assert f["city"].value == "B"
    # uncheck again
    back = _by_name(fm.fill_form(out, {"agree": False}))
    assert back["agree"].value is False
    assert back["name"].value == "王小明"  # others untouched


def test_fill_validation() -> None:
    data = _form(readonly=True)
    with pytest.raises(KeyError):
        fm.fill_form(data, {"nope": "x"})
    with pytest.raises(ValueError, match="read-only"):
        fm.fill_form(data, {"ro": "y"})
    with pytest.raises(ValueError, match="Invalid option"):
        fm.fill_form(data, {"sex": "X"})
    with pytest.raises(ValueError, match="Invalid option"):
        fm.fill_form(data, {"city": "Z"})


def test_fill_flatten_removes_form_keeps_values_visible() -> None:
    out = fm.fill_form(
        _form(), {"name": "王小明", "agree": True, "sex": "M", "city": "B"}, flatten=True
    )
    assert fm.list_form_fields(out) == []
    reader = PdfReader(io.BytesIO(out))
    assert "/AcroForm" not in reader.trailer["/Root"]
    assert not any(
        a.get_object().get("/Subtype") == "/Widget" for a in (reader.pages[0].get("/Annots") or [])
    )
    text = reader.pages[0].extract_text()
    assert "FormTitle" in text and "王小明" in text and "B" in text
    assert "Page2" in reader.pages[1].extract_text()


def test_flatten_empty_and_rotated() -> None:
    out = fm.flatten_form(_form())
    assert fm.list_form_fields(out) == []
    with pytest.raises(OperationNotSupportedError):
        fm.flatten_form(_form(rotate=90))


def test_checkbox_ink_only_when_checked() -> None:
    import pypdfium2

    def ink(data: bytes) -> int:
        page = pypdfium2.PdfDocument(data)[0]
        img = page.render(scale=2).to_pil().convert("L")
        # region around the checkbox at (20..35, 270..285) in a 300x400 page
        box = (40, 2 * (400 - 285), 70, 2 * (400 - 270))
        return sum(1 for p in img.crop(box).tobytes() if p < 128)

    off = fm.flatten_form(_form())
    on = fm.fill_form(_form(), {"agree": True}, flatten=True)
    assert ink(on) > ink(off)


def test_document_api() -> None:
    doc = PdfHandler().open(_form())
    assert {f.name for f in doc.form_fields()} >= {"name", "agree"}
    doc.fill_form({"name": "Bob"})
    assert _by_name(doc._data)["name"].value == "Bob"
    doc.flatten_form()
    assert doc.form_fields() == []


def test_cli_fields_fill_flatten(tmp_path: Path) -> None:
    src = tmp_path / "f.pdf"
    src.write_bytes(_form())
    r = runner.invoke(app, ["pdf", "fields", str(src)])
    assert r.exit_code == 0
    assert {f["name"] for f in json.loads(r.stdout)} >= {"name", "city"}

    vals = tmp_path / "v.json"
    vals.write_text(json.dumps({"name": "王"}), encoding="utf-8")
    out = tmp_path / "o.pdf"
    r = runner.invoke(
        app,
        ["pdf", "fill", str(src), "-o", str(out), "--values", str(vals),
         "--set", "agree=true", "--set", "city=B"],
    )  # fmt: skip
    assert r.exit_code == 0, r.output
    f = _by_name(out.read_bytes())
    assert f["name"].value == "王" and f["agree"].value is True and f["city"].value == "B"

    flat = tmp_path / "flat.pdf"
    r = runner.invoke(app, ["pdf", "flatten", str(out), "-o", str(flat)])
    assert r.exit_code == 0
    assert fm.list_form_fields(flat.read_bytes()) == []

    bad = runner.invoke(app, ["pdf", "fill", str(src), "-o", str(out), "--set", "zzz=1"])
    assert bad.exit_code != 0
    bad = runner.invoke(app, ["pdf", "fill", str(src), "-o", str(out), "--set", "novalue"])
    assert bad.exit_code != 0
