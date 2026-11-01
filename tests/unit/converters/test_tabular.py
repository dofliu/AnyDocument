"""Tests for XLSX / CSV / TSV / JSON interconversion (T035)."""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import date, datetime
from pathlib import Path

import pytest
from openpyxl import Workbook, load_workbook
from typer.testing import CliRunner

import anydoc
from anydoc.cli import app
from anydoc.converters import (
    csv_to_csv,
    csv_to_json,
    csv_to_xlsx,
    json_to_csv,
    json_to_xlsx,
    load_builtin_converters,
    register_tabular,
    xlsx_to_csv,
    xlsx_to_csv_files,
    xlsx_to_json,
)
from anydoc.core.convert import ConversionGraph, graph
from anydoc.core.errors import CorruptFileError

runner = CliRunner()
Sample = Callable[[str], Path]


def _typed_xlsx(path: Path) -> Path:
    wb = Workbook()
    ws = wb.active
    assert ws is not None
    ws.title = "資料"
    ws.append(["名稱", "數量", "單價", "完成", "日期", "時間"])
    ws.append(["蘋果", 3, 1.5, True, date(2024, 1, 2), datetime(2024, 1, 2, 3, 4, 5)])
    ws.append(["香蕉", 10, 0.25, False, date(2024, 2, 29), datetime(2024, 3, 1, 12, 0, 0)])
    ws.append(["櫻桃", None, None, None, None, None])
    wb.create_sheet("Notes;備註 1").append(["說明"])
    wb["Notes;備註 1"].append(["第二張工作表"])
    wb.save(path)
    return path


def test_registers_edges_on_graph() -> None:
    g = ConversionGraph()
    register_tabular(g)
    pairs = {(c.src, c.dst) for c in g.converters()}
    for a, b in [
        ("xlsx", "csv"),
        ("csv", "xlsx"),
        ("xlsx", "json"),
        ("json", "xlsx"),
        ("csv", "json"),
        ("json", "csv"),
        ("csv", "tsv"),
        ("tsv", "csv"),
        ("xlsx", "tsv"),
        ("tsv", "xlsx"),
    ]:
        assert (a, b) in pairs
    assert all(not c.requires for c in g.converters())


def test_global_graph_has_tabular_edges() -> None:
    load_builtin_converters()
    pairs = [(c.src, c.dst) for c in graph.converters()]
    assert ("xlsx", "csv") in pairs and ("json", "xlsx") in pairs
    assert len(pairs) == len(set(pairs))


def test_xlsx_to_csv_default_first_sheet(sample: Sample, tmp_path: Path) -> None:
    out = xlsx_to_csv(sample("xlsx"), tmp_path / "out.csv")
    lines = out.read_text(encoding="utf-8").splitlines()
    assert lines[0] == "姓名,部門,分數,,平均"
    assert lines[1].startswith("王小明,研發部,90,")
    assert len(lines) == 4


def test_xlsx_to_csv_select_sheet_by_name_and_index(sample: Sample, tmp_path: Path) -> None:
    by_name = xlsx_to_csv(sample("xlsx"), tmp_path / "a.csv", sheet="備註")
    by_index = xlsx_to_csv(sample("xlsx"), tmp_path / "b.csv", sheet=1)
    assert by_name.read_bytes() == by_index.read_bytes()
    assert "姓名" not in by_name.read_text(encoding="utf-8")


def test_xlsx_to_csv_unknown_sheet(sample: Sample, tmp_path: Path) -> None:
    with pytest.raises(KeyError):
        xlsx_to_csv(sample("xlsx"), tmp_path / "x.csv", sheet="nope")


def test_xlsx_to_csv_files_multi_sheet(tmp_path: Path) -> None:
    src = _typed_xlsx(tmp_path / "book.xlsx")
    paths = xlsx_to_csv_files(src, tmp_path / "out")
    assert [p.name for p in paths] == ["book-資料.csv", "book-Notes;備註_1.csv"]
    assert paths[1].read_text(encoding="utf-8") == "說明\n第二張工作表\n"
    assert paths[0].read_text(encoding="utf-8").splitlines()[0] == "名稱,數量,單價,完成,日期,時間"


def test_xlsx_to_csv_files_tsv_and_stem(sample: Sample, tmp_path: Path) -> None:
    paths = xlsx_to_csv_files(sample("xlsx"), tmp_path, delimiter="\t", stem="s")
    assert [p.suffix for p in paths] == [".tsv", ".tsv"]
    assert paths[0].name == "s-成績.tsv"
    assert paths[0].read_text(encoding="utf-8").splitlines()[0] == "姓名\t部門\t分數\t\t平均"


