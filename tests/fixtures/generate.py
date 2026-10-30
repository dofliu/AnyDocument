"""Programmatic sample-file generators used by the test-suite.

Every sample contains Traditional Chinese text so that encoding and font handling
is exercised.  Nothing here touches the network and nothing binary is committed:
files are written to ``tests/fixtures/generated/`` (git-ignored).
"""

from __future__ import annotations

import io
import json
from collections.abc import Callable
from pathlib import Path

GENERATED_DIR = Path(__file__).parent / "generated"

#: Chinese sample strings shared by all generators.
TITLE = "測試文件"
BODY = "這是一份用於自動化測試的繁體中文樣本。"
TABLE_HEADER = ("姓名", "部門", "分數")
TABLE_ROWS = (("王小明", "研發部", 90), ("陳美玲", "行銷部", 85), ("林志豪", "財務部", 78))


def _csv_text(delimiter: str = ",") -> str:
    lines = [delimiter.join(TABLE_HEADER)]
    lines += [delimiter.join(str(c) for c in row) for row in TABLE_ROWS]
    return "\n".join(lines) + "\n"


def make_txt() -> bytes:
    """Plain UTF-8 text."""
    return f"{TITLE}\n{BODY}\nSecond line: ASCII mixed 中英文。\n".encode()


def make_json() -> bytes:
    """UTF-8 JSON document with Chinese values."""
    data = {"title": TITLE, "body": BODY, "items": [list(r) for r in TABLE_ROWS]}
    return json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8")


def make_csv() -> bytes:
    """UTF-8 CSV without BOM."""
    return _csv_text().encode("utf-8")


def make_csv_bom() -> bytes:
    """UTF-8 CSV with BOM (Excel style)."""
    return _csv_text().encode("utf-8-sig")


def make_csv_big5() -> bytes:
    """Big5-encoded CSV."""
    return _csv_text().encode("big5")


def make_png() -> bytes:
    """Small RGB PNG image."""
    from PIL import Image, ImageDraw

    img = Image.new("RGB", (64, 48), "white")
    ImageDraw.Draw(img).rectangle((8, 8, 55, 39), outline="red", fill="blue")
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


def make_jpg() -> bytes:
    """Small JPEG image."""
    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (64, 48), (200, 30, 30)).save(buf, "JPEG")
    return buf.getvalue()


def make_docx() -> bytes:
    """DOCX with a heading, a paragraph and a table."""
    from docx import Document

    doc = Document()
    doc.core_properties.title = TITLE
    doc.core_properties.author = "AnyDocument"
    doc.add_heading(TITLE, level=1)
    doc.add_paragraph(BODY)
    table = doc.add_table(rows=1, cols=len(TABLE_HEADER))
    for cell, text in zip(table.rows[0].cells, TABLE_HEADER, strict=True):
        cell.text = text
    for row in TABLE_ROWS:
        for cell, value in zip(table.add_row().cells, row, strict=True):
            cell.text = str(value)
    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


def make_xlsx() -> bytes:
    """XLSX with two sheets, a formula and Chinese sheet names."""
    from openpyxl import Workbook

    wb = Workbook()
    ws = wb.active
    assert ws is not None
    ws.title = "成績"
    ws.append(list(TABLE_HEADER))
    for row in TABLE_ROWS:
        ws.append(list(row))
    ws["E1"] = "平均"
    ws["E2"] = "=AVERAGE(C2:C4)"
    wb.create_sheet("備註")["A1"] = BODY
    wb.properties.title = TITLE
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def make_pptx() -> bytes:
    """PPTX with a title slide and a bullet slide."""
    from pptx import Presentation

    prs = Presentation()
    first = prs.slides.add_slide(prs.slide_layouts[0])
    first.shapes.title.text = TITLE  # type: ignore[union-attr]
    first.placeholders[1].text = BODY
    second = prs.slides.add_slide(prs.slide_layouts[1])
    second.shapes.title.text = "第二頁"  # type: ignore[union-attr]
    second.placeholders[1].text = "重點一\n重點二"
    second.notes_slide.notes_text_frame.text = "講者備忘稿"  # type: ignore[union-attr]
    buf = io.BytesIO()
    prs.save(buf)
    return buf.getvalue()


def make_pdf() -> bytes:
    """Two-page PDF with extractable Chinese (CID font, UniGB encoding) and English text."""
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.cidfonts import UnicodeCIDFont
    from reportlab.pdfgen import canvas

    pdfmetrics.registerFont(UnicodeCIDFont("STSong-Light"))
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    c.setTitle(TITLE)
    c.setFont("STSong-Light", 18)
    c.drawString(72, 760, TITLE)
    c.setFont("STSong-Light", 12)
    c.drawString(72, 730, BODY)
    c.showPage()
    c.setFont("Helvetica", 12)
    c.drawString(72, 760, "Page two: hello world")
    c.showPage()
    c.save()
    return buf.getvalue()


