"""Tests for XLSX conditional formatting and data validation (T065)."""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import pytest
from openpyxl import load_workbook

from anydoc.core.registry import registry
from anydoc.external import convert_with_libreoffice
from anydoc.formats.xlsx import XlsxDocument, XlsxHandler


@pytest.fixture(autouse=True)
def _handler() -> None:
    registry.register(XlsxHandler, replace=True)


def _doc() -> XlsxDocument:
    doc = XlsxHandler().create()
    doc.set_range(
        0,
        "A1",
        [["姓名", "分數"], ["小明", 90], ["小華", 55], ["小美", 72], ["小強", 55], ["阿德", None]],
    )
    return doc


def _rt(doc: XlsxDocument) -> XlsxDocument:
    return XlsxHandler().open(doc.to_bytes())


def test_cell_is_roundtrip() -> None:
    doc = _doc()
    prio = doc.add_conditional_format(
        0, "B2:B6", "cell_is", operator="<", value=60, fill="red", font_color="white", bold=True
    )
    assert prio >= 1
    (rule,) = _rt(doc).conditional_formats(0)
    assert rule["kind"] == "cell_is"
    assert rule["operator"] == "<"
    assert rule["formula"] == ["60"]
    assert rule["range"] == "B2:B6"
    assert rule["style"]["fill"] == "FF0000"
    assert rule["style"]["font_color"] == "FFFFFF"
    assert rule["style"]["bold"] is True


def test_cell_is_between_and_string_value() -> None:
    doc = _doc()
    doc.add_conditional_format(
        0, "B2:B6", "cell_is", operator="between", value=60, value2=80, fill="yellow"
    )
    doc.add_conditional_format(0, "A2:A6", "cell_is", operator="=", value="小明", italic=True)
    rules = _rt(doc).conditional_formats(0)
    assert rules[0]["formula"] == ["60", "80"]
    assert rules[1]["formula"] == ['"小明"']
    assert rules[1]["style"]["italic"] is True


@pytest.mark.parametrize(
    ("operator", "kind_name"),
    [
        ("contains", "contains"),
        ("not_contains", "not_contains"),
        ("begins_with", "begins_with"),
        ("ends_with", "ends_with"),
    ],
)
def test_text_rules(operator: str, kind_name: str) -> None:
    doc = _doc()
    doc.add_conditional_format(0, "A2:A6", "text", operator=operator, text="小", fill="green")
    (rule,) = _rt(doc).conditional_formats(0)
    assert rule["kind"] == "text"
    assert rule["operator"] == kind_name
    assert rule["text"] == "小"


def test_formula_top_bottom_duplicates_and_more() -> None:
    doc = _doc()
    doc.add_conditional_format(0, "A2:B6", "formula", formula="=$B2>80", fill="green")
    doc.add_conditional_format(0, "B2:B6", "top_n", rank=2, fill="orange")
    doc.add_conditional_format(0, "B2:B6", "bottom_n", rank=20, percent=True, fill="blue")
    doc.add_conditional_format(0, "B2:B6", "duplicates", font_color="red")
    doc.add_conditional_format(0, "B2:B6", "unique", bold=True)
    doc.add_conditional_format(0, "B2:B6", "blanks", fill="gray")
    doc.add_conditional_format(0, "B2:B6", "not_blanks", fill="yellow")
    doc.add_conditional_format(0, "B2:B6", "errors", fill="red")
    rules = _rt(doc).conditional_formats(0)
    kinds = [r["kind"] for r in rules]
    assert kinds == [
        "formula",
        "top_n",
        "bottom_n",
        "duplicates",
        "unique",
        "blanks",
        "not_blanks",
        "errors",
    ]
    assert rules[0]["formula"] == ["$B2>80"]
    assert rules[1]["rank"] == 2 and rules[1]["percent"] is False
    assert rules[2]["rank"] == 20 and rules[2]["percent"] is True


