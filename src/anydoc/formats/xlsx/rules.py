"""XLSX conditional formatting and data validation on an ``openpyxl.Workbook`` (T065)."""

from __future__ import annotations

from typing import Any

from openpyxl.formatting import rule as _rule_mod
from openpyxl.styles import Font, PatternFill
from openpyxl.styles.differential import DifferentialStyle
from openpyxl.utils import datetime as _xl_datetime
from openpyxl.utils.cell import range_boundaries
from openpyxl.worksheet.datavalidation import DataValidation
from openpyxl.worksheet.worksheet import Worksheet

from anydoc.formats.xlsx.style import color_hex

# openpyxl's rule/validation constructors are loosely typed; treat them as ``Any``.
Rule: Any = _rule_mod.Rule
CellIsRule: Any = _rule_mod.CellIsRule
ColorScaleRule: Any = _rule_mod.ColorScaleRule
DataBarRule: Any = _rule_mod.DataBarRule
FormulaRule: Any = _rule_mod.FormulaRule
_DataValidation: Any = DataValidation
_to_excel: Any = _xl_datetime.to_excel

CELL_OPERATORS = {
    ">": "greaterThan",
    ">=": "greaterThanOrEqual",
    "<": "lessThan",
    "<=": "lessThanOrEqual",
    "=": "equal",
    "==": "equal",
    "!=": "notEqual",
    "<>": "notEqual",
    "between": "between",
    "not_between": "notBetween",
}
_OPERATOR_NAMES = {
    "greaterThan": ">",
    "greaterThanOrEqual": ">=",
    "lessThan": "<",
    "lessThanOrEqual": "<=",
    "equal": "=",
    "notEqual": "!=",
    "between": "between",
    "notBetween": "not_between",
}
TEXT_OPERATORS = {
    "contains": "containsText",
    "not_contains": "notContainsText",
    "begins_with": "beginsWith",
    "ends_with": "endsWith",
}
RULE_KINDS = (
    "cell_is",
    "text",
    "formula",
    "color_scale",
    "data_bar",
    "top_n",
    "bottom_n",
    "duplicates",
    "unique",
    "blanks",
    "not_blanks",
    "errors",
)
VALIDATION_KINDS = ("list", "whole", "decimal", "date", "time", "text_length", "custom")
_VALIDATION_TYPES = {
    "list": "list",
    "whole": "whole",
    "decimal": "decimal",
    "date": "date",
    "time": "time",
    "text_length": "textLength",
    "custom": "custom",
}
_VALIDATION_NAMES = {v: k for k, v in _VALIDATION_TYPES.items()}
_VALIDATION_OPERATORS = {
    "between": "between",
    "not_between": "notBetween",
    "=": "equal",
    "==": "equal",
    "!=": "notEqual",
    "<>": "notEqual",
    ">": "greaterThan",
    ">=": "greaterThanOrEqual",
    "<": "lessThan",
    "<=": "lessThanOrEqual",
}
_VALIDATION_OPERATOR_NAMES = {
    "between": "between",
    "notBetween": "not_between",
    "equal": "=",
    "notEqual": "!=",
    "greaterThan": ">",
    "greaterThanOrEqual": ">=",
    "lessThan": "<",
    "lessThanOrEqual": "<=",
}
_ERROR_STYLES = ("stop", "warning", "information")


def _normalize_range(ref: str) -> str:
    text = ref.strip().upper().replace("$", "")
    if not text:
        raise ValueError("Range must not be empty")
    probe = text if ":" in text else f"{text}:{text}"
    try:
        bounds = range_boundaries(probe)
    except (ValueError, TypeError):
        raise ValueError(f"Invalid range: {ref!r}") from None
    if any(b is None or b < 1 for b in bounds):
        raise ValueError(f"Range must be bounded (A1:C9): {ref!r}")
    return text


def _first_cell(ref: str) -> str:
    return ref.split(":")[0].split()[0]


def _formula_text(value: Any) -> str:
    """Render a rule operand as a formula fragment (strings are quoted)."""
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, (int, float)):
        return repr(value)
    text = str(value)
    if text.startswith("="):
        return text[1:]
    return '"' + text.replace('"', '""') + '"'


