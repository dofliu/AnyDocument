"""Tests for PDF redaction (T049)."""

from __future__ import annotations

import io
import json
from pathlib import Path

import pytest
from pypdf import PdfReader
from typer.testing import CliRunner

from anydoc.cli import app
from anydoc.core.errors import CorruptFileError
from anydoc.formats.pdf import PdfHandler
from anydoc.formats.pdf import redact as rd
from anydoc.formats.pdf.pages import rotate_pages

runner = CliRunner()


def _pdf() -> bytes:
    from reportlab.pdfgen import canvas

    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=(400, 300))
    c.drawString(40, 250, "Public line one")
    c.drawString(40, 220, "SECRET-1234 is the code")
    c.showPage()
    c.drawString(40, 250, "Page two keeps secret-1234 too")
    c.showPage()
    c.drawString(40, 250, "Untouched page three")
    c.showPage()
    c.save()
    return buf.getvalue()


def _texts(data: bytes) -> list[str]:
    return [p.extract_text() or "" for p in PdfReader(io.BytesIO(data)).pages]


def test_text_redaction_removes_text_everywhere() -> None:
    res = rd.redact_pdf(_pdf(), texts=["secret-1234"])
    assert res.pages_redacted == [0, 1]
    assert res.matches == 2
    out = _texts(res.data)
    assert all("1234" not in t for t in out)
    assert "Untouched page three" in out[2]  # untouched pages keep their text
    assert rd.verify_redaction(res.data, ["secret-1234"]) == []


def test_verify_detects_leaks() -> None:
    assert rd.verify_redaction(_pdf(), ["SECRET-1234", "nothing-here"]) == ["SECRET-1234"]
    assert rd.verify_redaction(_pdf(), ["Secret-1234"], case_sensitive=True) == []


def test_region_redaction_burns_black_box() -> None:
    import pypdfium2

    res = rd.redact_pdf(_pdf(), regions=[(2, (0, 0, 400, 300))], dpi=72)
    assert res.pages_redacted == [2]
    assert res.regions == 1
    assert _texts(res.data)[2].strip() == ""
    page = pypdfium2.PdfDocument(res.data)[2]
    px = page.render(scale=1).to_pil().convert("L")
    assert px.getextrema() == (0, 0)  # fully black
    assert tuple(round(v) for v in page.get_size()) == (400, 300)


def test_regex_and_case_options() -> None:
    res = rd.redact_pdf(_pdf(), texts=[r"SECRET-\d+"], regex=True, case_sensitive=True)
    assert res.pages_redacted == [0]
    assert "1234" not in _texts(res.data)[0]
    assert "secret-1234" in _texts(res.data)[1]


def test_no_match_returns_original() -> None:
    src = _pdf()
    res = rd.redact_pdf(src, texts=["absent"])
    assert res.data == src
    assert res.pages_redacted == []


def test_find_text_boxes() -> None:
    found = rd.find_text(_pdf(), ["Public"])
    assert list(found) == [0]
    x0, top, x1, bottom = found[0][0]
    assert x0 < x1 and top < bottom and 40 <= x0 < 45


def test_errors() -> None:
    src = _pdf()
    with pytest.raises(ValueError):
        rd.redact_pdf(src, texts=[""])
    with pytest.raises(ValueError):
        rd.redact_pdf(src, texts=["("], regex=True)
    with pytest.raises(ValueError):
        rd.redact_pdf(src, dpi=5)
    with pytest.raises(IndexError):
        rd.redact_pdf(src, regions=[(9, (0, 0, 1, 1))])
    with pytest.raises(ValueError):
        rd.redact_pdf(src, regions=[(0, (0, 0, 1))])
    with pytest.raises(CorruptFileError):
        rd.redact_pdf(b"nope", texts=["x"])


def test_rotated_page_text_search_unsupported() -> None:
    from anydoc.core.errors import OperationNotSupportedError

    with pytest.raises(OperationNotSupportedError):
        rd.redact_pdf(rotate_pages(_pdf(), 90, [0]), texts=["Public"])
    # explicit regions still work on rotated pages
    res = rd.redact_pdf(rotate_pages(_pdf(), 90, [0]), regions=[(0, (0, 0, 50, 50))])
    assert res.pages_redacted == [0]


def test_metadata_preserved() -> None:
    from pypdf import PdfWriter

    w = PdfWriter(clone_from=io.BytesIO(_pdf()))
    w.add_metadata({"/Title": "Report"})
    buf = io.BytesIO()
    w.write(buf)
    res = rd.redact_pdf(buf.getvalue(), texts=["Public"])
    assert PdfReader(io.BytesIO(res.data)).metadata.title == "Report"  # type: ignore[union-attr]


def test_result_to_dict_and_document_method(tmp_path: Path) -> None:
    f = tmp_path / "a.pdf"
    f.write_bytes(_pdf())
    doc = PdfHandler().open(f)
    res = doc.redact(texts=["SECRET-1234"])  # type: ignore[attr-defined]
    assert res.to_dict() == {"pages_redacted": [0, 1], "regions": 0, "matches": 2}
    assert "1234" not in doc.text()


def test_cli_redact(tmp_path: Path) -> None:
    src = tmp_path / "a.pdf"
    src.write_bytes(_pdf())
    out = tmp_path / "o.pdf"
    r = runner.invoke(
        app, ["pdf", "redact", str(src), "-o", str(out), "-t", "secret-1234", "-r", "3:0,0,400,300"]
    )
    assert r.exit_code == 0, r.output
    assert json.loads(r.output[r.output.index("{") :])["pages_redacted"] == [0, 1, 2]
    assert rd.verify_redaction(out.read_bytes(), ["secret-1234", "Untouched"]) == []


def test_cli_redact_errors(tmp_path: Path) -> None:
    src = tmp_path / "a.pdf"
    src.write_bytes(_pdf())
    out = tmp_path / "o.pdf"
    assert runner.invoke(app, ["pdf", "redact", str(src), "-o", str(out)]).exit_code != 0
    bad = runner.invoke(app, ["pdf", "redact", str(src), "-o", str(out), "-r", "1:1,2"])
    assert bad.exit_code != 0