def test_color_scale_and_data_bar() -> None:
    doc = _doc()
    doc.add_conditional_format(0, "B2:B6", "color_scale")
    doc.add_conditional_format(0, "B2:B6", "color_scale", colors=["white", "blue"])
    doc.add_conditional_format(0, "B2:B6", "data_bar", bar_color="green")
    rules = _rt(doc).conditional_formats(0)
    assert rules[0]["colors"] == ["F8696B", "FFEB84", "63BE7B"]
    assert rules[1]["colors"] == ["FFFFFF", "0000FF"]
    assert rules[2]["kind"] == "data_bar"
    assert rules[2]["color"] == "00B050"


def test_priorities_stop_if_true_and_remove() -> None:
    doc = _doc()
    p1 = doc.add_conditional_format(0, "B2:B6", "duplicates", fill="red", stop_if_true=True)
    p2 = doc.add_conditional_format(0, "A2:A6", "unique", fill="red")
    assert p2 > p1
    assert doc.remove_conditional_formats(0, "$B$2:$B$6") == 1
    assert [r["range"] for r in doc.conditional_formats(0)] == ["A2:A6"]
    assert doc.remove_conditional_formats(0) == 1
    assert doc.conditional_formats(0) == []


def test_conditional_format_errors() -> None:
    doc = _doc()
    with pytest.raises(ValueError, match="Unknown rule kind"):
        doc.add_conditional_format(0, "A1", "nope", fill="red")
    with pytest.raises(ValueError, match="at least one"):
        doc.add_conditional_format(0, "A1", "duplicates")
    with pytest.raises(ValueError, match="operator"):
        doc.add_conditional_format(0, "A1", "cell_is", value=1, fill="red")
    with pytest.raises(ValueError, match="needs a value"):
        doc.add_conditional_format(0, "A1", "cell_is", operator=">", fill="red")
    with pytest.raises(ValueError, match="value2"):
        doc.add_conditional_format(0, "A1", "cell_is", operator="between", value=1, fill="red")
    with pytest.raises(ValueError, match="operator"):
        doc.add_conditional_format(0, "A1", "text", text="x", fill="red")
    with pytest.raises(ValueError, match="non-empty"):
        doc.add_conditional_format(0, "A1", "text", operator="contains", fill="red")
    with pytest.raises(ValueError, match="needs a formula"):
        doc.add_conditional_format(0, "A1", "formula", fill="red")
    with pytest.raises(ValueError, match="rank"):
        doc.add_conditional_format(0, "A1", "top_n", rank=0, fill="red")
    with pytest.raises(ValueError, match="2 or 3"):
        doc.add_conditional_format(0, "A1", "color_scale", colors=["red"])
    with pytest.raises(ValueError, match="Invalid range"):
        doc.add_conditional_format(0, "??", "duplicates", fill="red")
    with pytest.raises(ValueError, match="empty"):
        doc.add_conditional_format(0, " ", "duplicates", fill="red")


def test_list_validation_inline_roundtrip() -> None:
    doc = _doc()
    doc.add_data_validation(
        0,
        "C2:C6",
        "list",
        items=["通過", "不通過"],
        error_title="錯誤",
        error="請選擇",
        prompt="選一個",
    )
    (dv,) = _rt(doc).data_validations(0)
    assert dv["kind"] == "list"
    assert dv["items"] == ["通過", "不通過"]
    assert dv["range"] == "C2:C6"
    assert dv["error_title"] == "錯誤" and dv["error"] == "請選擇"
    assert dv["prompt"] == "選一個"
    assert dv["show_dropdown"] is True


def test_list_validation_range_source_and_hidden_dropdown() -> None:
    doc = _doc()
    doc.add_sheet("選項")
    doc.set_range("選項", "A1", [["甲"], ["乙"]])
    doc.add_data_validation(0, "C2", "list", source="=選項!$A$1:$A$2", show_dropdown=False)
    (dv,) = _rt(doc).data_validations(0)
    assert dv["items"] is None
    assert dv["source"] == "選項!$A$1:$A$2"
    assert dv["show_dropdown"] is False
    assert doc.validate_value(0, "C2", "甲") is True
    assert doc.validate_value(0, "C2", "丙") is False


