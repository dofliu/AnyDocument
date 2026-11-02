"""Tests for PDF watermarks, headers/footers and page numbers (T041)."""

from __future__ import annotations

import io
from pathlib import Path

import pdfplumber
import pypdfium2
import pytest
from PIL import Image, ImageChops
from pypdf import PdfReader
from typer.testing import CliRunner

from anydoc.cli import app
from anydoc.core.errors import OperationNotSupportedError
from anydoc.formats.pdf import PdfHandler
from anydoc.formats.pdf import stamp as st
from anydoc.formats.pdf.pages import rotate_pages

runner = CliRunner()


def _pdf(n: int = 3, size: tuple[float, float] = (300, 400)) -> bytes:
    from reportlab.pdfgen import canvas

    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=size)
    for i in range(1, n + 1):
        c.setFont("Helvetica", 14)
        c.drawString(100, 200, f"BODY{i}")
        c.showPage()
    c.save()
    return buf.getvalue()


def _words(data: bytes, page: int = 0) -> list[dict[str, float | str]]:
    with pdfplumber.open(io.BytesIO(data)) as pdf:
        return pdf.pages[page].extract_words()  # type: ignore[no-any-return]


def _text(data: bytes, page: int = 0) -> str:
    return PdfReader(io.BytesIO(data)).pages[page].extract_text()


def _render(data: bytes, page: int = 0) -> Image.Image:
    pdf = pypdfium2.PdfDocument(data)
    try:
        return pdf[page].render(scale=1).to_pil().convert("RGB")
    finally:
        pdf.close()