def _dxf(
    *,
    font_color: str | None,
    fill: str | None,
    bold: bool | None,
    italic: bool | None,
    strike: bool | None = None,
) -> DifferentialStyle:
    font = None
    if any(v is not None for v in (font_color, bold, italic, strike)):
        font = Font(
            color=color_hex(font_color) if font_color else None,
            bold=bold,
            italic=italic,
            strike=strike,
        )
    pattern = None
    if fill is not None:
        argb = color_hex(fill)
        pattern = PatternFill(fill_type="solid", start_color=argb, end_color=argb, bgColor=argb)
    return DifferentialStyle(font=font, fill=pattern)


def _cf_count(ws: Worksheet) -> int:
    return sum(len(r.rules) for r in ws.conditional_formatting)


def add_conditional_format(
    ws: Worksheet,
    cell_range: str,
    kind: str,
    *,
    operator: str | None = None,
    value: Any = None,
    value2: Any = None,
    formula: str | None = None,
    text: str | None = None,
    rank: int = 10,
    percent: bool = False,
    font_color: str | None = None,
    fill: str | None = None,
    bold: bool | None = None,
    italic: bool | None = None,
    colors: tuple[str, ...] | list[str] | None = None,
    bar_color: str = "638EC6",
    stop_if_true: bool = False,
) -> int:
    """Add a conditional-format rule on ``cell_range`` and return its priority.

    ``kind`` is one of :data:`RULE_KINDS`. Highlight kinds (``cell_is``, ``text``,
    ``formula``, ``top_n``, ``bottom_n``, ``duplicates``, ``unique``, ``blanks``,
    ``not_blanks``, ``errors``) apply ``font_color`` / ``fill`` / ``bold`` / ``italic``.
    ``color_scale`` takes 2 or 3 ``colors`` (min[, mid], max); ``data_bar`` takes ``bar_color``.
    """
    kind = kind.lower()
    if kind not in RULE_KINDS:
        raise ValueError(f"Unknown rule kind {kind!r}; expected one of {RULE_KINDS}")
    rng = _normalize_range(cell_range)
    first = _first_cell(rng)
    style_given = any(v is not None for v in (font_color, fill, bold, italic))
    highlight = kind not in ("color_scale", "data_bar")
    if highlight and not style_given:
        raise ValueError("Highlight rules need at least one of font_color/fill/bold/italic")
    dxf = _dxf(font_color=font_color, fill=fill, bold=bold, italic=italic) if highlight else None

    rule: Any
    if kind == "cell_is":
        if operator is None or operator not in CELL_OPERATORS:
            raise ValueError(f"cell_is needs operator in {sorted(CELL_OPERATORS)}")
        if value is None:
            raise ValueError("cell_is needs a value")
        between = CELL_OPERATORS[operator] in ("between", "notBetween")
        if between and value2 is None:
            raise ValueError(f"Operator {operator!r} needs value2")
        operands = [_formula_text(value)] + ([_formula_text(value2)] if between else [])
        rule = CellIsRule(operator=CELL_OPERATORS[operator], formula=operands)
    elif kind == "text":
        if operator is None or operator not in TEXT_OPERATORS:
            raise ValueError(f"text needs operator in {sorted(TEXT_OPERATORS)}")
        if not text:
            raise ValueError("text rule needs non-empty text")
        quoted = text.replace('"', '""')
        op = TEXT_OPERATORS[operator]
        expr = {
            "containsText": f'NOT(ISERROR(SEARCH("{quoted}",{first})))',
            "notContainsText": f'ISERROR(SEARCH("{quoted}",{first}))',
            "beginsWith": f'LEFT({first},LEN("{quoted}"))="{quoted}"',
            "endsWith": f'RIGHT({first},LEN("{quoted}"))="{quoted}"',
        }[op]
        rule = Rule(
            type=op,
            operator="notContains" if op == "notContainsText" else op,
            text=text,
            formula=[expr],
        )
    elif kind == "formula":
        if not formula:
            raise ValueError("formula rule needs a formula")
        rule = FormulaRule(formula=[formula.removeprefix("=")])
    elif kind in ("top_n", "bottom_n"):
        if rank < 1:
            raise ValueError("rank must be >= 1")
        rule = Rule(type="top10", rank=rank, percent=percent or None, bottom=kind == "bottom_n")
    elif kind == "duplicates":
        rule = Rule(type="duplicateValues")
    elif kind == "unique":
        rule = Rule(type="uniqueValues")
    elif kind == "blanks":
        rule = Rule(type="containsBlanks", formula=[f"LEN(TRIM({first}))=0"])
    elif kind == "not_blanks":
        rule = Rule(type="notContainsBlanks", formula=[f"LEN(TRIM({first}))>0"])
    elif kind == "errors":
        rule = Rule(type="containsErrors", formula=[f"ISERROR({first})"])
    elif kind == "color_scale":
        palette = list(colors) if colors else ["F8696B", "FFEB84", "63BE7B"]
        if len(palette) not in (2, 3):
            raise ValueError("color_scale needs 2 or 3 colors")
        hexes = [color_hex(c) for c in palette]
        if len(hexes) == 2:
            rule = ColorScaleRule(
                start_type="min", start_color=hexes[0], end_type="max", end_color=hexes[1]
            )
        else:
            rule = ColorScaleRule(
                start_type="min",
                start_color=hexes[0],
                mid_type="percentile",
                mid_value=50,
                mid_color=hexes[1],
                end_type="max",
                end_color=hexes[2],
            )
    else:  # data_bar
        rule = DataBarRule(
            start_type="min", end_type="max", color=color_hex(bar_color), showValue=True
        )

    if dxf is not None:
        rule.dxf = dxf
    if stop_if_true:
        rule.stopIfTrue = True
    ws.conditional_formatting.add(rng, rule)
    return int(rule.priority)


