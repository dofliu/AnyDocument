"""Tests for the ODF (ODT/ODS/ODP) reader (T018)."""

# ruff: noqa: E501  (inline ODF XML fixtures)

from __future__ import annotations

import io
import zipfile
from collections.abc import Callable
from datetime import date, datetime
from pathlib import Path

import pytest

import anydoc
from anydoc.core.errors import CorruptFileError, SecurityLimitError
from anydoc.core.model import Capability, Document
from anydoc.core.registry import registry
from anydoc.formats.odf import (
    OdpDocument,
    OdpHandler,
    OdsDocument,
    OdsHandler,
    OdtDocument,
    OdtHandler,
)
from tests.fixtures.generate import BODY, TITLE

HANDLERS = (OdtHandler, OdsHandler, OdpHandler)
MIME = {
    "odt": "application/vnd.oasis.opendocument.text",
    "ods": "application/vnd.oasis.opendocument.spreadsheet",
    "odp": "application/vnd.oasis.opendocument.presentation",
}

NS = (
    'xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0" '
    'xmlns:text="urn:oasis:names:tc:opendocument:xmlns:text:1.0" '
    'xmlns:table="urn:oasis:names:tc:opendocument:xmlns:table:1.0" '
    'xmlns:draw="urn:oasis:names:tc:opendocument:xmlns:drawing:1.0" '
    'xmlns:presentation="urn:oasis:names:tc:opendocument:xmlns:presentation:1.0" '
    'xmlns:meta="urn:oasis:names:tc:opendocument:xmlns:meta:1.0" '
    'xmlns:dc="http://purl.org/dc/elements/1.1/" '
    'xmlns:xlink="http://www.w3.org/1999/xlink"'
)

META = f"""<?xml version="1.0" encoding="UTF-8"?>
<office:document-meta {NS}><office:meta>
<dc:title>{TITLE}</dc:title><meta:initial-creator>王小明</meta:initial-creator>
<dc:subject>主旨</dc:subject><meta:keyword>甲</meta:keyword><meta:keyword>乙</meta:keyword>
<meta:creation-date>2026-01-02T03:04:05</meta:creation-date>
<dc:date>2026-02-03T04:05:06</dc:date><meta:generator>unit-test</meta:generator>
</office:meta></office:document-meta>"""

ODT_BODY = f"""<office:text>
<text:h text:outline-level="1" text:style-name="Heading_20_1">{TITLE}</text:h>
<text:p text:style-name="Standard">{BODY}<text:s text:c="2"/>甲<text:tab/>乙<text:line-break/>丙
<text:span>粗體</text:span><text:note><text:note-body><text:p>註腳不計</text:p></text:note-body></text:note></text:p>
<text:list><text:list-item><text:p>項目一</text:p>
<text:list><text:list-item><text:p>子項目</text:p></text:list-item></text:list>
</text:list-item></text:list>
<table:table table:name="T1"><table:table-row>
<table:table-cell><text:p>姓名</text:p></table:table-cell>
<table:table-cell><text:p>分數</text:p></table:table-cell></table:table-row>
<table:table-row><table:table-cell><text:p>小明</text:p></table:table-cell>
<table:table-cell><text:p>95</text:p></table:table-cell></table:table-row></table:table>
<text:h text:outline-level="2">第二節</text:h>
<text:p><draw:frame><draw:image xlink:href="Pictures/a.png"/></draw:frame></text:p>
</office:text>"""

