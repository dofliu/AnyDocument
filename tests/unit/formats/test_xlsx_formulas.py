"""Tests for the XLSX formula engine (T061)."""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest
from openpyxl import Workbook, load_workbook
from openpyxl.utils.datetime import to_excel
from openpyxl.workbook.defined_name import DefinedName

from anydoc.core.errors import AnyDocError
from anydoc.external import convert_with_libreoffice
from anydoc.formats.xlsx import XlsxHandler
from anydoc.formats.xlsx.formulas import (
    FormulaEngine,
    FormulaSyntaxError,
    XlError,
    evaluate_formula,
    evaluate_workbook,
    parse_formula,
    supported_functions,
)


def _book() -> Workbook:
    wb = Workbook()
    ws = wb.active
    assert ws is not None
    ws.title = "資料"
    rows = [
        ["姓名", "部門", "分數", "備註"],
        ["小明", "A", 80, "ok"],
        ["小華", "B", 90, None],
        ["小美", "A", 70.5, "ok"],
        ["阿志", "B", None, "x"],
        ["小傑", "A", 100, "ok"],
    ]
    for r in rows:
        ws.append(r)
    ws["F1"] = 3
    ws["F2"] = "4"
    ws["F3"] = True
    other = wb.create_sheet("Sheet Two")
    other["A1"] = 10
    other["A2"] = 20
    wb.defined_names["Tax"] = DefinedName("Tax", attr_text="'Sheet Two'!$A$1")
    wb.defined_names["Scores"] = DefinedName("Scores", attr_text="資料!$C$2:$C$6")
    return wb


