"""Tests for CSV/TSV data operations (T080)."""

from __future__ import annotations

from typing import Any

import pytest

from anydoc.formats.csv import CsvDocument, CsvHandler, TsvHandler, ops

PEOPLE = (
    "姓名,部門,分數,到職日\n"
    "王小明,研發部,90,2021-03-01\n"
    "陳美玲,行銷部,85,2019-07-15\n"
    "林志豪,財務部,78,2022-11-30\n"
    "Alice,研發部,9.5,2020-01-05\n"
    "bob,行銷部,,\n"
)


def _doc(text: str = PEOPLE, *, tsv: bool = False) -> CsvDocument:
    handler = TsvHandler() if tsv else CsvHandler()
    return handler.open(text.encode("utf-8"))


def _names(doc: CsvDocument, column: int = 0) -> list[str]:
    return [r[column] for r in doc.rows]


# ---------------------------------------------------------------- columns / helpers


def test_column_names_with_and_without_header() -> None:
    assert _doc().column_names() == ["姓名", "部門", "分數", "到職日"]
    assert _doc("1,2,3\n4,5,6\n").column_names() == ["col1", "col2", "col3"]
    ragged = _doc("a,b\n1,2,3\n")
    assert ragged.column_names() == ["a", "b", "col3"]


def test_resolve_column_by_name_index_and_errors() -> None:
    doc = _doc()
    assert ops.resolve_column(doc, "分數") == 2
    assert ops.resolve_column(doc, " 分數 ") == 2
    assert ops.resolve_column(doc, 3) == 3
    with pytest.raises(ValueError, match="No such column"):
        ops.resolve_column(doc, "薪資")
    with pytest.raises(ValueError, match="out of range"):
        ops.resolve_column(doc, 9)
    with pytest.raises(ValueError, match="Invalid column reference"):
        ops.resolve_column(doc, True)
    dup = CsvDocument([["a", "a"], ["1", "2"]])
    with pytest.raises(ValueError, match="Ambiguous"):
        ops.resolve_column(dup, "a")


# ---------------------------------------------------------------- filter


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("分數>=85", ("分數", ">=", "85")),
        ("分數 <= 85", ("分數", "<=", "85")),
        ("部門=研發部", ("部門", "=", "研發部")),
        ("部門 == '研發部'", ("部門", "==", "研發部")),
        ('姓名!="bob"', ("姓名", "!=", "bob")),
        ("姓名~小", ("姓名", "~", "小")),
        ("姓名!~小", ("姓名", "!~", "小")),
        ("姓名=~^[A-Z]", ("姓名", "=~", "^[A-Z]")),
        ("分數=", ("分數", "=", "")),
        ("a>b=c", ("a", ">", "b=c")),
    ],
)
def test_parse_condition(text: str, expected: tuple[str, str, str]) -> None:
    assert ops.parse_condition(text) == expected


@pytest.mark.parametrize("text", ["分數", "", ">=3", "  = 3"])
def test_parse_condition_invalid(text: str) -> None:
    with pytest.raises(ValueError, match="Invalid condition"):
        ops.parse_condition(text)


@pytest.mark.parametrize(
    ("condition", "expected"),
    [
        ("分數>=85", ["王小明", "陳美玲"]),
        ("分數>9.5", ["王小明", "陳美玲", "林志豪"]),
        ("分數<80", ["林志豪", "Alice"]),
        ("分數<=78", ["林志豪", "Alice"]),
        ("分數=90.0", ["王小明"]),
        ("分數!=90", ["陳美玲", "林志豪", "Alice", "bob"]),
        ("分數=", ["bob"]),
        ("分數!=", ["王小明", "陳美玲", "林志豪", "Alice"]),
        ("部門=研發部", ["王小明", "Alice"]),
        ("姓名~ALI", ["Alice"]),
        ("姓名!~小", ["陳美玲", "林志豪", "Alice", "bob"]),
        ("姓名=~^[a-z]+$", ["bob"]),
        ("到職日>=2021-01-01", ["王小明", "林志豪"]),
        ("到職日<2020-01-05T08:00:00", ["陳美玲", "Alice"]),
        ("姓名>a", ["bob", "王小明", "陳美玲", "林志豪"]),
        (("分數", ">", 80), ["王小明", "陳美玲"]),
        ((1, "=", "財務部"), ["林志豪"]),
        (("分數", "=", None), ["bob"]),
    ],
)
def test_filter_single_condition(condition: Any, expected: list[str]) -> None:
    doc = _doc()
    removed = doc.filter_rows(condition)
    assert sorted(_names(doc)) == sorted(expected)
    assert removed == 5 - len(expected)
    assert doc.header == ["姓名", "部門", "分數", "到職日"]
    assert doc.metadata.extra["row_count"] == len(expected)