def _png(color: tuple[int, int, int, int] = (255, 0, 0, 255)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGBA", (40, 20), color).save(buf, format="PNG")
    return buf.getvalue()


def test_text_watermark_chinese_all_pages() -> None:
    out = st.add_text_watermark(_pdf(), "機密文件")
    for i in range(3):
        assert "機密文件" in _text(out, i)
        assert f"BODY{i + 1}" in _text(out, i)
    assert len(PdfReader(io.BytesIO(out)).pages) == 3


def test_text_watermark_selected_pages_only() -> None:
    out = st.add_text_watermark(_pdf(), "SECRET", pages=[1])
    assert "SECRET" not in _text(out, 0)
    assert "SECRET" in _text(out, 1)
    assert "SECRET" not in _text(out, 2)


def test_text_watermark_opacity_color_and_tile() -> None:
    out = st.add_text_watermark(_pdf(1), "DRAFT", opacity=0.25, color="#FF0000", tile=True)
    page = PdfReader(io.BytesIO(out)).pages[0]
    gs = page["/Resources"]["/ExtGState"]
    assert any(abs(float(v.get_object().get("/ca", 1)) - 0.25) < 1e-6 for v in gs.values())
    assert _text(out).count("DRAFT") > 1
    r, g, b = _render(out).getpixel((150, 100))
    assert r >= g and r >= b


def test_text_watermark_under_keeps_body_visible() -> None:
    out = st.add_text_watermark(_pdf(1), "UNDER", under=True)
    assert "UNDER" in _text(out) and "BODY1" in _text(out)


def test_text_watermark_validation() -> None:
    with pytest.raises(ValueError):
        st.add_text_watermark(_pdf(1), "")
    with pytest.raises(ValueError):
        st.add_text_watermark(_pdf(1), "x", opacity=2)
    with pytest.raises(ValueError):
        st.add_text_watermark(_pdf(1), "x", font_size=0)
    with pytest.raises(ValueError):
        st.add_text_watermark(_pdf(1), "x", color="red")
    with pytest.raises(ValueError):
        st.add_text_watermark(_pdf(1), "x", color="#GGGGGG")
    with pytest.raises(ValueError):
        st.add_text_watermark(_pdf(1), "x", color=(2, 0, 0))
    with pytest.raises(IndexError):
        st.add_text_watermark(_pdf(1), "x", pages=[5])
    with pytest.raises(ValueError):
        st.add_text_watermark(_pdf(1), "x", font_path=Path("/nonexistent.ttf"))


def test_explicit_font_path_and_cid_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    font = st.find_cjk_font()
    if font is not None:
        out = st.add_text_watermark(_pdf(1), "測試", font_path=font)
        assert "測試" in _text(out)
    monkeypatch.setattr(st, "find_cjk_font", lambda: None)
    out = st.add_text_watermark(_pdf(1), "測試")
    assert len(PdfReader(io.BytesIO(out)).pages) == 1


def test_find_cjk_font_scans_candidates(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    font = tmp_path / "f.ttc"
    font.write_bytes(b"x")
    monkeypatch.setattr(st, "_FONT_CANDIDATES", ("/nope", str(font)))
    assert st.find_cjk_font() == font
    monkeypatch.setattr(st, "_FONT_CANDIDATES", ("/nope",))
    assert st.find_cjk_font() is None


def test_image_watermark_positions_and_pixels() -> None:
    out = st.add_image_watermark(_pdf(1), _png(), scale=0.5, opacity=1.0)
    img = _render(out)
    assert img.getpixel((150, 200)) == (255, 0, 0)  # centre
    out = st.add_image_watermark(_pdf(1), _png(), scale=0.2, opacity=1.0, position="top-left")
    img = _render(out)
    assert img.getpixel((55, 50)) == (255, 0, 0)
    assert img.getpixel((250, 350)) == (255, 255, 255)


def test_image_watermark_translucent_and_path(tmp_path: Path) -> None:
    p = tmp_path / "w.png"
    p.write_bytes(_png())
    out = st.add_image_watermark(_pdf(1), p, opacity=0.5, angle=30, pages=[0])
    r, g, b = _render(out).getpixel((150, 200))
    assert r >= 250 and 100 < g < 200


def test_image_watermark_validation() -> None:
    with pytest.raises(ValueError):
        st.add_image_watermark(_pdf(1), b"not an image")
    with pytest.raises(ValueError):
        st.add_image_watermark(_pdf(1), _png(), scale=0)
    with pytest.raises(ValueError):
        st.add_image_watermark(_pdf(1), _png(), opacity=-1)
    with pytest.raises(ValueError):
        st.add_image_watermark(_pdf(1), _png(), position="middle")


def test_header_footer_placeholders_and_chinese() -> None:
    out = st.add_header_footer(_pdf(), header="報告標題", footer="第 {page} 頁 / 共 {total} 頁")
    assert "報告標題" in _text(out, 0)
    assert "第 1 頁 / 共 3 頁" in _text(out, 0)
    assert "第 3 頁 / 共 3 頁" in _text(out, 2)
    assert "BODY2" in _text(out, 1)


def test_header_footer_three_slots_positions() -> None:
    out = st.add_header_footer(_pdf(1), footer=("LEFT", "MID", "RIGHT"), header=("a", "", ""))
    words = {w["text"]: w for w in _words(out)}
    assert words["LEFT"]["x0"] < words["MID"]["x0"] < words["RIGHT"]["x0"]
    assert words["LEFT"]["x0"] == pytest.approx(36, abs=1)
    assert words["RIGHT"]["x1"] == pytest.approx(264, abs=1)
    assert words["a"]["top"] < words["LEFT"]["top"]  # header above footer
    assert words["LEFT"]["bottom"] == pytest.approx(400 - 36, abs=4)


def test_header_footer_align_start_pages() -> None:
    out = st.add_header_footer(_pdf(), footer="N{page}", align="right", start=0, pages=[1, 2])
    assert "N0" not in _text(out, 0) and "N" not in _text(out, 0).replace("BODY", "")
    assert "N1" in _text(out, 1) and "N2" in _text(out, 2)
    w = [w for w in _words(out, 1) if str(w["text"]).startswith("N")][0]
    assert float(w["x1"]) == pytest.approx(264, abs=1)


def test_header_footer_validation() -> None:
    with pytest.raises(ValueError):
        st.add_header_footer(_pdf(1))
    with pytest.raises(ValueError):
        st.add_header_footer(_pdf(1), footer="x", align="middle")
    with pytest.raises(ValueError):
        st.add_header_footer(_pdf(1), footer=("a", "b"))  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        st.add_header_footer(_pdf(1), footer="x", font_size=-1)


@pytest.mark.parametrize(
    ("position", "vertical", "horizontal"),
    [
        ("bottom-center", "bottom", "center"),
        ("bottom-right", "bottom", "right"),
        ("top-left", "top", "left"),
    ],
)
def test_page_numbers_positions(position: str, vertical: str, horizontal: str) -> None:
    out = st.add_page_numbers(_pdf(2), position=position, fmt="P{page}/{total}")
    w = [w for w in _words(out, 1) if str(w["text"]).startswith("P")][0]
    assert w["text"] == "P2/2"
    assert (float(w["top"]) < 100) == (vertical == "top")
    mid = (float(w["x0"]) + float(w["x1"])) / 2
    expect = {"left": 36 + (float(w["x1"]) - float(w["x0"])) / 2, "center": 150, "right": 264}
    assert mid == pytest.approx(
        expect[horizontal]
        if horizontal != "right"
        else 264 - (float(w["x1"]) - float(w["x0"])) / 2,
        abs=2,
    )


def test_page_numbers_default_and_invalid() -> None:
    out = st.add_page_numbers(_pdf(3))
    assert "2 / 3" in _text(out, 1)
    with pytest.raises(ValueError):
        st.add_page_numbers(_pdf(1), position="nowhere")


@pytest.mark.parametrize("angle", [90, 180, 270])
def test_stamp_on_rotated_pages_lands_at_visible_bottom(angle: int) -> None:
    rotated = rotate_pages(_pdf(1), angle)
    out = st.add_page_numbers(rotated, fmt="XXXXXXXX", position="bottom-center", margin=20)
    img = _render(out)
    base = _render(rotated)
    box = (
        ImageChops.difference(img.convert("L"), base.convert("L"))
        .point(lambda v: 255 if v > 60 else 0)
        .getbbox()
    )
    assert box is not None
    width, height = img.size
    assert box[3] > height - 40  # near the visible bottom edge
    assert abs((box[0] + box[2]) / 2 - width / 2) < 20  # horizontally centred


def test_stamp_preserves_other_content_and_page_sizes() -> None:
    src = _pdf(2, (250, 350))
    out = st.add_text_watermark(src, "W")
    r = PdfReader(io.BytesIO(out))
    assert [(float(p.mediabox.width), float(p.mediabox.height)) for p in r.pages] == [
        (250, 350)
    ] * 2
    assert "BODY2" in _text(out, 1)


def test_encrypted_requires_password() -> None:
    from pypdf import PdfWriter

    w = PdfWriter()
    w.add_blank_page(100, 100)
    w.encrypt("secret")
    buf = io.BytesIO()
    w.write(buf)
    with pytest.raises(OperationNotSupportedError):
        st.add_text_watermark(buf.getvalue(), "x")
    out = st.add_text_watermark(buf.getvalue(), "x", password="secret")
    assert len(PdfReader(io.BytesIO(out)).pages) == 1


def test_document_roundtrip(tmp_path: Path) -> None:
    src = tmp_path / "a.pdf"
    src.write_bytes(_pdf(2))
    doc = PdfHandler().open(src)
    doc.add_text_watermark("機密", angle=0)
    doc.add_image_watermark(_png(), opacity=0.5)
    doc.add_header_footer(header="H")
    doc.add_page_numbers()
    out = tmp_path / "b.pdf"
    doc.save(out)
    doc.close()
    again = PdfHandler().open(out)
    try:
        assert again.page_count == 2
        text = again.page_text(1)
        assert "機密" in text and "2 / 2" in text and "BODY2" in text
    finally:
        again.close()


def test_document_refuses_when_encrypted(tmp_path: Path) -> None:
    from pypdf import PdfWriter

    w = PdfWriter()
    w.add_blank_page(100, 100)
    w.encrypt("pw")
    buf = io.BytesIO()
    w.write(buf)
    doc = PdfHandler().open(buf.getvalue(), password="pw")
    with pytest.raises(OperationNotSupportedError):
        doc.add_page_numbers()
    doc.close()


def test_cli_watermark_text_image_and_errors(tmp_path: Path) -> None:
    src = tmp_path / "in.pdf"
    src.write_bytes(_pdf(3))
    out = tmp_path / "out.pdf"
    res = runner.invoke(
        app, ["pdf", "watermark", str(src), "-o", str(out), "--text", "內部使用", "-p", "2-3"]
    )
    assert res.exit_code == 0, res.output
    assert "內部使用" not in _text(out.read_bytes(), 0)
    assert "內部使用" in _text(out.read_bytes(), 2)

    img = tmp_path / "w.png"
    img.write_bytes(_png())
    res = runner.invoke(
        app, ["pdf", "watermark", str(src), "-o", str(out), "--image", str(img), "--opacity", "1"]
    )
    assert res.exit_code == 0, res.output
    assert _render(out.read_bytes()).getpixel((150, 200)) == (255, 0, 0)

    assert runner.invoke(app, ["pdf", "watermark", str(src), "-o", str(out)]).exit_code == 2
    bad = runner.invoke(app, ["pdf", "watermark", str(src), "-o", str(out), "-t", "x", "-p", "9"])
    assert bad.exit_code != 0
    bad = runner.invoke(
        app, ["pdf", "watermark", str(src), "-o", str(out), "-t", "x", "--color", "nope"]
    )
    assert bad.exit_code != 0
    missing = runner.invoke(app, ["pdf", "watermark", str(src), "-o", str(out), "-i", "/no.png"])
    assert missing.exit_code == 2


def test_cli_header_footer_and_page_numbers(tmp_path: Path) -> None:
    src = tmp_path / "in.pdf"
    src.write_bytes(_pdf(2))
    out = tmp_path / "out.pdf"
    res = runner.invoke(
        app,
        [
            "pdf",
            "header-footer",
            str(src),
            "-o",
            str(out),
            "--header",
            "頁首",
            "--footer",
            "{page}",
        ],
    )
    assert res.exit_code == 0, res.output
    assert "頁首" in _text(out.read_bytes(), 0)

    res = runner.invoke(
        app,
        ["pdf", "page-numbers", str(src), "-o", str(out), "--format", "第{page}頁", "--start", "5"],
    )
    assert res.exit_code == 0, res.output
    assert "第6頁" in _text(out.read_bytes(), 1)

    bad = runner.invoke(app, ["pdf", "header-footer", str(src), "-o", str(out)])
    assert bad.exit_code != 0
    bad = runner.invoke(app, ["pdf", "page-numbers", str(src), "-o", str(out), "--position", "x"])
    assert bad.exit_code != 0