ODS_BODY = """<office:spreadsheet>
<table:table table:name="資料">
<table:table-row>
<table:table-cell office:value-type="string"><text:p>姓名</text:p></table:table-cell>
<table:table-cell office:value-type="string"><text:p>分數</text:p></table:table-cell>
<table:table-cell office:value-type="string"><text:p>日期</text:p></table:table-cell>
</table:table-row>
<table:table-row>
<table:table-cell office:value-type="string"><text:p>小明</text:p></table:table-cell>
<table:table-cell office:value-type="float" office:value="95"><text:p>95</text:p></table:table-cell>
<table:table-cell office:value-type="date" office:date-value="2026-03-04"><text:p>x</text:p></table:table-cell>
</table:table-row>
<table:table-row table:number-rows-repeated="3"><table:table-cell table:number-columns-repeated="3"/></table:table-row>
<table:table-row>
<table:table-cell table:number-columns-repeated="1"/>
<table:table-cell office:value-type="float" office:value="2.5" table:formula="of:=SUM([.B2:.B2])"><text:p>2.5</text:p></table:table-cell>
<table:table-cell office:value-type="boolean" office:boolean-value="true"><text:p>TRUE</text:p></table:table-cell>
</table:table-row>
<table:table-row>
<table:table-cell office:value-type="percentage" office:value="0.5" table:number-columns-repeated="2"><text:p>50%</text:p></table:table-cell>
<table:table-cell office:value-type="date" office:date-value="2026-03-04T10:20:30"/>
<table:table-cell table:formula="of:=NOW()"/>
</table:table-row>
</table:table>
<table:table table:name="空白"><table:table-row table:number-rows-repeated="1048576">
<table:table-cell table:number-columns-repeated="1024"/></table:table-row></table:table>
</office:spreadsheet>"""

ODP_BODY = f"""<office:presentation>
<draw:page draw:name="page1" draw:master-page-name="Default">
<draw:frame presentation:class="title" draw:name="t"><draw:text-box><text:p>{TITLE}</text:p></draw:text-box></draw:frame>
<draw:frame presentation:class="outline"><draw:text-box><text:list><text:list-item>
<text:p>{BODY}</text:p></text:list-item></text:list></draw:text-box></draw:frame>
<draw:frame><draw:image xlink:href="Pictures/a.png"/></draw:frame>
<draw:frame><table:table><table:table-row><table:table-cell><text:p>甲</text:p></table:table-cell>
<table:table-cell><text:p>乙</text:p></table:table-cell></table:table-row></table:table></draw:frame>
<presentation:notes><draw:frame presentation:class="page"/>
<draw:frame presentation:class="notes"><draw:text-box><text:p>備忘稿內容</text:p></draw:text-box></draw:frame>
</presentation:notes></draw:page>
<draw:page draw:name="page2" draw:master-page-name="Other">
<draw:frame><draw:text-box><text:p>第二頁</text:p></draw:text-box></draw:frame></draw:page>
</office:presentation>"""


def make_odf(
    kind: str, body: str, *, meta: str | None = META, mimetype: str | None = None
) -> bytes:
    """Build a minimal ODF package of ``kind`` around an ``office:body`` payload."""
    content = (
        f'<?xml version="1.0" encoding="UTF-8"?>'
        f"<office:document-content {NS}><office:body>{body}</office:body></office:document-content>"
    )
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("mimetype", mimetype or MIME[kind], compress_type=zipfile.ZIP_STORED)
        zf.writestr("content.xml", content)
        if meta is not None:
            zf.writestr("meta.xml", meta)
    return buf.getvalue()


BODIES = {"odt": ODT_BODY, "ods": ODS_BODY, "odp": ODP_BODY}


@pytest.fixture(autouse=True)
def _handlers() -> None:
    for h in HANDLERS:
        registry.register(h, replace=True)


@pytest.fixture
def odt() -> OdtDocument:
    doc = OdtHandler().open(make_odf("odt", ODT_BODY))
    assert isinstance(doc, OdtDocument)
    return doc


@pytest.fixture
def ods() -> OdsDocument:
    doc = OdsHandler().open(make_odf("ods", ODS_BODY))
    assert isinstance(doc, OdsDocument)
    return doc


@pytest.fixture
def odp() -> OdpDocument:
    doc = OdpHandler().open(make_odf("odp", ODP_BODY))
    assert isinstance(doc, OdpDocument)
    return doc


# -- handlers ----------------------------------------------------------------


def test_handler_metadata() -> None:
    for h, fmt in zip(HANDLERS, ("odt", "ods", "odp"), strict=True):
        assert h.format_id == fmt
        assert h.extensions == (fmt,)
        assert h.mime_types == (MIME[fmt],)
        assert Capability.READ in h.capabilities
        assert Capability.EXTRACT_TEXT in h.capabilities


def test_sniff() -> None:
    assert OdtHandler.sniff(b"PK", Path("a.odt")) == 0.9
    assert OdtHandler.sniff(b"PK", Path("a.ods")) == 0.0
    assert OdtHandler.sniff(b"PK", None) == 0.0