def _describe_rule(range_text: str, rule: Any) -> dict[str, Any]:
    info: dict[str, Any] = {"range": range_text, "priority": rule.priority}
    rtype = rule.type
    if rtype == "cellIs":
        info["kind"] = "cell_is"
        info["operator"] = _OPERATOR_NAMES.get(rule.operator or "", rule.operator)
        info["formula"] = list(rule.formula or [])
    elif rtype in ("containsText", "notContainsText", "beginsWith", "endsWith"):
        info["kind"] = "text"
        names = {v: k for k, v in TEXT_OPERATORS.items()}
        info["operator"] = names[rtype]
        info["text"] = rule.text
    elif rtype == "expression":
        info["kind"] = "formula"
        info["formula"] = list(rule.formula or [])
    elif rtype == "top10":
        info["kind"] = "bottom_n" if rule.bottom else "top_n"
        info["rank"] = rule.rank
        info["percent"] = bool(rule.percent)
    elif rtype == "duplicateValues":
        info["kind"] = "duplicates"
    elif rtype == "uniqueValues":
        info["kind"] = "unique"
    elif rtype == "containsBlanks":
        info["kind"] = "blanks"
    elif rtype == "notContainsBlanks":
        info["kind"] = "not_blanks"
    elif rtype == "containsErrors":
        info["kind"] = "errors"
    elif rtype == "colorScale":
        info["kind"] = "color_scale"
        scale = rule.colorScale
        info["colors"] = [c.rgb[2:] for c in scale.color if isinstance(c.rgb, str)]
    elif rtype == "dataBar":
        info["kind"] = "data_bar"
        color = rule.dataBar.color
        info["color"] = color.rgb[2:] if color is not None and isinstance(color.rgb, str) else None
    else:
        info["kind"] = rtype
    dxf: Any = rule.dxf
    if dxf is not None:
        fill = None
        if dxf.fill is not None:
            color = dxf.fill.bgColor if dxf.fill.bgColor is not None else dxf.fill.fgColor
            if color is not None and isinstance(color.rgb, str):
                fill = color.rgb[2:]
        font = dxf.font
        font_color = None
        if font is not None and font.color is not None and isinstance(font.color.rgb, str):
            font_color = font.color.rgb[2:]
        info["style"] = {
            "fill": fill,
            "font_color": font_color,
            "bold": bool(font.b) if font is not None and font.b is not None else None,
            "italic": bool(font.i) if font is not None and font.i is not None else None,
        }
    return info


def list_conditional_formats(ws: Worksheet) -> list[dict[str, Any]]:
    """Describe every conditional-format rule on a sheet, ordered by priority."""
    out: list[dict[str, Any]] = []
    for cf in ws.conditional_formatting:
        for rule in cf.rules:
            out.append(_describe_rule(str(cf.sqref), rule))
    out.sort(key=lambda d: d["priority"])
    return out


def remove_conditional_formats(ws: Worksheet, cell_range: str | None = None) -> int:
    """Remove rules on ``cell_range`` (exact match), or all rules when ``None``."""
    from openpyxl.formatting.formatting import ConditionalFormattingList

    target = _normalize_range(cell_range) if cell_range is not None else None
    removed = 0
    kept = ConditionalFormattingList()
    for cf in ws.conditional_formatting:
        sq = str(cf.sqref)
        if target is None or sq == target:
            removed += len(cf.rules)
            continue
        for rule in cf.rules:
            kept.add(sq, rule)
    ws.conditional_formatting = kept
    return removed