@pytest.mark.parametrize(
    ("kind", "operator", "value", "value2", "good", "bad"),
    [
        ("whole", "between", 1, 10, 5, 11),
        ("whole", ">=", 0, None, 0, -1),
        ("decimal", "<", 1.5, None, 1.2, 1.5),
        ("text_length", "<=", 3, None, "abc", "abcd"),
        ("whole", "not_between", 1, 3, 5, 2),
        ("whole", "!=", 4, None, 3, 4),
        ("decimal", "=", 2.5, None, 2.5, 2.6),
        ("whole", ">", 4, None, 5, 4),
    ],
)
def test_numeric_validation(
    kind: str, operator: str, value: float, value2: float | None, good: object, bad: object
) -> None:
    doc = _doc()
    doc.add_data_validation(0, "D2:D6", kind, operator=operator, value=value, value2=value2)
    info = _rt(doc).data_validations(0)[0]
    assert info["kind"] == kind
    assert info["operator"] == ("!=" if operator == "<>" else operator)
    assert doc.validate_value(0, "D3", good) is True
    assert doc.validate_value(0, "D3", bad) is False


def test_whole_rejects_fraction_and_non_numbers() -> None:
    doc = _doc()
    doc.add_data_validation(0, "D2", "whole", operator=">=", value=0)
    assert doc.validate_value(0, "D2", 1.5) is False
    assert doc.validate_value(0, "D2", "abc") is False
    assert doc.validate_value(0, "D2", True) is False


def test_date_validation() -> None:
    doc = _doc()
    doc.add_data_validation(
        0,
        "E2:E6",
        "date",
        operator="between",
        value=dt.date(2026, 1, 1),
        value2=dt.date(2026, 12, 31),
    )
    info = _rt(doc).data_validations(0)[0]
    assert info["kind"] == "date"
    assert float(info["value"]) < float(info["value2"])
    assert doc.validate_value(0, "E2", dt.date(2026, 6, 1)) is True
    assert doc.validate_value(0, "E2", dt.datetime(2027, 1, 1)) is False
    doc.add_data_validation(0, "F2", "time", operator=">", value=0.5)
    assert doc.validate_value(0, "F2", dt.time(18, 0)) is True
    assert doc.validate_value(0, "F2", dt.time(6, 0)) is False


def test_custom_validation_and_formula_operand() -> None:
    doc = _doc()
    doc.add_data_validation(0, "G2:G6", "custom", formula="=ISNUMBER(G2)")
    doc.add_data_validation(0, "H2", "whole", operator=">", value="=B2")
    infos = _rt(doc).data_validations(0)
    assert infos[0]["formula"] == "ISNUMBER(G2)"
    assert infos[1]["value"] == "B2"
    assert doc.validate_value(0, "G3", "x") is True  # custom formulas are not evaluated
    assert doc.validate_value(0, "H2", 1) is True  # formula operands are not evaluated


def test_allow_blank_and_uncovered_cells() -> None:
    doc = _doc()
    doc.add_data_validation(0, "D2", "whole", operator=">", value=0, allow_blank=False)
    doc.add_data_validation(0, "D3", "whole", operator=">", value=0)
    assert doc.validate_value(0, "D2", None) is False
    assert doc.validate_value(0, "D3", "") is True
    assert doc.validate_value(0, "Z9", "anything") is True


def test_remove_validations() -> None:
    doc = _doc()
    doc.add_data_validation(0, "D2", "whole", operator=">", value=0)
    doc.add_data_validation(0, "D3", "list", items=["a"])
    assert doc.remove_data_validations(0, 0) == 1
    assert [d["range"] for d in doc.data_validations(0)] == ["D3"]
    assert doc.remove_data_validations(0) == 1
    assert doc.data_validations(0) == []
    with pytest.raises(ValueError, match="out of range"):
        doc.remove_data_validations(0, 0)


