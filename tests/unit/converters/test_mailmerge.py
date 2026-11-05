"""Tests for DOCX mail merge (T056)."""

from __future__ import annotations

import json
import zipfile
from datetime import date
from pathlib import Path

import pytest
from openpyxl import Workbook
from typer.testing import CliRunner

import anydoc
from anydoc.cli import app
from anydoc.converters.mailmerge import (
    MergeData,
    format_value,
    load_records,
    merge_one,
    merge_to_files,
    merge_to_pdf,
    merge_to_single,
    template_fields,
)
from anydoc.core.errors import AnyDocError
from anydoc.core.registry import registry
from anydoc.formats.docx import DocxHandler


@pytest.fixture(autouse=True)
def _handler() -> None:
    registry.register(DocxHandler, replace=True)


def _template(tmp_path: Path) -> Path:
    doc = DocxHandler().create()
    doc.add_paragraph("敬啟者 {{ 姓名 }} 您好")
    p = doc.add_paragraph("")
    native = doc.native.paragraphs[-1]
    native.add_run("金額：{{am")
    native.add_run("ount}} 元").bold = True  # placeholder split across runs
    del p
    doc.add_table([["{{city}}", "固定"]])
    doc.set_header("Hdr {{姓名}}")
    path = tmp_path / "tpl.docx"
    path.write_bytes(doc.to_bytes())
    return path


def _csv(tmp_path: Path) -> Path:
    path = tmp_path / "data.csv"
    path.write_text("姓名,amount,city\n王小明,100,台中\n李大華,2500.5,台北\n", encoding="utf-8")
    return path


def _text(path_or_bytes: Path | bytes) -> str:
    doc = DocxHandler().open(path_or_bytes)
    try:
        parts = [doc.text()] + [h["header"] for h in doc.headers_footers()]
        return "\n".join(parts)
    finally:
        doc.close()


def test_template_fields(tmp_path: Path) -> None:
    assert template_fields(_template(tmp_path)) == ["姓名", "amount", "city"]


def test_load_records_csv_xlsx_json(tmp_path: Path) -> None:
    data = load_records(_csv(tmp_path))
    assert data.fields == ["姓名", "amount", "city"]
    assert data.records[0] == {"姓名": "王小明", "amount": 100, "city": "台中"}

    wb = Workbook()
    ws = wb.active
    assert ws is not None
    ws.append(["姓名", "amount", "city"])
    ws.append(["陳", 3, "高雄"])
    xlsx = tmp_path / "d.xlsx"
    wb.save(xlsx)
    assert load_records(xlsx).records == [{"姓名": "陳", "amount": 3, "city": "高雄"}]

    js = tmp_path / "d.json"
    js.write_text(json.dumps([{"姓名": "林", "amount": 5, "city": "新竹"}], ensure_ascii=False))
    assert load_records(js).records[0]["city"] == "新竹"


def test_load_records_errors(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        load_records(tmp_path / "nope.csv")
    bad = tmp_path / "d.xyz"
    bad.write_text("a")
    with pytest.raises(AnyDocError):
        load_records(bad)


def test_format_value() -> None:
    assert format_value(None) == ""
    assert format_value(3.0) == "3"
    assert format_value(2.5) == "2.5"
    assert format_value(True) == "TRUE"
    assert format_value(date(2026, 1, 2)) == "2026-01-02"


def test_merge_one_replaces_across_runs_and_headers(tmp_path: Path) -> None:
    out = merge_one(_template(tmp_path), {"姓名": "王", "amount": 7, "city": "A"})
    text = _text(out)
    assert "敬啟者 王 您好" in text
    assert "金額：7 元" in text
    assert "Hdr 王" in text
    assert "{{" not in text


def test_merge_to_files(tmp_path: Path) -> None:
    paths = merge_to_files(_template(tmp_path), _csv(tmp_path), tmp_path / "out", name_field="姓名")
    assert [p.name for p in paths] == ["001_王小明.docx", "002_李大華.docx"]
    assert "李大華" in _text(paths[1])
    assert "2500.5" in _text(paths[1])
    assert "台北" in _text(paths[1])
    assert "王小明" not in _text(paths[1])


def test_merge_to_files_default_names_and_bad_name_field(tmp_path: Path) -> None:
    paths = merge_to_files(_template(tmp_path), _csv(tmp_path), tmp_path / "o")
    assert [p.name for p in paths] == ["001.docx", "002.docx"]
    with pytest.raises(AnyDocError):
        merge_to_files(_template(tmp_path), _csv(tmp_path), tmp_path / "o2", name_field="zzz")


def test_missing_policies(tmp_path: Path) -> None:
    tpl = _template(tmp_path)
    data = MergeData(["姓名"], [{"姓名": "王"}])
    keep = _text(merge_one(tpl, data.records[0]))
    assert "{{city}}" in keep
    blank = _text(merge_one(tpl, data.records[0], missing="blank"))
    assert "{{" not in blank
    with pytest.raises(AnyDocError):
        merge_one(tpl, data.records[0], missing="error")
    with pytest.raises(AnyDocError):
        merge_to_files(tpl, data, tmp_path / "x", missing="error")
    with pytest.raises(ValueError):
        merge_to_files(tpl, data, tmp_path / "y", missing="bogus")  # type: ignore[arg-type]


def test_merge_to_single(tmp_path: Path) -> None:
    out = merge_to_single(_template(tmp_path), _csv(tmp_path), tmp_path / "all.docx")
    text = _text(out)
    assert "敬啟者 王小明 您好" in text
    assert "敬啟者 李大華 您好" in text
    assert "台中" in text and "台北" in text
    doc = anydoc.open(out)
    try:
        assert doc.text().count("敬啟者") == 2
    finally:
        doc.close()
    with zipfile.ZipFile(out) as z:
        body = z.read("word/document.xml").decode()
    assert body.count('w:type="page"') == 1


def test_merge_to_single_empty_data(tmp_path: Path) -> None:
    data = MergeData(["姓名"], [])
    out = merge_to_single(_template(tmp_path), data, tmp_path / "e.docx")
    assert "敬啟者" not in _text(out)


@pytest.mark.requires("libreoffice")
def test_merge_to_pdf(tmp_path: Path) -> None:
    out = merge_to_pdf(_template(tmp_path), _csv(tmp_path), tmp_path / "m.pdf")
    doc = anydoc.open(out)
    try:
        assert doc.metadata.page_count == 2
        text = doc.text()
    finally:
        doc.close()
    assert "王小明" in text and "李大華" in text


def test_cli_directory_docx_and_errors(tmp_path: Path) -> None:
    runner = CliRunner()
    tpl, data = _template(tmp_path), _csv(tmp_path)
    r = runner.invoke(app, ["mailmerge", str(tpl), str(data), str(tmp_path / "d"), "-n", "姓名"])
    assert r.exit_code == 0, r.output
    assert len(list((tmp_path / "d").glob("*.docx"))) == 2
    r = runner.invoke(app, ["mailmerge", str(tpl), str(data), str(tmp_path / "one.docx")])
    assert r.exit_code == 0 and (tmp_path / "one.docx").exists()
    r = runner.invoke(app, ["mailmerge", str(tpl), str(tmp_path / "no.csv"), str(tmp_path / "z")])
    assert r.exit_code == 2
    r = runner.invoke(
        app, ["mailmerge", str(tpl), str(data), str(tmp_path / "z"), "--missing", "bad"]
    )
    assert r.exit_code == 2
    r = runner.invoke(app, ["mailmerge", str(tpl), str(data), str(tmp_path / "z"), "-n", "nope"])
    assert r.exit_code == 1