# -- data validation ---------------------------------------------------------


def _quote_list(items: list[Any]) -> str:
    strings = [str(i) for i in items]
    if not strings:
        raise ValueError("List validation needs at least one item")
    if any("," in s or '"' in s for s in strings):
        raise ValueError("List items must not contain commas or double quotes")
    joined = ",".join(strings)
    if len(joined) > 255:
        raise ValueError("Inline list is longer than 255 characters; use a range reference")
    return f'"{joined}"'


def add_data_validation(
    ws: Worksheet,
    cell_range: str,
    kind: str,
    *,
    items: list[Any] | None = None,
    source: str | None = None,
    operator: str = "between",
    value: Any = None,
    value2: Any = None,
    formula: str | None = None,
    allow_blank: bool = True,
    show_dropdown: bool = True,
    error_title: str | None = None,
    error: str | None = None,
    error_style: str = "stop",
    prompt_title: str | None = None,
    prompt: str | None = None,
) -> int:
    """Add a data-validation rule on ``cell_range`` and return its index on the sheet.

    ``kind`` is one of :data:`VALIDATION_KINDS`. ``list`` takes inline ``items`` or a
    ``source`` range/name (``=$A$1:$A$5`` or ``Sheet2!A1:A5``). ``whole``/``decimal``/
    ``date``/``time``/``text_length`` take ``operator`` with ``value`` (and ``value2`` for
    between/not_between). ``custom`` takes a ``formula``.
    """
    kind = kind.lower()
    if kind not in VALIDATION_KINDS:
        raise ValueError(f"Unknown validation kind {kind!r}; expected one of {VALIDATION_KINDS}")
    if error_style not in _ERROR_STYLES:
        raise ValueError(f"Invalid error_style {error_style!r}; expected one of {_ERROR_STYLES}")
    rng = _normalize_range(cell_range)

    formula1: str | None
    formula2: str | None = None
    op: str | None = None
    if kind == "list":
        if (items is None) == (source is None):
            raise ValueError("list validation needs exactly one of items or source")
        formula1 = _quote_list(items) if items is not None else _source_ref(source or "")
    elif kind == "custom":
        if not formula:
            raise ValueError("custom validation needs a formula")
        formula1 = formula.removeprefix("=")
    else:
        if operator not in _VALIDATION_OPERATORS:
            raise ValueError(
                f"Invalid operator {operator!r}; expected {sorted(_VALIDATION_OPERATORS)}"
            )
        op = _VALIDATION_OPERATORS[operator]
        if value is None:
            raise ValueError(f"{kind} validation needs a value")
        if op in ("between", "notBetween") and value2 is None:
            raise ValueError(f"Operator {operator!r} needs value2")
        formula1 = _validation_operand(kind, value)
        if op in ("between", "notBetween"):
            formula2 = _validation_operand(kind, value2)

    dv = _DataValidation(
        type=_VALIDATION_TYPES[kind],
        operator=op,
        formula1=formula1,
        formula2=formula2,
        allow_blank=allow_blank,
        showDropDown=not show_dropdown if kind == "list" else None,
        errorStyle=error_style,
        errorTitle=error_title,
        error=error,
        promptTitle=prompt_title,
        prompt=prompt,
        showErrorMessage=True,
        showInputMessage=prompt is not None or prompt_title is not None,
    )
    dv.add(rng)
    ws.add_data_validation(dv)
    return len(ws.data_validations.dataValidation) - 1


def _source_ref(source: str) -> str:
    text = source.strip().removeprefix("=")
    if not text:
        raise ValueError("list source must not be empty")
    return text


def _validation_operand(kind: str, value: Any) -> str:
    from datetime import date, datetime, time

    if isinstance(value, str) and value.startswith("="):
        return value[1:]
    if kind in ("date", "time"):
        if isinstance(value, (date, datetime, time)):
            return repr(_to_excel(value))
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return repr(value)
        raise ValueError(f"{kind} validation needs a date/time/number or '=formula', got {value!r}")
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{kind} validation needs a number or '=formula', got {value!r}")
    if kind in ("whole", "text_length") and value != int(value):
        raise ValueError(f"{kind} validation needs an integer, got {value!r}")
    return repr(int(value)) if kind in ("whole", "text_length") else repr(value)