def make_dxf() -> bytes:
    """DXF (R2010) with layers, entities and Chinese text."""
    import ezdxf

    doc = ezdxf.new("R2010")
    doc.layers.add("牆", color=1)
    doc.layers.add("標註", color=3)
    msp = doc.modelspace()
    msp.add_line((0, 0), (100, 0), dxfattribs={"layer": "牆"})
    msp.add_circle((50, 50), 25, dxfattribs={"layer": "牆"})
    msp.add_text(TITLE, height=5, dxfattribs={"layer": "標註", "insert": (0, -10)})
    stream = io.StringIO()
    doc.write(stream)
    return stream.getvalue().encode("utf-8")


def make_rtf() -> bytes:
    """Return an RTF sample (CJK text via ``\\uN`` escapes) with an info block."""

    def esc(text: str) -> str:
        return "".join(f"\\u{ord(c)}?" if ord(c) > 127 else c for c in text)

    return (
        "{\\rtf1\\ansi\\ansicpg950\\deff0{\\fonttbl{\\f0 Arial;}}"
        f"{{\\info{{\\title {esc(TITLE)}}}{{\\author Doflab}}}}"
        f"\\pard {esc(TITLE)}\\par {esc(BODY)}\\par}}"
    ).encode("ascii")


def make_html() -> bytes:
    """Return an HTML sample with headings, a link, an image and a table."""
    return (
        '<!DOCTYPE html><html lang="zh-Hant"><head><meta charset="utf-8">'
        f'<title>{TITLE}</title><meta name="author" content="Doflab">'
        "<style>p{color:red}</style></head><body>"
        f'<h1>{TITLE}</h1><p>{BODY}</p><a href="https://example.com">連結</a>'
        '<img src="a.png" alt="圖"><table><tr><th>名稱</th><th>數量</th></tr>'
        "<tr><td>蘋果</td><td>3</td></tr></table>"
        "<script>var x = 1;</script></body></html>"
    ).encode()


def make_epub() -> bytes:
    """Return a minimal two-chapter EPUB 3 package."""
    import zipfile

    def chapter(title: str, body: str) -> str:
        return (
            '<?xml version="1.0" encoding="utf-8"?><html xmlns="http://www.w3.org/1999/xhtml">'
            f"<head><title>{title}</title></head><body><h1>{title}</h1><p>{body}</p></body></html>"
        )

    opf = (
        '<?xml version="1.0" encoding="utf-8"?>'
        '<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="id">'
        '<metadata xmlns:dc="http://purl.org/dc/elements/1.1/">'
        f"<dc:title>{TITLE}</dc:title><dc:creator>Doflab</dc:creator>"
        '<dc:language>zh-TW</dc:language><dc:identifier id="id">urn:test:1</dc:identifier>'
        "</metadata><manifest>"
        '<item id="c1" href="ch1.xhtml" media-type="application/xhtml+xml"/>'
        '<item id="c2" href="ch2.xhtml" media-type="application/xhtml+xml"/>'
        '</manifest><spine><itemref idref="c1"/><itemref idref="c2"/></spine></package>'
    )
    container = (
        '<?xml version="1.0"?><container version="1.0" '
        'xmlns="urn:oasis:names:tc:opendocument:xmlns:container"><rootfiles>'
        '<rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/>'
        "</rootfiles></container>"
    )
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("mimetype", "application/epub+zip", compress_type=zipfile.ZIP_STORED)
        zf.writestr("META-INF/container.xml", container)
        zf.writestr("OEBPS/content.opf", opf)
        zf.writestr("OEBPS/ch1.xhtml", chapter("第一章", BODY))
        zf.writestr("OEBPS/ch2.xhtml", chapter("第二章", "結尾"))
    return buf.getvalue()


#: Sample id -> (file name, generator).  ``sample(fmt)`` accepts these ids.
SAMPLES: dict[str, tuple[str, Callable[[], bytes]]] = {
    "txt": ("sample.txt", make_txt),
    "json": ("sample.json", make_json),
    "csv": ("sample.csv", make_csv),
    "csv-bom": ("sample_bom.csv", make_csv_bom),
    "csv-big5": ("sample_big5.csv", make_csv_big5),
    "png": ("sample.png", make_png),
    "jpg": ("sample.jpg", make_jpg),
    "docx": ("sample.docx", make_docx),
    "xlsx": ("sample.xlsx", make_xlsx),
    "pptx": ("sample.pptx", make_pptx),
    "pdf": ("sample.pdf", make_pdf),
    "dxf": ("sample.dxf", make_dxf),
    "rtf": ("sample.rtf", make_rtf),
    "html": ("sample.html", make_html),
    "epub": ("sample.epub", make_epub),
}


def generate_sample(fmt: str, directory: Path = GENERATED_DIR) -> Path:
    """Generate one sample into *directory* and return its path."""
    try:
        name, factory = SAMPLES[fmt]
    except KeyError:
        raise KeyError(f"unknown sample format {fmt!r}; known: {sorted(SAMPLES)}") from None
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / name
    path.write_bytes(factory())
    return path


def generate_all(directory: Path = GENERATED_DIR) -> dict[str, Path]:
    """Generate every sample and return ``{sample_id: path}``."""
    return {fmt: generate_sample(fmt, directory) for fmt in SAMPLES}