def test_filter_all_any_invert_and_callable() -> None:
    doc = _doc()
    doc.filter_rows(["部門=研發部", "分數>50"])
    assert _names(doc) == ["王小明"]
    doc = _doc()
    doc.filter_rows(["部門=財務部", "分數>=90"], match="any")
    assert _names(doc) == ["王小明", "林志豪"]
    doc = _doc()
    doc.filter_rows("部門=研發部", invert=True)
    assert _names(doc) == ["陳美玲", "林志豪", "bob"]
    doc = _doc()
    doc.filter_rows(lambda row: row["姓名"].isascii())
    assert _names(doc) == ["Alice", "bob"]


def test_filter_bool_values_and_text_fallback() -> None:
    doc = _doc("name,active\na,true\nb,FALSE\nc,maybe\n")
    doc.filter_rows("active=True")
    assert _names(doc) == ["a"]
    doc = _doc("name,active\na,true\nb,FALSE\nc,maybe\n")
    doc.filter_rows("active>false")  # bools order False < True; text falls back to str
    assert _names(doc) == ["a", "c"]


def test_filter_errors() -> None:
    doc = _doc()
    with pytest.raises(ValueError, match="match must be"):
        doc.filter_rows("分數>1", match="some")
    with pytest.raises(ValueError, match="At least one condition"):
        doc.filter_rows([])
    with pytest.raises(ValueError, match="No such column"):
        doc.filter_rows("薪資>1")
    with pytest.raises(ValueError, match="Unknown operator"):
        doc.filter_rows(("分數", "><", 1))
    with pytest.raises(ValueError, match="Invalid regular expression"):
        doc.filter_rows("姓名=~[")
    with pytest.raises(ValueError, match="Invalid condition"):
        doc.filter_rows(("分數", ">"))  # type: ignore[arg-type]
    assert len(doc.rows) == 5  # untouched after failures


def test_filter_headerless_and_ragged_rows() -> None:
    doc = _doc("1,2,3\n4,5\n7,8,9\n")
    assert not doc.has_header
    doc.filter_rows("col3>=3")
    assert doc.rows == [["1", "2", "3"], ["7", "8", "9"]]
    assert doc.to_bytes() == b"1,2,3\n7,8,9\n"


# ---------------------------------------------------------------- sort


def test_sort_numeric_and_blank_last() -> None:
    doc = _doc()
    doc.sort_rows("分數")
    assert _names(doc) == ["Alice", "林志豪", "陳美玲", "王小明", "bob"]
    doc.sort_rows("分數:desc")
    assert _names(doc) == ["王小明", "陳美玲", "林志豪", "Alice", "bob"]
    doc.sort_rows("分數", descending=True)
    assert _names(doc)[-1] == "bob"


def test_sort_dates_text_and_ignore_case() -> None:
    doc = _doc()
    doc.sort_rows(("到職日", True))
    assert _names(doc) == ["林志豪", "王小明", "Alice", "陳美玲", "bob"]
    doc.sort_rows(0)
    assert _names(doc)[:2] == ["Alice", "bob"]
    doc = _doc("name\nbeta\nAlpha\nalpha\nBeta\n")
    doc.sort_rows("name")
    assert _names(doc) == ["Alpha", "Beta", "alpha", "beta"]
    doc.sort_rows("name", ignore_case=True)
    assert _names(doc) == ["Alpha", "alpha", "Beta", "beta"]


def test_sort_multiple_keys_is_stable() -> None:
    doc = _doc()
    doc.sort_rows(["部門", "分數:desc"])
    assert doc.rows[0][1] == doc.rows[1][1]
    by_dept: dict[str, list[str]] = {}
    for row in doc.rows:
        by_dept.setdefault(row[1], []).append(row[0])
    assert by_dept["研發部"] == ["王小明", "Alice"]
    assert by_dept["行銷部"] == ["陳美玲", "bob"]