def test_entry_points_registered() -> None:
    from anydoc.core.registry import HandlerRegistry

    reg = HandlerRegistry()
    assert {"odt", "ods", "odp"} <= set(reg.load_entry_points())


@pytest.mark.parametrize("fmt", ["odt", "ods", "odp"])
def test_detect_and_open(fmt: str, tmp_path: Path) -> None:
    f = tmp_path / f"a.{fmt}"
    f.write_bytes(make_odf(fmt, BODIES[fmt]))
    assert anydoc.detect(f).format == fmt
    doc = anydoc.open(f)
    assert isinstance(doc, Document)
    assert doc.format == fmt
    assert doc.metadata.format == fmt
    assert doc.metadata.mime == MIME[fmt]
    assert doc.path == f


def test_detect_wrong_extension(tmp_path: Path) -> None:
    f = tmp_path / "mislabelled.txt"
    f.write_bytes(make_odf("ods", ODS_BODY))
    assert anydoc.open(f).format == "ods"


# -- metadata ----------------------------------------------------------------


def test_metadata(odt: OdtDocument) -> None:
    m = odt.metadata
    assert m.title == TITLE
    assert m.author == "王小明"
    assert m.created == datetime(2026, 1, 2, 3, 4, 5)
    assert m.modified == datetime(2026, 2, 3, 4, 5, 6)
    assert m.extra["subject"] == "主旨"
    assert m.extra["keywords"] == ["甲", "乙"]
    assert m.extra["generator"] == "unit-test"


def test_metadata_missing_meta_xml() -> None:
    doc = OdtHandler().open(make_odf("odt", ODT_BODY, meta=None))
    assert doc.metadata.title is None
    assert doc.metadata.created is None
    assert doc.metadata.extra["keywords"] is None


def test_metadata_bad_date_ignored() -> None:
    meta = META.replace("2026-01-02T03:04:05", "not-a-date")
    doc = OdtHandler().open(make_odf("odt", ODT_BODY, meta=meta))
    assert doc.metadata.created is None


# -- ODT ---------------------------------------------------------------------


def test_odt_blocks(odt: OdtDocument) -> None:
    blocks = odt.blocks()
    assert [b.kind for b in blocks] == [
        "heading",
        "paragraph",
        "paragraph",
        "paragraph",
        "table",
        "heading",
        "paragraph",
    ]
    assert blocks[0].text == TITLE
    assert blocks[0].level == 1
    assert blocks[0].style == "Heading_20_1"
    assert blocks[1].text.startswith(f"{BODY}  甲\t乙\n丙")
    assert "註腳不計" not in blocks[1].text
    assert (blocks[2].text, blocks[2].list_depth) == ("項目一", 1)
    assert (blocks[3].text, blocks[3].list_depth) == ("子項目", 2)


def test_odt_headings_tables_images(odt: OdtDocument) -> None:
    assert [(h.text, h.level) for h in odt.headings()] == [(TITLE, 1), ("第二節", 2)]
    assert odt.tables() == [[["姓名", "分數"], ["小明", "95"]]]
    assert odt.images() == ["Pictures/a.png"]
    assert all(p.kind != "table" for p in odt.paragraphs())
    assert odt.parts() == []


def test_odt_text_reading_order(odt: OdtDocument) -> None:
    text = odt.text()
    assert (
        text.index(TITLE) < text.index("項目一") < text.index("姓名\t分數") < text.index("第二節")
    )
    assert "小明\t95" in text


def test_odt_section_and_toc_walked() -> None:
    body = "<office:text><text:section><text:p>節內</text:p></text:section></office:text>"
    doc = OdtHandler().open(make_odf("odt", body))
    assert doc.text() == "節內"


# -- ODS ---------------------------------------------------------------------


def test_ods_sheets(ods: OdsDocument) -> None:
    sheets = ods.sheets()
    assert [(s.name, s.dimensions) for s in sheets] == [("資料", "A1:D7"), ("空白", None)]
    assert [(p.kind, p.name) for p in ods.parts()] == [("sheet", "資料"), ("sheet", "空白")]