def list_data_validations(ws: Worksheet) -> list[dict[str, Any]]:
    """Describe the data-validation rules on a sheet."""
    out: list[dict[str, Any]] = []
    for i, dv in enumerate(ws.data_validations.dataValidation):
        kind = _VALIDATION_NAMES.get(dv.type or "", dv.type)
        info: dict[str, Any] = {
            "index": i,
            "range": str(dv.sqref),
            "kind": kind,
            "allow_blank": bool(dv.allow_blank),
            "error_style": dv.errorStyle or "stop",
            "error_title": dv.errorTitle,
            "error": dv.error,
            "prompt_title": dv.promptTitle,
            "prompt": dv.prompt,
        }
        if kind == "list":
            f1 = dv.formula1 or ""
            if f1.startswith('"') and f1.endswith('"') and len(f1) >= 2:
                info["items"] = f1[1:-1].split(",")
                info["source"] = None
            else:
                info["items"] = None
                info["source"] = f1
            info["show_dropdown"] = not dv.showDropDown
        elif kind == "custom":
            info["formula"] = dv.formula1
        else:
            info["operator"] = _VALIDATION_OPERATOR_NAMES.get(
                dv.operator or "between", dv.operator or "between"
            )
            info["value"] = dv.formula1
            info["value2"] = dv.formula2
        out.append(info)
    return out


def remove_data_validations(ws: Worksheet, index: int | None = None) -> int:
    """Remove one validation rule by index, or all when ``index`` is ``None``."""
    rules = ws.data_validations.dataValidation
    if index is None:
        n = len(rules)
        rules.clear()
        return n
    if not 0 <= index < len(rules):
        raise ValueError(f"Validation index {index} out of range")
    del rules[index]
    return 1


def check_value(ws: Worksheet, coordinate: str, value: Any) -> bool:
    """Return whether ``value`` would be accepted in ``coordinate`` by its validation rules.

    Cells without a rule accept anything. Custom-formula rules are not evaluated here
    and are treated as accepting.
    """
    from datetime import date, datetime, time

    coord = coordinate.strip().upper().replace("$", "")
    for dv in ws.data_validations.dataValidation:
        if coord not in dv.sqref:
            continue
        if value is None or value == "":
            if dv.allow_blank:
                continue
            return False
        kind = _VALIDATION_NAMES.get(dv.type or "", dv.type)
        if kind == "custom":
            continue
        if kind == "list":
            f1 = dv.formula1 or ""
            if f1.startswith('"'):
                allowed = f1[1:-1].split(",")
                if str(value) not in allowed:
                    return False
            else:
                allowed_values = _resolve_list(ws, f1)
                if allowed_values is None:
                    continue
                if str(value) not in {str(v) for v in allowed_values}:
                    return False
            continue
        subject: float
        if kind == "text_length":
            subject = len(str(value))
        elif isinstance(value, (date, datetime, time)):
            subject = float(_to_excel(value))
        elif isinstance(value, (int, float)) and not isinstance(value, bool):
            subject = float(value)
        else:
            return False
        if kind == "whole" and subject != int(subject):
            return False
        try:
            lo = float(dv.formula1) if dv.formula1 is not None else None
            hi = float(dv.formula2) if dv.formula2 is not None else None
        except ValueError:
            continue  # formula operands are not evaluated
        if lo is None:
            continue
        if not _compare(subject, dv.operator or "between", lo, hi):
            return False
    return True


def _compare(x: float, op: str, lo: float, hi: float | None) -> bool:
    if op == "between":
        return hi is not None and lo <= x <= hi
    if op == "notBetween":
        return hi is not None and not lo <= x <= hi
    return {
        "equal": x == lo,
        "notEqual": x != lo,
        "greaterThan": x > lo,
        "greaterThanOrEqual": x >= lo,
        "lessThan": x < lo,
        "lessThanOrEqual": x <= lo,
    }[op]


def _resolve_list(ws: Worksheet, ref: str) -> list[Any] | None:
    """Resolve a list source range (optionally sheet-qualified) to cell values."""
    sheet = ws
    book: Any = ws.parent
    text = ref.replace("$", "")
    if "!" in text:
        name, _, text = text.rpartition("!")
        name = name.strip("'").replace("''", "'")
        if name not in book.sheetnames:
            return None
        sheet = book[name]
    try:
        probe = text if ":" in text else f"{text}:{text}"
        c1, r1, c2, r2 = (int(b) for b in range_boundaries(probe) if b is not None)
    except (ValueError, TypeError):
        return None
    return [sheet.cell(row=r, column=c).value for r in range(r1, r2 + 1) for c in range(c1, c2 + 1)]