def test_sort_bool_column_and_column_named_like_direction() -> None:
    doc = _doc("name,ok\na,true\nb,false\nc,true\n")
    doc.sort_rows("ok")
    assert _names(doc) == ["b", "a", "c"]
    doc = _doc("x:desc,y\n2,a\n1,b\n")
    doc.sort_rows("x:desc")  # exact column name wins over the direction suffix
    assert _names(doc, 1) == ["b", "a"]


def test_sort_errors() -> None:
    doc = _doc()
    with pytest.raises(ValueError, match="At least one sort key"):
        doc.sort_rows([])
    with pytest.raises(ValueError, match="No such column"):
        doc.sort_rows("薪資:desc")


# ---------------------------------------------------------------- dedupe

DUPES = "name,city\nAnn,台北\nann,台北\nAnn,台北 \nBen,高雄\nAnn,台中\n"


def test_dedupe_all_columns_and_keep() -> None:
    doc = _doc(DUPES)
    assert doc.dedupe() == 1
    assert doc.rows == [["Ann", "台北"], ["ann", "台北"], ["Ben", "高雄"], ["Ann", "台中"]]
    doc = _doc(DUPES)
    assert doc.dedupe(keep="last") == 1
    assert doc.rows[1] == ["Ann", "台北 "]  # the later occurrence survives, order kept
    assert [r[0] for r in doc.rows] == ["ann", "Ann", "Ben", "Ann"]


def test_dedupe_by_columns_and_ignore_case() -> None:
    doc = _doc(DUPES)
    assert doc.dedupe(["name"]) == 2
    assert doc.rows == [["Ann", "台北"], ["ann", "台北"], ["Ben", "高雄"]]
    doc = _doc(DUPES)
    assert doc.dedupe(["name"], ignore_case=True) == 3
    assert _names(doc) == ["Ann", "Ben"]
    doc = _doc(DUPES)
    assert doc.dedupe(ignore_case=True) == 2
    assert doc.metadata.extra["row_count"] == 3


def test_dedupe_errors() -> None:
    doc = _doc(DUPES)
    with pytest.raises(ValueError, match="keep must be"):
        doc.dedupe(keep="middle")
    with pytest.raises(ValueError, match="No such column"):
        doc.dedupe(["nope"])


# ---------------------------------------------------------------- columns


def test_select_reorders_and_drop() -> None:
    doc = _doc()
    doc.select_columns(["分數", "姓名"])
    assert doc.header == ["分數", "姓名"]
    assert doc.rows[0] == ["90", "王小明"]
    assert doc.metadata.extra["columns"] == ["分數", "姓名"]
    doc = _doc()
    doc.drop_columns(["到職日", 1])
    assert doc.header == ["姓名", "分數"]
    assert doc.rows[-1] == ["bob", ""]


def test_select_and_drop_errors() -> None:
    doc = _doc()
    with pytest.raises(ValueError, match="At least one column"):
        doc.select_columns([])
    with pytest.raises(ValueError, match="more than once"):
        doc.select_columns(["姓名", 0])
    with pytest.raises(ValueError, match="Cannot drop every column"):
        doc.drop_columns([0, 1, 2, 3])
    with pytest.raises(ValueError, match="No such column"):
        doc.drop_columns(["薪資"])


def test_rename_columns() -> None:
    doc = _doc()
    doc.rename_columns({"姓名": "name", 2: "score"})
    assert doc.header == ["name", "部門", "score", "到職日"]
    assert doc.to_bytes().startswith("name,部門,score,到職日\n".encode())
    doc.rename_columns({"name": "score", "score": "name"})  # swap is allowed
    assert doc.header[:3] == ["score", "部門", "name"]
    with pytest.raises(ValueError, match="Duplicate column name"):
        doc.rename_columns({"部門": "name"})
    with pytest.raises(ValueError, match="Invalid new column name"):
        doc.rename_columns({"部門": " "})
    with pytest.raises(ValueError, match="No such column"):
        doc.rename_columns({"nope": "x"})


def test_rename_adds_header_to_headerless_table() -> None:
    doc = _doc("1,2\n3,4\n")
    doc.rename_columns({"col1": "x"})
    assert doc.has_header
    assert doc.header == ["x", "col2"]
    assert doc.to_bytes() == b"x,col2\n1,2\n3,4\n"