def test_ods_cells_types(ods: OdsDocument) -> None:
    assert ods.cell(0, "A1").value == "姓名"  # type: ignore[union-attr]
    score = ods.cell("資料", "b2")
    assert score is not None and (score.value, score.type) == (95, "number")
    d = ods.cell(0, "C2")
    assert d is not None and (d.value, d.type) == (date(2026, 3, 4), "date")
    f = ods.cell(0, "B6")
    assert f is not None
    assert (f.value, f.type, f.formula) == (2.5, "formula", "of:=SUM([.B2:.B2])")
    b = ods.cell(0, "C6")
    assert b is not None and (b.value, b.type) == (True, "boolean")
    assert ods.cell(0, "A6") is None
    assert ods.cell(0, "Z99") is None


def test_ods_repeated_cells_and_empty_rows(ods: OdsDocument) -> None:
    # rows 3-5 are an empty repeated run: skipped, but row numbering stays correct
    assert ods.cell(0, "B7").value == 0.5  # type: ignore[union-attr]
    assert ods.cell(0, "A7").value == 0.5  # type: ignore[union-attr]
    assert ods.cell(0, "C7").value == datetime(2026, 3, 4, 10, 20, 30)  # type: ignore[union-attr]
    nofv = ods.cell(0, "D7")
    assert nofv is not None and nofv.value is None and nofv.type == "formula"


def test_ods_huge_empty_sheet_is_cheap(ods: OdsDocument) -> None:
    assert ods.cells("空白") == []
    assert ods.rows("空白") == []


def test_ods_rows_and_text(ods: OdsDocument) -> None:
    rows = ods.rows()
    assert rows[0] == ["姓名", "分數", "日期", None]
    assert rows[1][:2] == ["小明", 95]
    assert rows[2] == [None, None, None, None]
    text = ods.text()
    assert text.startswith("資料\n姓名\t分數\t日期")
    assert "空白" in text


def test_ods_bad_sheet_lookup(ods: OdsDocument) -> None:
    with pytest.raises(IndexError):
        ods.cells(9)
    with pytest.raises(KeyError):
        ods.cells("nope")


def test_ods_column_letters_beyond_z() -> None:
    body = (
        '<office:spreadsheet><table:table table:name="s"><table:table-row>'
        '<table:table-cell table:number-columns-repeated="27"/>'
        '<table:table-cell office:value-type="string"><text:p>far</text:p></table:table-cell>'
        "</table:table-row></table:table></office:spreadsheet>"
    )
    doc = OdsHandler().open(make_odf("ods", body))
    assert isinstance(doc, OdsDocument)
    assert doc.cell(0, "AB1").value == "far"  # type: ignore[union-attr]


def test_ods_bad_date_and_time_values() -> None:
    body = (
        '<office:spreadsheet><table:table table:name="s"><table:table-row>'
        '<table:table-cell office:value-type="date" office:date-value="oops"/>'
        '<table:table-cell office:value-type="time" office:time-value="PT01H00M00S"/>'
        '<table:table-cell office:value-type="string" office:string-value="sv"/>'
        "</table:table-row></table:table></office:spreadsheet>"
    )
    doc = OdsHandler().open(make_odf("ods", body))
    assert isinstance(doc, OdsDocument)
    assert doc.rows()[0] == ["oops", "PT01H00M00S", "sv"]


# -- ODP ---------------------------------------------------------------------


def test_odp_slides(odp: OdpDocument) -> None:
    slides = odp.slides()
    assert len(slides) == 2
    s0 = slides[0]
    assert (s0.name, s0.layout, s0.title) == ("page1", "Default", TITLE)
    assert [(s.kind, s.placeholder) for s in s0.shapes] == [
        ("text", "title"),
        ("text", "outline"),
        ("picture", None),
        ("table", None),
    ]
    assert s0.shapes[1].text == BODY
    assert s0.shapes[3].table == [["甲", "乙"]]
    assert s0.notes == "備忘稿內容"
    assert slides[1].title is None
    assert slides[1].notes == ""
    assert odp.metadata.page_count == 2


def test_odp_slide_lookup_and_text(odp: OdpDocument) -> None:
    assert odp.slide(1).layout == "Other"
    assert odp.slide_text(1) == "第二頁"
    with pytest.raises(IndexError):
        odp.slide(2)
    assert [(p.kind, p.name) for p in odp.parts()] == [("slide", TITLE), ("slide", "page2")]
    text = odp.text()
    assert text.index(TITLE) < text.index("備忘稿內容") < text.index("第二頁")