def test_validation_errors() -> None:
    doc = _doc()
    with pytest.raises(ValueError, match="Unknown validation kind"):
        doc.add_data_validation(0, "A1", "nope")
    with pytest.raises(ValueError, match="error_style"):
        doc.add_data_validation(0, "A1", "whole", value=1, error_style="boom")
    with pytest.raises(ValueError, match="exactly one"):
        doc.add_data_validation(0, "A1", "list")
    with pytest.raises(ValueError, match="exactly one"):
        doc.add_data_validation(0, "A1", "list", items=["a"], source="A1:A2")
    with pytest.raises(ValueError, match="at least one item"):
        doc.add_data_validation(0, "A1", "list", items=[])
    with pytest.raises(ValueError, match="commas"):
        doc.add_data_validation(0, "A1", "list", items=["a,b"])
    with pytest.raises(ValueError, match="255"):
        doc.add_data_validation(0, "A1", "list", items=["x" * 100] * 3)
    with pytest.raises(ValueError, match="source must not be empty"):
        doc.add_data_validation(0, "A1", "list", source="=")
    with pytest.raises(ValueError, match="needs a formula"):
        doc.add_data_validation(0, "A1", "custom")
    with pytest.raises(ValueError, match="Invalid operator"):
        doc.add_data_validation(0, "A1", "whole", operator="~", value=1)
    with pytest.raises(ValueError, match="needs a value"):
        doc.add_data_validation(0, "A1", "whole")
    with pytest.raises(ValueError, match="value2"):
        doc.add_data_validation(0, "A1", "whole", operator="between", value=1)
    with pytest.raises(ValueError, match="integer"):
        doc.add_data_validation(0, "A1", "whole", operator=">", value=1.5)
    with pytest.raises(ValueError, match="number"):
        doc.add_data_validation(0, "A1", "decimal", operator=">", value="x")
    with pytest.raises(ValueError, match="date"):
        doc.add_data_validation(0, "A1", "date", operator=">", value="x")


def test_missing_list_source_sheet_is_accepting() -> None:
    doc = _doc()
    doc.add_data_validation(0, "C2", "list", source="Nope!A1:A2")
    doc.add_data_validation(0, "C3", "list", source="NamedRange")
    assert doc.validate_value(0, "C2", "x") is True
    assert doc.validate_value(0, "C3", "x") is True


def test_package_contains_rules_and_other_sheet() -> None:
    doc = _doc()
    doc.add_sheet("S2")
    doc.add_conditional_format("S2", "A1:A3", "duplicates", fill="red")
    doc.add_data_validation("S2", "B1", "whole", operator=">", value=0)
    wb = load_workbook(__import__("io").BytesIO(doc.to_bytes()))
    assert len(wb["S2"].conditional_formatting) == 1
    assert len(wb["S2"].data_validations.dataValidation) == 1
    assert _rt(doc).conditional_formats("S2")[0]["kind"] == "duplicates"


@pytest.mark.requires("libreoffice")
def test_rules_survive_libreoffice_roundtrip(tmp_path: Path) -> None:
    doc = _doc()
    doc.add_conditional_format(0, "B2:B6", "cell_is", operator="<", value=60, fill="red")
    doc.add_data_validation(0, "C2:C6", "list", items=["甲", "乙"])
    src = tmp_path / "r.xlsx"
    src.write_bytes(doc.to_bytes())
    out = convert_with_libreoffice(src, "xlsx", tmp_path / "o")
    again = XlsxHandler().open(out)
    assert any(r["kind"] == "cell_is" for r in again.conditional_formats(0))
    dvs = again.data_validations(0)
    assert dvs and dvs[0]["kind"] == "list"