def test_select_keeps_headerless_tables_headerless() -> None:
    doc = _doc("1,2,3\n4,5,6\n")
    doc.select_columns(["col3", "col1"])
    assert not doc.has_header
    assert doc.to_bytes() == b"3,1\n6,4\n"


# ---------------------------------------------------------------- join

DEPTS = "部門,主管,分數\n研發部,張經理,A\n人資部,李經理,B\n研發部,副理,C\n"


def test_join_inner_many_to_many_and_suffix() -> None:
    left, right = _doc(), _doc(DEPTS)
    out = left.join(right, "部門")
    assert out.header == ["姓名", "部門", "分數", "到職日", "主管", "分數_right"]
    assert [(r[0], r[4]) for r in out.rows] == [
        ("王小明", "張經理"),
        ("王小明", "副理"),
        ("Alice", "張經理"),
        ("Alice", "副理"),
    ]
    assert len(left.rows) == 5 and len(right.rows) == 3  # inputs untouched
    assert out.has_header and out.encoding == left.encoding


def test_join_left_right_outer() -> None:
    left, right = _doc(), _doc(DEPTS)
    out = left.join(right, "部門", how="left", suffix="_r")
    assert len(out.rows) == 7
    assert out.header[-1] == "分數_r"
    assert out.rows[2] == ["陳美玲", "行銷部", "85", "2019-07-15", "", ""]
    out = left.join(right, "部門", how="right")
    assert len(out.rows) == 5
    assert out.rows[-1] == ["", "人資部", "", "", "李經理", "B"]
    out = left.join(right, "部門", how="outer")
    assert len(out.rows) == 8
    assert out.rows[-1][1] == "人資部"


def test_join_different_key_names_numeric_and_empty_keys() -> None:
    left = _doc("id,name\n1,甲\n2.0,乙\n,丙\n3,丁\n")
    right = _doc("code,price\n1.0,10\n2,20\n,99\n")
    out = left.join(right, "id", right_on="code")
    assert out.header == ["id", "name", "price"]
    assert out.rows == [["1", "甲", "10"], ["2.0", "乙", "20"]]
    out = left.join(right, ["id"], right_on=["code"], how="outer")
    assert out.rows[2:] == [["", "丙", ""], ["3", "丁", ""], ["", "", "99"]]


def test_join_composite_key() -> None:
    left = _doc("y,m,v\n2024,1,a\n2024,2,b\n2025,1,c\n")
    right = _doc("y,m,w\n2024,2,x\n2025,1,y\n")
    out = left.join(right, ["y", "m"])
    assert out.rows == [["2024", "2", "b", "x"], ["2025", "1", "c", "y"]]


def test_join_errors() -> None:
    left, right = _doc(), _doc(DEPTS)
    with pytest.raises(ValueError, match="how must be one of"):
        left.join(right, "部門", how="cross")
    with pytest.raises(ValueError, match="same, non-zero number"):
        left.join(right, ["部門", "分數"], right_on=["部門"])
    with pytest.raises(ValueError, match="same, non-zero number"):
        left.join(right, [])
    with pytest.raises(ValueError, match="No such column"):
        left.join(right, "姓名")


# ---------------------------------------------------------------- group-by

SALES = (
    "區域,業務,金額,日期\n"
    "北區,甲,100,2024-01-05\n"
    "南區,乙,250.5,2024-01-07\n"
    "北區,丙,300,2024-02-01\n"
    "北區,甲,,2024-02-03\n"
    "南區,乙,49.5,2024-03-09\n"
)


def test_group_by_basic_aggregates() -> None:
    out = _doc(SALES).group_by(
        "區域",
        ["count", "count:金額", "sum:金額", "mean:金額", "min:金額", "max:金額", "median:金額"],
    )
    assert out.header == [
        "區域",
        "count",
        "count_金額",
        "sum_金額",
        "mean_金額",
        "min_金額",
        "max_金額",
        "median_金額",
    ]
    assert out.rows == [
        ["北區", "3", "2", "400", "200", "100", "300", "200"],
        ["南區", "2", "2", "300", "150", "49.5", "250.5", "150"],
    ]