def test_xlsx_to_csv_files_duplicate_labels(tmp_path: Path) -> None:
    wb = Workbook()
    ws = wb.active
    assert ws is not None
    ws.title = "a b"
    ws["A1"] = 1
    wb.create_sheet("a_b")["A1"] = 2
    wb.save(tmp_path / "d.xlsx")
    names = [p.name for p in xlsx_to_csv_files(tmp_path / "d.xlsx", tmp_path / "o")]
    assert len(set(n.lower() for n in names)) == 2


def test_xlsx_typed_values_to_csv_text(tmp_path: Path) -> None:
    src = _typed_xlsx(tmp_path / "t.xlsx")
    out = xlsx_to_csv(src, tmp_path / "t.csv")
    lines = out.read_text(encoding="utf-8").splitlines()
    assert lines[1] == "蘋果,3,1.5,true,2024-01-02,2024-01-02 03:04:05"
    assert lines[2] == "香蕉,10,0.25,false,2024-02-29,2024-03-01 12:00:00"
    assert lines[3] == "櫻桃,,,,,"


def test_xlsx_to_csv_encoding_option(sample: Sample, tmp_path: Path) -> None:
    out = xlsx_to_csv(sample("xlsx"), tmp_path / "b.csv", encoding="big5")
    assert "姓名" in out.read_bytes().decode("big5")
    bom = xlsx_to_csv(sample("xlsx"), tmp_path / "c.csv", encoding="utf-8-sig")
    assert bom.read_bytes().startswith(b"\xef\xbb\xbf")


def test_csv_to_xlsx_preserves_types(sample: Sample, tmp_path: Path) -> None:
    out = csv_to_xlsx(sample("csv"), tmp_path / "o.xlsx")
    ws = load_workbook(out).active
    assert ws is not None
    assert [c.value for c in ws[1]] == ["姓名", "部門", "分數"]
    assert ws["A2"].value == "王小明"
    assert ws["C2"].value == 90 and isinstance(ws["C2"].value, int)


def test_csv_to_xlsx_all_types(tmp_path: Path) -> None:
    src = tmp_path / "t.csv"
    src.write_text(
        "名稱,數量,單價,完成,日期,時間\n"
        "蘋果,3,1.5,true,2024-01-02,2024-01-02 03:04:05\n"
        "香蕉,10,0.25,false,2024-02-29,2024-03-01 12:00:00\n",
        encoding="utf-8",
    )
    out = csv_to_xlsx(src, tmp_path / "t.xlsx", sheet_name="資料")
    wb = load_workbook(out)
    ws = wb["資料"]
    row = [c.value for c in ws[2]]
    assert row[:4] == ["蘋果", 3, 1.5, True]
    assert row[4] == datetime(2024, 1, 2) and ws["E2"].number_format == "yyyy-mm-dd"
    assert row[5] == datetime(2024, 1, 2, 3, 4, 5)
    assert ws["F2"].number_format == "yyyy-mm-dd hh:mm:ss"


@pytest.mark.parametrize("key", ["csv", "csv-bom", "csv-big5"])
def test_csv_encodings_to_xlsx_to_csv_roundtrip(sample: Sample, tmp_path: Path, key: str) -> None:
    xlsx = csv_to_xlsx(sample(key), tmp_path / "r.xlsx")
    back = xlsx_to_csv(xlsx, tmp_path / "r.csv")
    assert back.read_text(encoding="utf-8").splitlines()[:2] == [
        "姓名,部門,分數",
        "王小明,研發部,90",
    ]


def test_roundtrip_typed_xlsx_csv_xlsx(tmp_path: Path) -> None:
    src = _typed_xlsx(tmp_path / "t.xlsx")
    csv_path = xlsx_to_csv(src, tmp_path / "t.csv")
    back = csv_to_xlsx(csv_path, tmp_path / "back.xlsx")
    ws = load_workbook(back).active
    assert ws is not None
    got = [[c.value for c in r] for r in ws.iter_rows()]
    assert got[1] == ["蘋果", 3, 1.5, True, datetime(2024, 1, 2), datetime(2024, 1, 2, 3, 4, 5)]
    assert got[3] == ["櫻桃", None, None, None, None, None]