CASES: list[tuple[str, Any]] = [
    # arithmetic & operators
    ("=1+2*3", 7),
    ("=(1+2)*3", 9),
    ("=2^3^2", 64),
    ("=-2^2", 4),
    ("=10/4", 2.5),
    ("=50%", 0.5),
    ("=1/0", "#DIV/0!"),
    ('="a"&"b"&1', "ab1"),
    ('="abc"="ABC"', True),
    ("=1<2", True),
    ("=2<>2", False),
    ('=1<"a"', True),
    ("=TRUE+1", 2),
    ('="5"+1', 6),
    ('="x"+1', "#VALUE!"),
    ("=#N/A", "#N/A"),
    ("=1E3+.5", 1000.5),
    # aggregates over ranges
    ("=SUM(資料!C2:C6)", 340.5),
    ("=SUM(C2:C6)", 340.5),
    ("=AVERAGE(資料!C2:C6)", 85.125),
    ("=MIN(資料!C2:C6)", 70.5),
    ("=MAX(資料!C2:C6)", 100),
    ("=COUNT(資料!C2:C6)", 4),
    ("=COUNT(資料!F1:F3)", 1),
    ("=COUNTA(資料!A1:A6)", 6),
    ("=COUNTBLANK(資料!C2:C6)", 1),
    ("=MEDIAN(資料!C2:C6)", 85),
    ("=LARGE(資料!C2:C6,2)", 90),
    ("=SMALL(資料!C2:C6,1)", 70.5),
    ("=PRODUCT(2,3,4)", 24),
    ("=SUM(資料!F1:F3)", 3),
    ("=SUM(資料!F1,資料!F2,資料!F3)", 3),  # text/boolean cells referenced are ignored
    ('=SUM(1,"4",TRUE)', 6),  # literal arguments are coerced
    ("=SUM(資料!C:C)", 340.5),
    ("=SUM(資料!2:2)", 80 + 4 - 0 if False else 80),
    ("=SUMPRODUCT(資料!C2:C6,資料!C2:C6)", 6400 + 8100 + 4970.25 + 10000),
    ('=SUMPRODUCT((資料!B2:B6="A")*(資料!C2:C6))', 250.5),
    ("=STDEV.P(1,2,3,4)", 1.118033988749895),
    ("=VAR(1,2,3,4)", 1.6666666666666667),
    ("=SUBTOTAL(9,資料!C2:C6)", 340.5),
    ("=AVERAGE(資料!D2:D3)", "#DIV/0!"),
    # conditional aggregates
    ('=COUNTIF(資料!B2:B6,"A")', 3),
    ('=COUNTIF(資料!C2:C6,">=80")', 3),
    ('=COUNTIF(資料!D2:D6,"<>ok")', 2),
    ('=COUNTIF(資料!A2:A6,"小*")', 4),
    ('=COUNTIF(資料!A2:A6,"小?")', 4),
    ('=COUNTIF(資料!D2:D6,"")', 1),
    ('=SUMIF(資料!B2:B6,"B",資料!C2:C6)', 90),
    ('=SUMIF(資料!C2:C6,">75")', 270),
    ('=SUMIFS(資料!C2:C6,資料!B2:B6,"A",資料!D2:D6,"ok")', 250.5),
    ('=COUNTIFS(資料!B2:B6,"A",資料!C2:C6,">75")', 2),
    ('=AVERAGEIF(資料!B2:B6,"A",資料!C2:C6)', 250.5 / 3),
    ('=AVERAGEIFS(資料!C2:C6,資料!B2:B6,"B")', 90),
    ('=MAXIFS(資料!C2:C6,資料!B2:B6,"A")', 100),
    ('=MINIFS(資料!C2:C6,資料!B2:B6,"A")', 70.5),
    # math
    ("=ABS(-3.5)", 3.5),
    ("=ROUND(2.5,0)", 3),
    ("=ROUND(-2.5,0)", -3),
    ("=ROUND(1.2345,2)", 1.23),
    ("=ROUND(1234,-2)", 1200),
    ("=ROUNDUP(1.21,1)", 1.3),
    ("=ROUNDDOWN(-1.29,1)", -1.2),
    ("=INT(-1.5)", -2),
    ("=MOD(-3,2)", 1),
    ("=MOD(3,0)", "#DIV/0!"),
    ("=POWER(2,10)", 1024),
    ("=SQRT(16)", 4),
    ("=SQRT(-1)", "#NUM!"),
    ("=CEILING(2.1,0.5)", 2.5),
    ("=FLOOR(2.9,0.5)", 2.5),
    ("=SIGN(-9)", -1),
    ("=GCD(12,18)", 6),
    ("=LCM(4,6)", 12),
    ("=FACT(5)", 120),
    ("=QUOTIENT(7,2)", 3),
    ("=ISEVEN(4)", True),
    ("=LOG(8,2)", 3),
    ("=LOG10(1000)", 3),
    ("=EXP(0)", 1),
    ("=PI()", 3.141592653589793),
    # logical
    ('=IF(資料!C2>=80,"pass","fail")', "pass"),
    ('=IF(1>2,"a")', False),
    ("=AND(TRUE,1,資料!C2>0)", True),
    ("=OR(FALSE,0)", False),
    ("=NOT(FALSE)", True),
    ("=XOR(TRUE,TRUE)", False),
    ('=IFERROR(1/0,"bad")', "bad"),
    ("=IFERROR(5,0)", 5),
    ("=IFNA(NA(),7)", 7),
    ("=IFS(1>2,1,2>1,2)", 2),
    ('=SWITCH(2,1,"a",2,"b","z")', "b"),
    ('=CHOOSE(2,"x","y","z")', "y"),
    ("=IF(TRUE,1,1/0)", 1),
    ("=ISNUMBER(資料!C2)", True),
    ("=ISTEXT(資料!A2)", True),
    ("=ISBLANK(資料!C5)", True),
    ("=ISERROR(1/0)", True),
    ("=ISNA(NA())", True),
    # text
    ("=LEN(資料!A2)", 2),
    ('=LEFT("abcdef",3)', "abc"),
    ('=RIGHT("abcdef",2)', "ef"),
    ('=MID("abcdef",2,3)', "bcd"),
    ('=UPPER("abc")', "ABC"),
    ('=LOWER("ABC")', "abc"),
    ('=PROPER("hello world")', "Hello World"),
    ('=TRIM("  a   b ")', "a b"),
    ('=REPT("ab",3)', "ababab"),
    ('=CONCATENATE("a",1,TRUE)', "a1TRUE"),
    ("=CONCAT(資料!A2:A3)", "小明小華"),
    ('=TEXTJOIN(",",TRUE,資料!D2:D6)', "ok,ok,x,ok"),
    ('=TEXTJOIN("-",FALSE,資料!D2:D4)', "ok--ok"),
    ('=SUBSTITUTE("a-b-c","-","+")', "a+b+c"),
    ('=SUBSTITUTE("a-b-c","-","+",2)', "a-b+c"),
    ('=FIND("b","abc")', 2),
    ('=FIND("z","abc")', "#VALUE!"),
    ('=SEARCH("B?","aBcd")', 2),
    ('=VALUE("12.5")', 12.5),
    ('=EXACT("a","A")', False),
    ('=REPLACE("abcdef",2,3,"X")', "aXef"),
    ("=CHAR(65)", "A"),
    ('=CODE("A")', 65),
    ('=TEXT(1234.567,"#,##0.00")', "1,234.57"),
    ('=TEXT(0.256,"0.0%")', "25.6%"),
    ('=TEXT(5,"000")', "005"),
    ('=TEXT(DATE(2026,3,9),"yyyy-mm-dd")', "2026-03-09"),
    ('=TEXT(DATE(2026,3,9),"yyyy/m/d")', "2026/3/9"),
    # date
    ("=DATE(2026,1,1)", 46023),
    ("=YEAR(DATE(2026,5,6))", 2026),
    ("=MONTH(DATE(2026,5,6))", 5),
    ("=DAY(DATE(2026,5,6))", 6),
    ("=DATE(2026,14,1)-DATE(2027,2,1)", 0),
    ("=WEEKDAY(DATE(2026,11,6))", 6),
    ("=DAYS(DATE(2026,1,10),DATE(2026,1,1))", 9),
    # lookup
    ('=VLOOKUP("小美",資料!A2:C6,3,FALSE)', 70.5),
    ('=VLOOKUP("無此人",資料!A2:C6,3,FALSE)', "#N/A"),
    ('=VLOOKUP(2.5,{1,"a";2,"b";3,"c"},2)', "b"),
    ('=HLOOKUP("分數",資料!A1:D2,2,FALSE)', 80),
    ('=MATCH("B",資料!B2:B6,0)', 2),
    ("=MATCH(85,資料!C2:C3,1)", 1),
    ("=MATCH(5,{1,3,5,7},0)", 3),
    ("=INDEX(資料!A1:D6,3,1)", "小華"),
    ("=INDEX(資料!C2:C6,2)", 90),
    ('=INDEX(資料!A2:D6,MATCH("阿志",資料!A2:A6,0),4)', "x"),
    ("=INDEX(資料!A1:D6,9,1)", "#REF!"),
    ('=XLOOKUP("小傑",資料!A2:A6,資料!C2:C6)', 100),
    ('=LOOKUP(2,{1,2,3},{"a","b","c"})', "b"),
    ("=ROWS(資料!A1:D6)", 6),
    ("=COLUMNS(資料!A1:D6)", 4),
    ("=ROW(資料!B3)", 3),
    ("=COLUMN(資料!C1)", 3),
    ('=INDIRECT("資料!C3")', 90),
    ("=SUM(INDEX(資料!A1:D6,0,3))", 340.5),
    # names and cross-sheet references
    ("='Sheet Two'!A1+'Sheet Two'!A2", 30),
    ("=SUM('Sheet Two'!A1:A2)", 30),
    ("=Tax*2", 20),
    ("=SUM(Scores)", 340.5),
    ("=MissingName", "#NAME?"),
    ("='No Sheet'!A1", "#REF!"),
    # unsupported / wrong arity
    ("=NOSUCHFUNC(1)", "#NAME?"),
    ("=ABS()", "#NAME?"),
]