# -- save / robustness ---------------------------------------------------------


def test_save_roundtrip(tmp_path: Path, odt: OdtDocument) -> None:
    out = odt.save(tmp_path / "copy.odt")
    again = anydoc.open(out)
    assert again.text() == odt.text()
    with pytest.raises(ValueError):
        odt.save()
    src = tmp_path / "s.odt"
    src.write_bytes(make_odf("odt", ODT_BODY))
    doc = anydoc.open(src)
    assert doc.save() == src
    doc.close()


@pytest.mark.parametrize(
    "data",
    [
        b"not a zip",
        b"",
    ],
)
def test_not_a_zip_is_corrupt(data: bytes) -> None:
    with pytest.raises(CorruptFileError):
        OdtHandler().open(data)


def test_zip_without_content_is_corrupt() -> None:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("mimetype", MIME["odt"])
    with pytest.raises(CorruptFileError, match="content.xml"):
        OdtHandler().open(buf.getvalue())


def test_wrong_mimetype_is_corrupt() -> None:
    with pytest.raises(CorruptFileError, match="Not an ODT"):
        OdtHandler().open(make_odf("ods", ODS_BODY))


def test_missing_body_kind_is_corrupt() -> None:
    for handler, kind in ((OdtHandler, "odt"), (OdsHandler, "ods"), (OdpHandler, "odp")):
        with pytest.raises(CorruptFileError):
            handler().open(make_odf(kind, "<office:chart/>"))


def test_no_office_body_is_corrupt() -> None:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("mimetype", MIME["odt"])
        zf.writestr("content.xml", f"<office:document-content {NS}/>")
    with pytest.raises(CorruptFileError, match="office:body"):
        OdtHandler().open(buf.getvalue())


def test_malformed_xml_is_corrupt() -> None:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("mimetype", MIME["odt"])
        zf.writestr("content.xml", "<office:document-content")
    with pytest.raises(CorruptFileError, match="Invalid ODF XML"):
        OdtHandler().open(buf.getvalue())


def test_entity_bomb_is_rejected() -> None:
    bomb = (
        '<?xml version="1.0"?><!DOCTYPE x [<!ENTITY a "aaaa"><!ENTITY b "&a;&a;&a;&a;">]>'
        f"<office:document-content {NS}><office:body><office:text><text:p>&b;</text:p>"
        "</office:text></office:body></office:document-content>"
    )
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("mimetype", MIME["odt"])
        zf.writestr("content.xml", bomb)
    with pytest.raises(CorruptFileError):
        OdtHandler().open(buf.getvalue())


def test_oversized_content_xml_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    import anydoc.formats.odf.handler as mod

    monkeypatch.setattr(mod, "_MAX_XML_BYTES", 10)
    with pytest.raises(SecurityLimitError):
        OdtHandler().open(make_odf("odt", ODT_BODY))


def test_unreadable_path_is_corrupt(tmp_path: Path) -> None:
    with pytest.raises(CorruptFileError, match="Cannot read"):
        OdtHandler().open(tmp_path / "missing.odt")


# -- real LibreOffice-authored files -------------------------------------------


@pytest.mark.requires("libreoffice")
@pytest.mark.parametrize(("src", "dst"), [("docx", "odt"), ("xlsx", "ods"), ("pptx", "odp")])
def test_libreoffice_generated_files(
    src: str, dst: str, sample: Callable[[str], Path], tmp_path: Path
) -> None:

    from anydoc.external import convert_with_libreoffice

    path = convert_with_libreoffice(sample(src), dst, tmp_path)
    assert anydoc.detect(path).format == dst
    doc = anydoc.open(path)
    assert doc.format == dst
    text = doc.text()
    assert TITLE in text or BODY in text or "王小明" in text
    if dst == "odt":
        assert isinstance(doc, OdtDocument) and doc.headings()
    elif dst == "ods":
        assert isinstance(doc, OdsDocument) and doc.cell(0, "A1") is not None
    else:
        assert isinstance(doc, OdpDocument) and doc.slides()