def test_xlsx_to_json_single_sheet_selected(tmp_path: Path) -> None:
    src = _typed_xlsx(tmp_path / "t.xlsx")
    out = xlsx_to_json(src, tmp_path / "t.json", sheet="資料")
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data[0] == {
        "名稱": "蘋果",
        "數量": 3,
        "單價": 1.5,
        "完成": True,
        "日期": "2024-01-02T00:00:00",
        "時間": "2024-01-02T03:04:05",
    }
    assert data[2]["數量"] is None
    assert "蘋果" in out.read_text(encoding="utf-8")  # ensure_ascii=False


def test_xlsx_to_json_multi_sheet_is_object(sample: Sample, tmp_path: Path) -> None:
    out = xlsx_to_json(sample("xlsx"), tmp_path / "m.json")
    data = json.loads(out.read_text(encoding="utf-8"))
    assert list(data) == ["成績", "備註"]
    assert data["成績"][0]["姓名"] == "王小明"
    assert data["備註"] == []  # single all-text row is the header


def test_xlsx_to_json_single_workbook_sheet_is_array(tmp_path: Path) -> None:
    wb = Workbook()
    ws = wb.active
    assert ws is not None
    ws.append(["a", "b"])
    ws.append([1, 2])
    wb.save(tmp_path / "one.xlsx")
    data = json.loads(xlsx_to_json(tmp_path / "one.xlsx", tmp_path / "o.json").read_text("utf-8"))
    assert data == [{"a": 1, "b": 2}]


def test_xlsx_to_json_headerless_and_duplicate_headers(tmp_path: Path) -> None:
    wb = Workbook()
    ws = wb.active
    assert ws is not None
    ws.append([1, 2])
    ws.append([3, 4])
    wb.create_sheet("dup").append(["x", "x", None])
    wb["dup"].append([1, 2, 3])
    wb.save(tmp_path / "h.xlsx")
    first = json.loads(
        xlsx_to_json(tmp_path / "h.xlsx", tmp_path / "1.json", sheet=0).read_text("utf-8")
    )
    assert first == [[1, 2], [3, 4]]
    dup = json.loads(
        xlsx_to_json(tmp_path / "h.xlsx", tmp_path / "2.json", sheet="dup").read_text("utf-8")
    )
    assert dup == [{"x": 1, "x_2": 2, "col3": 3}]