@pytest.mark.parametrize(("formula", "expected"), CASES, ids=[c[0] for c in CASES])
def test_formula_values(formula: str, expected: Any) -> None:
    got = evaluate_formula(_book(), formula, "資料")
    if isinstance(expected, float):
        assert got == pytest.approx(expected)
    else:
        assert got == expected
        assert type(got) is type(expected) or isinstance(expected, bool) is isinstance(got, bool)


def test_workbook_calculation_with_dependencies_and_order() -> None:
    wb = Workbook()
    ws = wb.active
    assert ws is not None
    ws["A1"] = "=B1+1"  # depends on later cells
    ws["B1"] = "=C1*2"
    ws["C1"] = 5
    ws["D1"] = "=SUM(A1:C1)"
    ws["E1"] = '=A1&"-"&B1'
    ws["F1"] = "=Other!A1+1"
    wb.create_sheet("Other")["A1"] = "=A1_missing"
    got = evaluate_workbook(wb)
    assert got[ws.title] == {"A1": 11, "B1": 10, "D1": 26, "E1": "11-10", "F1": "#NAME?"}
    assert got["Other"] == {"A1": "#NAME?"}


def test_long_dependency_chain() -> None:
    wb = Workbook()
    ws = wb.active
    assert ws is not None
    ws["A1"] = 1
    for i in range(2, 1501):
        ws[f"A{i}"] = f"=A{i - 1}+1"
    assert evaluate_workbook(wb)[ws.title]["A1500"] == 1500