def test_group_by_text_aggregates_names_and_tuples() -> None:
    out = _doc(SALES).group_by(
        ["區域"],
        [
            "人數=nunique:業務",
            "first:日期",
            ("last", "日期"),
            ("max", "日期", "最近"),
            "avg:金額",
            "min:業務",
        ],
        sort=False,
    )
    assert out.header == [
        "區域",
        "人數",
        "first_日期",
        "last_日期",
        "最近",
        "mean_金額",
        "min_業務",
    ]
    assert out.rows[0] == ["北區", "2", "2024-01-05", "2024-02-03", "2024-02-03", "200", "丙"]
    assert out.rows[1][:2] == ["南區", "1"]


def test_group_by_multiple_keys_sorted_and_totals() -> None:
    out = _doc(SALES).group_by(["區域", "業務"], "sum:金額")
    assert out.rows == [["北區", "丙", "300"], ["北區", "甲", "100"], ["南區", "乙", "300"]]
    total = _doc(SALES).group_by([], ["count", "總額=sum:金額", "unique:區域"])
    assert total.header == ["count", "總額", "nunique_區域"]
    assert total.rows == [["5", "700", "2"]]
    empty = CsvDocument([["a", "b"]]).group_by([], ["count", "sum:b", "mean:b", "max:b", "first:b"])
    assert empty.rows == [["0", "0", "", "", ""]]


def test_group_by_sorts_numeric_keys_by_value_and_trims_float_noise() -> None:
    out = _doc("k,v\n10,0.1\n9,1\n10,0.2\n").group_by("k", ["sum:v", "median:v", "last:v"])
    assert out.rows == [["9", "1", "1", "1"], ["10", "0.3", "0.15", "0.2"]]


def test_group_by_errors() -> None:
    doc = _doc(SALES)
    with pytest.raises(ValueError, match="Unknown aggregate"):
        doc.group_by("區域", "mode:金額")
    with pytest.raises(ValueError, match="needs a column"):
        doc.group_by("區域", "sum")
    with pytest.raises(ValueError, match="At least one aggregate"):
        doc.group_by("區域", [])
    with pytest.raises(ValueError, match="Duplicate output column"):
        doc.group_by("區域", ["sum:金額", "sum:金額"])
    with pytest.raises(ValueError, match="Invalid aggregate"):
        doc.group_by("區域", [("sum",)])  # type: ignore[list-item]
    with pytest.raises(ValueError, match="No such column"):
        doc.group_by("地區", "count")


# ---------------------------------------------------------------- statistics


def test_describe() -> None:
    stats = {s["name"]: s for s in _doc(SALES).describe()}
    assert list(stats) == ["區域", "業務", "金額", "日期"]
    assert stats["金額"] == {
        "name": "金額",
        "type": "float",
        "count": 4,
        "empty": 1,
        "unique": 4,
        "min": 49.5,
        "max": 300,
        "sum": 700.0,
        "mean": 175.0,
        "median": 175.25,
    }
    assert stats["區域"] == {
        "name": "區域",
        "type": "str",
        "count": 5,
        "empty": 0,
        "unique": 2,
        "min": "北區",
        "max": "南區",
    }
    assert stats["日期"]["min"] == "2024-01-05" and stats["日期"]["type"] == "date"


def test_describe_selected_and_empty_columns() -> None:
    doc = _doc("a,b\n1,\n2,\n")
    assert [s["name"] for s in doc.describe(["b"])] == ["b"]
    assert doc.describe(["b"])[0] == {
        "name": "b",
        "type": "null",
        "count": 0,
        "empty": 2,
        "unique": 0,
        "min": None,
        "max": None,
    }
    assert doc.describe([0])[0]["sum"] == 3


# ---------------------------------------------------------------- misc


def test_tsv_operations_keep_delimiter() -> None:
    doc = _doc("name\tqty\n甲\t3\n乙\t1\n", tsv=True)
    doc.sort_rows("qty")
    assert doc.to_bytes() == "name\tqty\n乙\t1\n甲\t3\n".encode()
    out = doc.group_by([], "sum:qty")
    assert out.delimiter == "\t" and out.format == "tsv"


def test_module_level_functions_are_exported() -> None:
    from anydoc.formats import csv as csv_pkg

    for name in (
        "filter_rows",
        "sort_rows",
        "dedupe",
        "select_columns",
        "drop_columns",
        "rename_columns",
        "join",
        "group_by",
        "describe",
    ):
        assert getattr(csv_pkg, name) is getattr(ops, name)