def test_json_to_xlsx_array_of_objects(tmp_path: Path) -> None:
    src = tmp_path / "r.json"
    src.write_text(
        json.dumps(
            [
                {"姓名": "王小明", "分數": 90, "通過": True},
                {"姓名": "陳美玲", "分數": 85.5, "備註": {"a": [1, 2]}},
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    ws = load_workbook(json_to_xlsx(src, tmp_path / "r.xlsx")).active
    assert ws is not None
    rows = [[c.value for c in r] for r in ws.iter_rows()]
    assert rows[0] == ["姓名", "分數", "通過", "備註"]
    assert rows[1] == ["王小明", 90, True, None]
    assert rows[2] == ["陳美玲", 85.5, None, '{"a": [1, 2]}']


def test_json_object_of_arrays_to_sheets(tmp_path: Path) -> None:
    src = tmp_path / "s.json"
    src.write_text(
        json.dumps({"成績": [{"a": 1}], "a/b:c": [[1, 2], [3]]}, ensure_ascii=False),
        encoding="utf-8",
    )
    wb = load_workbook(json_to_xlsx(src, tmp_path / "s.xlsx"))
    assert wb.sheetnames == ["成績", "a_b_c"]
    assert [c.value for c in wb["a_b_c"][2]] == [3, None]


def test_json_sheet_name_collisions_and_length(tmp_path: Path) -> None:
    src = tmp_path / "n.json"
    long = "長" * 40
    src.write_text(
        json.dumps({"a:b": [{"x": 1}], "a/b": [{"x": 2}], long: [{"x": 3}]}), encoding="utf-8"
    )
    names = load_workbook(json_to_xlsx(src, tmp_path / "n.xlsx")).sheetnames
    assert names[:2] == ["a_b", "a_b_2"]
    assert len(names[2]) == 31


def test_json_flat_object_is_one_row(tmp_path: Path) -> None:
    src = tmp_path / "f.json"
    src.write_text('{"a": 1, "b": "二"}', encoding="utf-8")
    out = json_to_csv(src, tmp_path / "f.csv")
    assert out.read_text(encoding="utf-8") == "a,b\n1,二\n"


def test_json_to_csv_sheet_selection(tmp_path: Path) -> None:
    src = tmp_path / "s.json"
    src.write_text('{"x": [{"a": 1}], "y": [{"b": 2}]}', encoding="utf-8")
    assert json_to_csv(src, tmp_path / "d.csv").read_text() == "a\n1\n"
    assert json_to_csv(src, tmp_path / "y.csv", sheet="y").read_text() == "b\n2\n"
    assert json_to_csv(src, tmp_path / "i.csv", sheet=1).read_text() == "b\n2\n"
    with pytest.raises(KeyError):
        json_to_csv(src, tmp_path / "e.csv", sheet="z")
    with pytest.raises(KeyError):
        json_to_csv(src, tmp_path / "e.csv", sheet=5)


def test_json_to_csv_delimiter_tsv(tmp_path: Path) -> None:
    src = tmp_path / "a.json"
    src.write_text('[{"a": 1, "b": "x,y"}]', encoding="utf-8")
    out = json_to_csv(src, tmp_path / "a.tsv", delimiter="\t")
    assert out.read_text() == "a\tb\n1\tx,y\n"


def test_json_array_of_arrays_pads_rows(tmp_path: Path) -> None:
    src = tmp_path / "a.json"
    src.write_text("[[1, 2, 3], [4]]", encoding="utf-8")
    assert json_to_csv(src, tmp_path / "a.csv").read_text() == "1,2,3\n4,,\n"


def test_json_bom_accepted(tmp_path: Path) -> None:
    src = tmp_path / "b.json"
    src.write_bytes(b"\xef\xbb\xbf" + '[{"名": 1}]'.encode())
    assert json_to_csv(src, tmp_path / "b.csv").read_text(encoding="utf-8") == "名\n1\n"


def test_json_invalid_inputs(tmp_path: Path) -> None:
    bad = tmp_path / "bad.json"
    bad.write_text("{nope", encoding="utf-8")
    with pytest.raises(CorruptFileError):
        json_to_xlsx(bad, tmp_path / "x.xlsx")
    bad.write_bytes(b"\xff\xfe\x00bad")
    with pytest.raises(CorruptFileError):
        json_to_csv(bad, tmp_path / "x.csv")
    scalar = tmp_path / "s.json"
    scalar.write_text("42", encoding="utf-8")
    with pytest.raises(ValueError, match="array or an object"):
        json_to_csv(scalar, tmp_path / "x.csv")
    mixed = tmp_path / "m.json"
    mixed.write_text('[{"a": 1}, [1]]', encoding="utf-8")
    with pytest.raises(ValueError, match="only objects or only arrays"):
        json_to_csv(mixed, tmp_path / "x.csv")


def test_empty_json_array_and_object(tmp_path: Path) -> None:
    empty = tmp_path / "e.json"
    empty.write_text("[]", encoding="utf-8")
    assert json_to_csv(empty, tmp_path / "e.csv").read_text() == ""
    wb = load_workbook(json_to_xlsx(empty, tmp_path / "e.xlsx"))
    assert wb.sheetnames == ["Sheet1"]
    obj = tmp_path / "o.json"
    obj.write_text("{}", encoding="utf-8")
    assert json_to_csv(obj, tmp_path / "o.csv").read_text() == "\n"  # one empty record


def test_csv_to_json_typed(sample: Sample, tmp_path: Path) -> None:
    out = csv_to_json(sample("csv-big5"), tmp_path / "c.json")
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data[0] == {"姓名": "王小明", "部門": "研發部", "分數": 90}
    assert len(data) == 3


def test_csv_without_header_to_json_arrays(tmp_path: Path) -> None:
    src = tmp_path / "n.csv"
    src.write_text("1,2\n3,4\n", encoding="utf-8")
    data = json.loads(csv_to_json(src, tmp_path / "n.json").read_text())
    assert data == [[1, 2], [3, 4]]


def test_csv_ragged_rows_padded(tmp_path: Path) -> None:
    src = tmp_path / "r.csv"
    src.write_text("a,b,c\n1,2\n3,4,5\n", encoding="utf-8")
    data = json.loads(csv_to_json(src, tmp_path / "r.json").read_text())
    assert data == [{"a": 1, "b": 2, "c": None}, {"a": 3, "b": 4, "c": 5}]


def test_json_csv_json_roundtrip(tmp_path: Path) -> None:
    original = [
        {"名稱": "蘋果", "數量": 3, "單價": 1.5, "完成": True},
        {"名稱": "香蕉", "數量": 10, "單價": 0.25, "完成": False},
    ]
    src = tmp_path / "a.json"
    src.write_text(json.dumps(original, ensure_ascii=False), encoding="utf-8")
    csv_path = json_to_csv(src, tmp_path / "a.csv")
    back = json.loads(csv_to_json(csv_path, tmp_path / "b.json").read_text(encoding="utf-8"))
    assert back == original


def test_json_xlsx_json_roundtrip_multi_sheet(tmp_path: Path) -> None:
    original = {
        "成績": [{"姓名": "王小明", "分數": 90}, {"姓名": "陳美玲", "分數": 85}],
        "備註": [{"內容": "好"}],
    }
    src = tmp_path / "a.json"
    src.write_text(json.dumps(original, ensure_ascii=False), encoding="utf-8")
    xlsx = json_to_xlsx(src, tmp_path / "a.xlsx")
    back = json.loads(xlsx_to_json(xlsx, tmp_path / "b.json").read_text(encoding="utf-8"))
    assert back == original


def test_csv_to_csv_retarget(sample: Sample, tmp_path: Path) -> None:
    tsv = csv_to_csv(sample("csv-big5"), tmp_path / "o.tsv", delimiter="\t")
    assert tsv.read_text(encoding="utf-8").splitlines()[0] == "姓名\t部門\t分數"
    back = csv_to_csv(tsv, tmp_path / "o.csv")
    assert back.read_bytes() == sample("csv").read_bytes()


def test_corrupt_xlsx_raises(tmp_path: Path) -> None:
    bad = tmp_path / "bad.xlsx"
    bad.write_bytes(b"not a zip")
    with pytest.raises(CorruptFileError):
        xlsx_to_csv(bad, tmp_path / "x.csv")
    with pytest.raises(CorruptFileError):
        xlsx_to_csv_files(bad, tmp_path / "o")
    with pytest.raises(CorruptFileError):
        xlsx_to_json(bad, tmp_path / "x.json")


def test_empty_workbook_to_csv(tmp_path: Path) -> None:
    wb = Workbook()
    wb.save(tmp_path / "e.xlsx")
    assert xlsx_to_csv(tmp_path / "e.xlsx", tmp_path / "e.csv").read_text() == ""
    data = json.loads(xlsx_to_json(tmp_path / "e.xlsx", tmp_path / "e.json").read_text())
    assert data == []


# -- conversion graph / public API / CLI --------------------------------------------------------


def test_anydoc_convert_xlsx_to_csv_and_json(sample: Sample, tmp_path: Path) -> None:
    anydoc.convert(sample("xlsx"), tmp_path / "o.csv")
    anydoc.convert(sample("xlsx"), tmp_path / "o.json")
    assert (tmp_path / "o.csv").read_text(encoding="utf-8").startswith("姓名,部門,分數")
    assert list(json.loads((tmp_path / "o.json").read_text(encoding="utf-8"))) == ["成績", "備註"]


def test_anydoc_convert_csv_to_xlsx_and_tsv(sample: Sample, tmp_path: Path) -> None:
    anydoc.convert(sample("csv-bom"), tmp_path / "o.xlsx")
    anydoc.convert(sample("csv"), tmp_path / "o.tsv")
    assert anydoc.extract_text(tmp_path / "o.xlsx").count("王小明") == 1
    assert "姓名\t部門\t分數" in (tmp_path / "o.tsv").read_text(encoding="utf-8")


def test_anydoc_convert_json_to_xlsx(tmp_path: Path) -> None:
    src = tmp_path / "in.json"
    src.write_text('[{"名": "甲", "值": 1}]', encoding="utf-8")
    out = anydoc.convert(src, tmp_path / "out.xlsx")
    ws = load_workbook(out).active
    assert ws is not None and ws["A2"].value == "甲"


def test_convert_chain_json_to_tsv_via_graph(tmp_path: Path) -> None:
    load_builtin_converters()
    path = graph.find_path("json", "tsv")
    assert [c.name for c in path] == ["json->tsv"]


def test_cli_convert_xlsx_to_csv(sample: Sample, tmp_path: Path) -> None:
    out = tmp_path / "cli.csv"
    result = runner.invoke(app, ["convert", str(sample("xlsx")), str(out)])
    assert result.exit_code == 0, result.output
    assert out.read_text(encoding="utf-8").startswith("姓名,部門,分數")


def test_cli_convert_json_to_xlsx(tmp_path: Path) -> None:
    src = tmp_path / "in.json"
    src.write_text('[{"a": 1}]', encoding="utf-8")
    result = runner.invoke(app, ["convert", str(src), str(tmp_path / "o.xlsx")])
    assert result.exit_code == 0, result.output
    assert (tmp_path / "o.xlsx").exists()