def test_circular_reference() -> None:
    wb = Workbook()
    ws = wb.active
    assert ws is not None
    ws["A1"] = "=B1+1"
    ws["B1"] = "=A1+1"
    ws["C1"] = "=IFERROR(A1,0)"
    ws["D1"] = "=D1"
    engine = FormulaEngine(wb)
    out = engine.calculate()[ws.title]
    assert out["A1"] == "#CIRC!" and out["B1"] == "#CIRC!" and out["D1"] == "#CIRC!"
    assert out["C1"] == 0
    assert engine.circular


def test_bad_formula_text_gives_error_value_and_parse_raises() -> None:
    wb = Workbook()
    ws = wb.active
    assert ws is not None
    ws["A1"] = "=1+"
    ws["A2"] = "=SUM(1,2"
    assert evaluate_workbook(wb)[ws.title] == {"A1": "#NAME?", "A2": "#NAME?"}
    for bad in ("=1+", "=(1", "=SUM(1 2)", "=1 $ 2", "={1,A1}", "=SUM(1,2))"):
        with pytest.raises(FormulaSyntaxError):
            parse_formula(bad)
    assert issubclass(FormulaSyntaxError, AnyDocError)


def test_array_arithmetic_and_constants() -> None:
    wb = _book()
    assert evaluate_formula(wb, "=SUM({1,2;3,4})") == 10
    assert evaluate_formula(wb, "=SUMPRODUCT({1,2,3}*{4,5,6})") == 32
    assert evaluate_formula(wb, "=SUMPRODUCT(--(資料!C2:C6>75))", "資料") == 3
    assert evaluate_formula(wb, "=SUM(TRANSPOSE({1,2,3}))") == 6
    assert evaluate_formula(wb, "=SUMPRODUCT({1,2},{1,2,3})") == "#VALUE!"
    # array results collapse to their top-left element; a bare multi-cell range is an error
    assert evaluate_formula(wb, "=資料!C2:C3+1") == 81
    assert evaluate_formula(wb, "=資料!C2:C3") == "#VALUE!"


def test_supported_functions_list_and_parse_cache() -> None:
    names = supported_functions()
    assert len(names) >= 120 and {"SUM", "VLOOKUP", "SUMIFS", "TEXT"} <= set(names)
    assert parse_formula("=A1+1") is parse_formula("A1+1")
    assert str(XlError("#N/A")) == "#N/A" and XlError("#N/A") == XlError("#N/A")
    assert repr(XlError("#N/A")) == "XlError('#N/A')" and len({XlError("#N/A")}) == 1


def test_error_propagation_through_ranges() -> None:
    wb = Workbook()
    ws = wb.active
    assert ws is not None
    ws["A1"] = 1
    ws["A2"] = "=1/0"
    ws["A3"] = 3
    ws["B1"] = "=SUM(A1:A3)"
    ws["B2"] = '=COUNTIF(A1:A3,">0")'
    ws["B3"] = "=IFERROR(SUM(A1:A3),-1)"
    ws["B4"] = "=ISERROR(A2)"
    ws["B5"] = "=A2+1"
    out = evaluate_workbook(wb)[ws.title]
    assert out["B1"] == "#DIV/0!" and out["B5"] == "#DIV/0!"
    assert out["B2"] == 2 and out["B3"] == -1 and out["B4"] is True


def test_dates_in_cells_are_numeric() -> None:
    from datetime import date

    wb = Workbook()
    ws = wb.active
    assert ws is not None
    ws["A1"] = date(2026, 1, 10)
    ws["A2"] = date(2026, 1, 1)
    ws["B1"] = "=A1-A2"
    ws["B2"] = "=YEAR(A1)"
    ws["B3"] = '=TEXT(A1,"yyyy-mm-dd")'
    out = evaluate_workbook(wb)[ws.title]
    assert out == {"B1": 9, "B2": 2026, "B3": "2026-01-10"}


def test_document_calculate_and_evaluate() -> None:
    doc = XlsxHandler().create()
    doc.set_range(0, "A1", [[1, 2], [3, 4]])
    doc.set_cell(0, "C1", "=SUM(A1:B2)")
    doc.set_cell(0, "C2", "=C1*2")
    assert doc.cells()[-1].value is None  # no cached value until calculated
    result = doc.calculate()
    assert result == {"Sheet": {"C1": 10, "C2": 20}}
    values = {c.coordinate: c.value for c in doc.cells()}
    assert values["C1"] == 10 and values["C2"] == 20
    assert doc.rows() == [[1, 2, 10], [3, 4, 20]]
    assert doc.evaluate("=C2+A1") == 21
    assert doc.evaluate("=SUM(A1:A2)", "Sheet") == 4
    doc.set_cell(0, "A1", 100)  # an edit invalidates computed values
    assert next(c for c in doc.cells() if c.coordinate == "C1").value is None
    assert doc.calculate()["Sheet"]["C1"] == 109


def test_document_with_cached_values_keeps_them(tmp_path: Path) -> None:
    wb = Workbook()
    ws = wb.active
    assert ws is not None
    ws["A1"] = 2
    ws["A2"] = "=A1*3"
    path = tmp_path / "f.xlsx"
    wb.save(path)
    doc = XlsxHandler().open(path)
    assert doc.calculate() == {"Sheet": {"A2": 6}}
    assert doc.evaluate("=A2") == 6


def test_edit_then_calculate_roundtrip(tmp_path: Path) -> None:
    doc = XlsxHandler().create()
    doc.set_range(0, "A1", [["品項", "數量", "單價", "小計"]])
    doc.set_range(0, "A2", [["蘋果", 3, 10.5], ["香蕉", 2, 8], ["橘子", 5, 4.2]])
    for r in range(2, 5):
        doc.set_cell(0, f"D{r}", f"=B{r}*C{r}")
    doc.set_cell(0, "D5", "=SUM(D2:D4)")
    doc.set_cell(0, "E5", '=IF(D5>60,"高","低")')
    calc = doc.calculate()["Sheet"]
    assert calc["D2"] == 31.5 and calc["D5"] == pytest.approx(68.5) and calc["E5"] == "高"
    out = doc.save(tmp_path / "r.xlsx")
    again = XlsxHandler().open(out)
    assert again.calculate() == doc.calculate()


_XLFN = re.compile(
    r"\b(STDEV\.P|STDEV\.S|VAR\.S|VAR\.P|MAXIFS|MINIFS|IFS|SWITCH|XOR|IFNA|TEXTJOIN|CONCAT|DAYS)\("
)


def test_future_function_prefix_is_accepted() -> None:
    wb = _book()
    assert evaluate_formula(wb, "=_xlfn.IFS(1>2,1,TRUE,5)") == 5
    assert evaluate_formula(wb, '=_xlfn.CONCAT("a","b")') == "ab"
    assert evaluate_formula(wb, "=_xlfn.STDEV.S(1,2,3)") == pytest.approx(1.0)


def _lo_formulas() -> list[str]:
    # booleans count as numbers in LibreOffice's COUNT; XLOOKUP/INDIRECT are version-dependent
    skip = (
        "TODAY",
        "NOW",
        "INDIRECT",
        "XLOOKUP",
        "資料!F1",
        "SQRT(-1)",
        'SUM(1,"4"',
        "TRUE)",
        "No Sheet",
        "ABS()",
    )
    return [
        f
        for f, _ in CASES
        if not any(s in f for s in skip) and "Tax" not in f and "Scores" not in f
    ]


@pytest.mark.requires("libreoffice")
def test_results_match_libreoffice(tmp_path: Path) -> None:
    formulas = _lo_formulas()
    wb = _book()
    ws = wb.create_sheet("計算")
    for i, f in enumerate(formulas, start=1):
        ws.cell(row=i, column=1).value = _XLFN.sub(r"_xlfn.\1(", f)
    src = tmp_path / "calc.xlsx"
    wb.save(src)
    mine = evaluate_workbook(load_workbook(src))["計算"]
    out = convert_with_libreoffice(src, "xlsx", _outdir(tmp_path))
    lo = load_workbook(out, data_only=True)["計算"]
    mismatches = []
    for i, f in enumerate(formulas, start=1):
        a, b = mine.get(f"A{i}"), lo.cell(row=i, column=1).value
        if isinstance(b, datetime):  # date-formatted result: compare serial numbers
            b = to_excel(b)
        if isinstance(b, str) and b.startswith("Err:"):
            continue
        if isinstance(a, int | float) and not isinstance(a, bool) and isinstance(b, int | float):
            if a != pytest.approx(b, rel=1e-9, abs=1e-9):
                mismatches.append((f, a, b))
        elif a != b and not (a in (None, "") and b in (None, "")):
            mismatches.append((f, a, b))
    assert not mismatches, mismatches


def _outdir(tmp_path: Path) -> Path:
    d = tmp_path / "lo"
    d.mkdir()
    return d
