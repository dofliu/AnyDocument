"""A self-contained spreadsheet formula engine for XLSX workbooks (T061).

The engine parses Excel formula text into an AST and evaluates it lazily against an
``openpyxl.Workbook`` (cell dependencies are resolved on demand and memoised).  No third-party
formula package is used; it covers the commonly used operators and ~130 functions.

Results use plain Python values: ``None`` (blank), ``bool``, ``int``/``float``, ``str`` and
``XlError`` (spreadsheet errors such as ``#DIV/0!``).  Unsupported functions evaluate to
``#NAME?``; a cell that depends on itself evaluates to ``#CIRC!``.
"""

from __future__ import annotations

import math
import re
import statistics
import sys
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from openpyxl import Workbook
from openpyxl.utils.cell import column_index_from_string
from openpyxl.utils.datetime import from_excel, to_excel
from openpyxl.worksheet.worksheet import Worksheet

from anydoc.core.errors import AnyDocError

__all__ = [
    "FormulaEngine",
    "FormulaSyntaxError",
    "XlError",
    "evaluate_formula",
    "evaluate_workbook",
    "parse_formula",
    "supported_functions",
]


def _serial(value: Any) -> float:
    return float(to_excel(value))  # type: ignore[no-untyped-call]


def _from_serial(serial: float) -> datetime:
    result: Any = from_excel(serial)
    return result if isinstance(result, datetime) else datetime(1900, 1, 1)


class FormulaSyntaxError(AnyDocError):
    """Raised when formula text cannot be parsed."""


class XlError(Exception):
    """A spreadsheet error value (``#DIV/0!``, ``#N/A``...); also raised to propagate errors."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code

    def __eq__(self, other: object) -> bool:
        return isinstance(other, XlError) and other.code == self.code

    def __hash__(self) -> int:
        return hash(self.code)

    def __str__(self) -> str:
        return self.code

    def __repr__(self) -> str:
        return f"XlError({self.code!r})"


DIV0 = XlError("#DIV/0!")
NA = XlError("#N/A")
NAME = XlError("#NAME?")
NUM = XlError("#NUM!")
REF = XlError("#REF!")
VALUE = XlError("#VALUE!")
NULL = XlError("#NULL!")
CIRC = XlError("#CIRC!")
_ERRORS = {e.code: e for e in (DIV0, NA, NAME, NUM, REF, VALUE, NULL, CIRC)}

# ---------------------------------------------------------------------------
# Tokenizer / parser
# ---------------------------------------------------------------------------

_SHEET = r"(?:(?:'(?:[^']|'')+'|[^\W\d][\w.]*|\d[\w.]*)!)?"
_TOKEN_RE = re.compile(
    r"""
    (?P<ws>\s+)
  | (?P<str>"(?:[^"]|"")*")
  | (?P<err>\#(?:DIV/0!|N/A|NAME\?|NULL!|NUM!|REF!|VALUE!))
  | (?P<ref>@SHEET@\$?[A-Za-z]{1,3}\$?\d+(?::\$?[A-Za-z]{1,3}\$?\d+)?(?![\w(.!]))
  | (?P<colrange>@SHEET@\$?[A-Za-z]{1,3}:\$?[A-Za-z]{1,3}(?![\w(.!]))
  | (?P<rowrange>@SHEET@\$?\d+:\$?\d+(?![\w(.!]))
  | (?P<num>(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?)
  | (?P<name>[^\W\d][\w.]*)
  | (?P<op><=|>=|<>|[-+*/^&=<>%(),;{}:])
    """.replace("@SHEET@", _SHEET),
    re.VERBOSE,
)

Node = tuple[Any, ...]


def _tokenize(text: str) -> list[tuple[str, str]]:
    tokens: list[tuple[str, str]] = []
    pos = 0
    while pos < len(text):
        m = _TOKEN_RE.match(text, pos)
        if not m or m.end() == pos:
            raise FormulaSyntaxError(f"Unexpected character {text[pos]!r} at {pos} in {text!r}")
        pos = m.end()
        kind = m.lastgroup or ""
        if kind != "ws":
            tokens.append((kind, m.group()))
    return tokens


def _split_sheet(text: str) -> tuple[str | None, str]:
    if "!" not in text:
        return None, text
    sheet, _, rest = text.rpartition("!")
    if sheet.startswith("'"):
        sheet = sheet[1:-1].replace("''", "'")
    return sheet, rest


def _col(letters: str) -> int:
    return column_index_from_string(letters.replace("$", "").upper())


_CELL_RE = re.compile(r"\$?([A-Za-z]{1,3})\$?(\d+)")


class _Parser:
    def __init__(self, text: str) -> None:
        self.text = text
        self.toks = _tokenize(text)
        self.i = 0

    def peek(self) -> tuple[str, str]:
        return self.toks[self.i] if self.i < len(self.toks) else ("eof", "")

    def take(self) -> tuple[str, str]:
        tok = self.peek()
        self.i += 1
        return tok

    def accept(self, *ops: str) -> str | None:
        kind, val = self.peek()
        if kind == "op" and val in ops:
            self.i += 1
            return val
        return None

    def expect(self, op: str) -> None:
        if self.accept(op) is None:
            raise FormulaSyntaxError(f"Expected {op!r} in {self.text!r}")

    def parse(self) -> Node:
        node = self.comparison()
        if self.peek()[0] != "eof":
            raise FormulaSyntaxError(f"Unexpected token {self.peek()[1]!r} in {self.text!r}")
        return node

    def _left_assoc(self, sub: Callable[[], Node], ops: tuple[str, ...]) -> Node:
        node = sub()
        while (op := self.accept(*ops)) is not None:
            node = ("bin", op, node, sub())
        return node

    def comparison(self) -> Node:
        return self._left_assoc(self.concat, ("=", "<>", "<=", ">=", "<", ">"))

    def concat(self) -> Node:
        return self._left_assoc(self.additive, ("&",))

    def additive(self) -> Node:
        return self._left_assoc(self.term, ("+", "-"))

    def term(self) -> Node:
        return self._left_assoc(self.power, ("*", "/"))

    def power(self) -> Node:
        return self._left_assoc(self.unary, ("^",))

    def unary(self) -> Node:
        if (op := self.accept("-", "+")) is not None:
            return ("un", op, self.unary())
        return self.postfix()

    def postfix(self) -> Node:
        node = self.primary()
        while self.accept("%"):
            node = ("pct", node)
        return node

    def primary(self) -> Node:
        kind, val = self.take()
        if kind == "num":
            return ("lit", float(val) if any(c in val for c in ".eE") else int(val))
        if kind == "str":
            return ("lit", val[1:-1].replace('""', '"'))
        if kind == "err":
            return ("lit", _ERRORS[val])
        if kind == "ref":
            return self._ref(val)
        if kind == "colrange":
            sheet, rest = _split_sheet(val)
            a, b = rest.split(":")
            return ("range", sheet, 1, _col(a), None, _col(b))
        if kind == "rowrange":
            sheet, rest = _split_sheet(val)
            a, b = rest.replace("$", "").split(":")
            return ("range", sheet, int(a), 1, int(b), None)
        if kind == "name":
            if self.peek() == ("op", "("):
                self.take()
                fname = val.upper()
                for prefix in ("_XLFN.", "_XLWS."):  # future-function prefixes in OOXML files
                    fname = fname.removeprefix(prefix)
                return ("call", fname, self.args())
            upper = val.upper()
            if upper in ("TRUE", "FALSE"):
                return ("lit", upper == "TRUE")
            return ("name", val)
        if kind == "op":
            if val == "(":
                node = self.comparison()
                self.expect(")")
                return node
            if val == "{":
                return self.array_const()
        raise FormulaSyntaxError(f"Unexpected token {val!r} in {self.text!r}")

    def _ref(self, val: str) -> Node:
        sheet, rest = _split_sheet(val)
        parts = rest.split(":")
        cells = []
        for p in parts:
            m = _CELL_RE.fullmatch(p)
            if not m:
                raise FormulaSyntaxError(f"Bad cell reference {p!r}")
            cells.append((int(m.group(2)), _col(m.group(1))))
        if len(cells) == 1:
            return ("cell", sheet, cells[0][0], cells[0][1])
        (r1, c1), (r2, c2) = cells
        return ("range", sheet, min(r1, r2), min(c1, c2), max(r1, r2), max(c1, c2))

    def args(self) -> list[Node]:
        args: list[Node] = []
        if self.accept(")"):
            return args
        while True:
            if self.peek() in (("op", ","), ("op", ")")):
                args.append(("lit", None))  # omitted argument
            else:
                args.append(self.comparison())
            if self.accept(")"):
                return args
            if self.accept(",", ";") is None:
                raise FormulaSyntaxError(f"Expected ',' or ')' in {self.text!r}")

    def array_const(self) -> Node:
        rows: list[list[Any]] = [[]]
        while True:
            node = self.unary()
            if node[0] != "lit" and not (node[0] == "un" and node[2][0] == "lit"):
                raise FormulaSyntaxError("Array constants may only contain literals")
            rows[-1].append(_const_value(node))
            if self.accept("}"):
                return ("array", rows)
            if self.accept(","):
                continue
            if self.accept(";"):
                rows.append([])
                continue
            raise FormulaSyntaxError(f"Bad array constant in {self.text!r}")


def _const_value(node: Node) -> Any:
    if node[0] == "lit":
        return node[1]
    return -node[2][1] if node[1] == "-" else node[2][1]


_PARSE_CACHE: dict[str, Node] = {}


def parse_formula(formula: str) -> Node:
    """Parse formula text (a leading ``=`` is optional) into an AST tuple.

    Raises:
        FormulaSyntaxError: if the text is not a valid formula.
    """
    text = formula[1:] if formula.startswith("=") else formula
    node = _PARSE_CACHE.get(text)
    if node is None:
        node = _Parser(text.strip()).parse()
        if len(_PARSE_CACHE) > 20000:
            _PARSE_CACHE.clear()
        _PARSE_CACHE[text] = node
    return node


# ---------------------------------------------------------------------------
# Values and coercion
# ---------------------------------------------------------------------------


@dataclass
class Array:
    """A 2-D array of values (array constants, intermediate array results)."""

    rows: list[list[Any]]

    @property
    def shape(self) -> tuple[int, int]:
        return len(self.rows), len(self.rows[0]) if self.rows else 0


class Range:
    """A rectangular reference into a worksheet, read lazily through the engine."""

    def __init__(self, eng: FormulaEngine, ws: Worksheet, r1: int, c1: int, r2: int, c2: int):
        self.eng, self.ws = eng, ws
        self.r1, self.c1, self.r2, self.c2 = r1, c1, r2, c2

    @property
    def shape(self) -> tuple[int, int]:
        return self.r2 - self.r1 + 1, self.c2 - self.c1 + 1

    def cell(self, r: int, c: int) -> Any:
        return self.eng.cell_value(self.ws, self.r1 + r, self.c1 + c)

    @property
    def rows(self) -> list[list[Any]]:
        return [
            [self.eng.cell_value(self.ws, r, c) for c in range(self.c1, self.c2 + 1)]
            for r in range(self.r1, self.r2 + 1)
        ]


Arrayish = Range | Array


def _is_arr(v: Any) -> bool:
    return isinstance(v, Range | Array)


def _rows_of(v: Arrayish) -> list[list[Any]]:
    return v.rows


def _scalar(v: Any) -> Any:
    """Collapse a range/array to a scalar (single cell or top-left element)."""
    if isinstance(v, Range):
        if v.shape == (1, 1):
            return _scalar(v.cell(0, 0))
        raise VALUE
    if isinstance(v, Array):
        return v.rows[0][0] if v.rows and v.rows[0] else VALUE
    if isinstance(v, XlError):
        raise v
    return v


def _num(v: Any) -> float | int:
    """Coerce a scalar to a number (blank=0, TRUE=1, numeric text parsed)."""
    v = _scalar(v)
    if v is None:
        return 0
    if isinstance(v, bool):
        return int(v)
    if isinstance(v, int | float):
        return v
    if isinstance(v, str):
        s = v.strip()
        try:
            if s.endswith("%"):
                return float(s[:-1].replace(",", "")) / 100
            return float(s.replace(",", "")) if "." in s or "e" in s.lower() else int(s)
        except ValueError:
            raise VALUE from None
    raise VALUE


def _int(v: Any) -> int:
    return int(math.floor(_num(v)))


def _text(v: Any) -> str:
    v = _scalar(v)
    if v is None:
        return ""
    if isinstance(v, bool):
        return "TRUE" if v else "FALSE"
    if isinstance(v, int):
        return str(v)
    if isinstance(v, float):
        if v == int(v) and abs(v) < 1e15:
            return str(int(v))
        return f"{v:.15g}"
    if isinstance(v, datetime | date):
        return str(_serial(v))
    return str(v)


def _bool(v: Any) -> bool:
    v = _scalar(v)
    if v is None:
        return False
    if isinstance(v, bool):
        return v
    if isinstance(v, int | float):
        return v != 0
    if isinstance(v, str):
        if v.upper() == "TRUE":
            return True
        if v.upper() == "FALSE":
            return False
    raise VALUE


def _norm_cell(raw: Any) -> Any:
    if isinstance(raw, datetime | date):
        return _serial(raw)
    if isinstance(raw, time):
        return (raw.hour * 3600 + raw.minute * 60 + raw.second) / 86400
    if isinstance(raw, timedelta):
        return raw.total_seconds() / 86400
    if isinstance(raw, str) and raw in _ERRORS:
        return _ERRORS[raw]
    return raw


def _type_rank(v: Any) -> int:
    if isinstance(v, bool):
        return 2
    return 1 if isinstance(v, str) else 0


def _compare(a: Any, b: Any) -> int:
    """Excel ordering: numbers < text < booleans; text case-insensitive; blank adapts."""
    if a is None and b is None:
        return 0
    if a is None:
        a = "" if isinstance(b, str) else (False if isinstance(b, bool) else 0)
    if b is None:
        b = "" if isinstance(a, str) else (False if isinstance(a, bool) else 0)
    ra, rb = _type_rank(a), _type_rank(b)
    if ra != rb:
        return -1 if ra < rb else 1
    if ra == 1:
        a, b = a.lower(), b.lower()
    return int(a > b) - int(a < b)


def _round_half_up(x: float, digits: int) -> float:
    if not math.isfinite(x):
        return x
    d = Decimal(repr(abs(x)))
    q = Decimal(1).scaleb(-digits)
    r = float(d.quantize(q, rounding=ROUND_HALF_UP)) if digits < 300 else abs(x)
    return math.copysign(r, x) if r else 0.0


# ---------------------------------------------------------------------------
# Operators
# ---------------------------------------------------------------------------


def _broadcast(a: Any, b: Any, fn: Callable[[Any, Any], Any]) -> Any:
    if not (_is_arr(a) or _is_arr(b)):
        return fn(a, b)
    ra = _rows_of(a) if _is_arr(a) else [[a]]
    rb = _rows_of(b) if _is_arr(b) else [[b]]
    h = max(len(ra), len(rb))
    w = max(len(ra[0]), len(rb[0]))
    if (len(ra) not in (1, h)) or (len(rb) not in (1, h)):
        return Array([[NA] * w for _ in range(h)])
    out: list[list[Any]] = []
    for i in range(h):
        row = []
        for j in range(w):
            x = ra[i if len(ra) > 1 else 0][j if len(ra[0]) > 1 else 0]
            y = rb[i if len(rb) > 1 else 0][j if len(rb[0]) > 1 else 0]
            try:
                row.append(fn(x, y))
            except XlError as err:
                row.append(err)
        out.append(row)
    return Array(out)


def _arith(op: str, x: Any, y: Any) -> Any:
    a, b = _num(x), _num(y)
    if op == "+":
        return a + b
    if op == "-":
        return a - b
    if op == "*":
        return a * b
    if op == "/":
        if b == 0:
            raise DIV0
        return a / b
    try:
        if a == 0 and b < 0:
            raise DIV0
        result = float(a) ** b
    except (OverflowError, ZeroDivisionError):
        raise NUM from None
    if isinstance(result, complex):
        raise NUM
    return result


def _cmp_op(op: str, x: Any, y: Any) -> bool:
    for v in (x, y):
        if isinstance(v, XlError):
            raise v
    c = _compare(x, y)
    return {"=": c == 0, "<>": c != 0, "<": c < 0, ">": c > 0, "<=": c <= 0, ">=": c >= 0}[op]


def _binary(op: str, a: Any, b: Any) -> Any:
    if op in "+-*/^":
        return _broadcast(a, b, lambda x, y: _arith(op, x, y))
    if op == "&":
        return _broadcast(a, b, lambda x, y: _text(x) + _text(y))
    return _broadcast(a, b, lambda x, y: _cmp_op(op, x, y))


# ---------------------------------------------------------------------------
# Function library
# ---------------------------------------------------------------------------

_FUNCS: dict[str, tuple[Callable[..., Any], bool]] = {}


def _fn(*names: str, lazy: bool = False) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    def deco(f: Callable[..., Any]) -> Callable[..., Any]:
        for n in names:
            _FUNCS[n] = (f, lazy)
        return f

    return deco


def supported_functions() -> list[str]:
    """Return the sorted names of all functions the engine can evaluate."""
    return sorted(_FUNCS)


def _flat(args: Sequence[Any]) -> Iterator[Any]:
    """Numbers from arguments: scalars are coerced, range/array elements must be numbers."""
    for a in args:
        if _is_arr(a):
            for row in _rows_of(a):
                for v in row:
                    if isinstance(v, XlError):
                        raise v
                    if isinstance(v, int | float) and not isinstance(v, bool):
                        yield v
        else:
            if isinstance(a, XlError):
                raise a
            if a is None:
                continue
            yield _num(a)


def _cells(args: Sequence[Any]) -> Iterator[Any]:
    """Every raw value from arguments (ranges flattened)."""
    for a in args:
        if _is_arr(a):
            for row in _rows_of(a):
                yield from row
        else:
            yield a


def _nums(args: Sequence[Any]) -> list[float | int]:
    return list(_flat(args))


@_fn("SUM")
def _sum(*args: Any) -> Any:
    return sum(_nums(args))


@_fn("PRODUCT")
def _product(*args: Any) -> Any:
    return math.prod(_nums(args))


@_fn("AVERAGE")
def _average(*args: Any) -> Any:
    n = _nums(args)
    if not n:
        raise DIV0
    return sum(n) / len(n)


@_fn("MIN")
def _min(*args: Any) -> Any:
    n = _nums(args)
    return min(n) if n else 0


@_fn("MAX")
def _max(*args: Any) -> Any:
    n = _nums(args)
    return max(n) if n else 0


@_fn("MEDIAN")
def _median(*args: Any) -> Any:
    n = _nums(args)
    if not n:
        raise NUM
    return statistics.median(n)


@_fn("COUNT")
def _count(*args: Any) -> Any:
    n = 0
    for a in args:
        if _is_arr(a):
            n += sum(
                1 for v in _cells([a]) if isinstance(v, int | float) and not isinstance(v, bool)
            )
        else:
            try:
                _num(a)
                n += a is not None
            except XlError:
                pass
    return n


@_fn("COUNTA")
def _counta(*args: Any) -> Any:
    return sum(1 for v in _cells(args) if v is not None)


@_fn("COUNTBLANK")
def _countblank(rng: Any) -> Any:
    return sum(1 for v in _cells([rng]) if v is None or v == "")


def _stat(args: Sequence[Any], sample: bool, root: bool) -> float:
    n = _nums(args)
    if len(n) < (2 if sample else 1):
        raise DIV0
    var = statistics.variance(n) if sample else statistics.pvariance(n)
    return math.sqrt(var) if root else var


_fn("STDEV", "STDEV.S")(lambda *a: _stat(a, True, True))
_fn("STDEV.P", "STDEVP")(lambda *a: _stat(a, False, True))
_fn("VAR", "VAR.S")(lambda *a: _stat(a, True, False))
_fn("VAR.P", "VARP")(lambda *a: _stat(a, False, False))


@_fn("LARGE")
def _large(rng: Any, k: Any) -> Any:
    n = sorted(_nums([rng]), reverse=True)
    i = _int(k)
    if not 1 <= i <= len(n):
        raise NUM
    return n[i - 1]


@_fn("SMALL")
def _small(rng: Any, k: Any) -> Any:
    n = sorted(_nums([rng]))
    i = _int(k)
    if not 1 <= i <= len(n):
        raise NUM
    return n[i - 1]


@_fn("SUMPRODUCT")
def _sumproduct(*args: Any) -> Any:
    arrays = [_rows_of(a) if _is_arr(a) else [[a]] for a in args]
    shape = (len(arrays[0]), len(arrays[0][0]))
    if any((len(a), len(a[0])) != shape for a in arrays):
        raise VALUE
    total = 0.0
    for i in range(shape[0]):
        for j in range(shape[1]):
            p = 1.0
            for a in arrays:
                v = a[i][j]
                if isinstance(v, XlError):
                    raise v
                p *= v if isinstance(v, int | float) and not isinstance(v, bool) else 0
            total += p
    return total


@_fn("SUBTOTAL")
def _subtotal(code: Any, *args: Any) -> Any:
    table: dict[int, Callable[..., Any]] = {
        1: _average,
        2: _count,
        3: _counta,
        4: _max,
        5: _min,
        6: _product,
        9: _sum,
    }
    c = _int(code) % 100
    if c not in table:
        raise VALUE
    return table[c](*args)


# -- conditional aggregates --------------------------------------------------

_WILD = re.compile(r"~?[*?]|[.^$+(){}|\[\]\\]")


def _wildcard_re(pattern: str) -> re.Pattern[str]:
    def sub(m: re.Match[str]) -> str:
        t = m.group()
        if t.startswith("~"):
            return re.escape(t[1])
        return {"*": ".*", "?": "."}.get(t, "\\" + t)

    return re.compile("^" + _WILD.sub(sub, pattern) + "$", re.IGNORECASE | re.DOTALL)


def _make_criterion(crit: Any) -> Callable[[Any], bool]:
    crit = _scalar(crit)
    if isinstance(crit, str):
        m = re.match(r"(<=|>=|<>|<|>|=)(.*)", crit, re.DOTALL)
        op, operand = (m.group(1), m.group(2)) if m else ("=", crit)
    else:
        op, operand = "=", crit
    target: Any = operand
    if isinstance(operand, str):
        try:
            target = _num(operand)
        except XlError:
            if operand.upper() in ("TRUE", "FALSE"):
                target = operand.upper() == "TRUE"

    def test(v: Any) -> bool:
        if isinstance(v, XlError):
            return False
        if isinstance(target, str):
            if op in ("=", "<>"):
                if target == "":
                    hit = v is None or v == ""
                else:
                    hit = isinstance(v, str) and bool(_wildcard_re(target).match(v))
                return hit if op == "=" else not hit
            return isinstance(v, str) and _cmp_op(op, v, target)
        if v is None:
            return op == "<>"
        if isinstance(target, bool) != isinstance(v, bool) or isinstance(v, str):
            return op == "<>"
        return _cmp_op(op, v, target)

    return test


def _mask(pairs: Sequence[tuple[Any, Any]]) -> list[tuple[int, int]]:
    """Positions where every (range, criterion) pair matches."""
    first = _rows_of(pairs[0][0])
    shape = (len(first), len(first[0]))
    tests = []
    for rng, crit in pairs:
        if not _is_arr(rng):
            raise VALUE
        rows = _rows_of(rng)
        if (len(rows), len(rows[0])) != shape:
            raise VALUE
        tests.append((rows, _make_criterion(crit)))
    return [
        (i, j)
        for i in range(shape[0])
        for j in range(shape[1])
        if all(t(rows[i][j]) for rows, t in tests)
    ]


def _pairs(args: Sequence[Any]) -> list[tuple[Any, Any]]:
    if len(args) % 2:
        raise VALUE
    return [(args[i], args[i + 1]) for i in range(0, len(args), 2)]


def _values_at(rng: Any, hits: list[tuple[int, int]]) -> list[float | int]:
    rows = _rows_of(rng)
    out = []
    for i, j in hits:
        v = rows[i][j] if i < len(rows) and j < len(rows[i]) else None
        if isinstance(v, XlError):
            raise v
        if isinstance(v, int | float) and not isinstance(v, bool):
            out.append(v)
    return out


@_fn("COUNTIF")
def _countif(rng: Any, crit: Any) -> Any:
    return len(_mask([(rng, crit)]))


@_fn("COUNTIFS")
def _countifs(*args: Any) -> Any:
    return len(_mask(_pairs(args)))


@_fn("SUMIF")
def _sumif(rng: Any, crit: Any, sum_rng: Any = None) -> Any:
    hits = _mask([(rng, crit)])
    return sum(_values_at(sum_rng if sum_rng is not None else rng, hits))


@_fn("SUMIFS")
def _sumifs(sum_rng: Any, *args: Any) -> Any:
    return sum(_values_at(sum_rng, _mask(_pairs(args))))


@_fn("AVERAGEIF")
def _averageif(rng: Any, crit: Any, avg_rng: Any = None) -> Any:
    n = _values_at(avg_rng if avg_rng is not None else rng, _mask([(rng, crit)]))
    if not n:
        raise DIV0
    return sum(n) / len(n)


@_fn("AVERAGEIFS")
def _averageifs(avg_rng: Any, *args: Any) -> Any:
    n = _values_at(avg_rng, _mask(_pairs(args)))
    if not n:
        raise DIV0
    return sum(n) / len(n)


@_fn("MAXIFS")
def _maxifs(rng: Any, *args: Any) -> Any:
    n = _values_at(rng, _mask(_pairs(args)))
    return max(n) if n else 0


@_fn("MINIFS")
def _minifs(rng: Any, *args: Any) -> Any:
    n = _values_at(rng, _mask(_pairs(args)))
    return min(n) if n else 0


# -- math --------------------------------------------------------------------


def _unary_math(name: str, f: Callable[[float], float]) -> None:
    def impl(x: Any) -> Any:
        try:
            return f(_num(x))
        except ValueError:
            raise NUM from None
        except OverflowError:
            raise NUM from None

    _fn(name)(impl)


_unary_math("ABS", abs)
_unary_math("SQRT", math.sqrt)
_unary_math("EXP", math.exp)
_unary_math("LN", math.log)
_unary_math("LOG10", math.log10)
_unary_math("SIN", math.sin)
_unary_math("COS", math.cos)
_unary_math("TAN", math.tan)
_unary_math("ATAN", math.atan)
_unary_math("DEGREES", math.degrees)
_unary_math("RADIANS", math.radians)
_unary_math("SIGN", lambda x: (x > 0) - (x < 0))
_unary_math("INT", lambda x: math.floor(x))
_unary_math("FACT", lambda x: math.factorial(int(x)))


@_fn("PI")
def _pi() -> Any:
    return math.pi


@_fn("LOG")
def _log(x: Any, base: Any = 10) -> Any:
    try:
        return math.log(_num(x), _num(base))
    except (ValueError, ZeroDivisionError):
        raise NUM from None


@_fn("POWER")
def _power(a: Any, b: Any) -> Any:
    return _arith("^", a, b)


@_fn("MOD")
def _mod(a: Any, b: Any) -> Any:
    x, y = _num(a), _num(b)
    if y == 0:
        raise DIV0
    return x - y * math.floor(x / y)


@_fn("QUOTIENT")
def _quotient(a: Any, b: Any) -> Any:
    if _num(b) == 0:
        raise DIV0
    return math.trunc(_num(a) / _num(b))


@_fn("ROUND")
def _round(x: Any, digits: Any = 0) -> Any:
    return _round_half_up(float(_num(x)), _int(digits))


def _directed(x: float, digits: int, up: bool) -> float:
    q = Decimal(1).scaleb(-digits)
    d = Decimal(repr(abs(x))).quantize(q, rounding="ROUND_UP" if up else "ROUND_DOWN")
    return math.copysign(float(d), x) if d else 0.0


@_fn("ROUNDUP")
def _roundup(x: Any, digits: Any = 0) -> Any:
    return _directed(float(_num(x)), _int(digits), True)


@_fn("ROUNDDOWN", "TRUNC")
def _rounddown(x: Any, digits: Any = 0) -> Any:
    return _directed(float(_num(x)), _int(digits), False)


@_fn("CEILING")
def _ceiling(x: Any, sig: Any = 1) -> Any:
    n, s = _num(x), _num(sig)
    if s == 0:
        return 0
    return math.ceil(n / s) * s


@_fn("FLOOR")
def _floor(x: Any, sig: Any = 1) -> Any:
    n, s = _num(x), _num(sig)
    if s == 0:
        raise DIV0
    return math.floor(n / s) * s


@_fn("GCD")
def _gcd(*args: Any) -> Any:
    return math.gcd(*(int(v) for v in _nums(args)))


@_fn("LCM")
def _lcm(*args: Any) -> Any:
    return math.lcm(*(int(v) for v in _nums(args)))


@_fn("ISEVEN")
def _iseven(x: Any) -> Any:
    return _int(x) % 2 == 0


@_fn("ISODD")
def _isodd(x: Any) -> Any:
    return _int(x) % 2 == 1


# -- logical -----------------------------------------------------------------


@_fn("IF", lazy=True)
def _if(ctx: FormulaEngine, cond: Node, a: Node | None = None, b: Node | None = None) -> Any:
    chosen = a if _bool(ctx.eval(cond)) else b
    if chosen is None:
        return False
    v = ctx.eval(chosen)
    return 0 if v is None and chosen[0] == "lit" else v


@_fn("IFERROR", lazy=True)
def _iferror(ctx: FormulaEngine, value: Node, alt: Node) -> Any:
    try:
        return ctx.eval(value)
    except XlError:
        return ctx.eval(alt)


@_fn("IFNA", lazy=True)
def _ifna(ctx: FormulaEngine, value: Node, alt: Node) -> Any:
    try:
        return ctx.eval(value)
    except XlError as err:
        if err == NA:
            return ctx.eval(alt)
        raise


@_fn("IFS", lazy=True)
def _ifs(ctx: FormulaEngine, *pairs: Node) -> Any:
    if len(pairs) % 2:
        raise VALUE
    for i in range(0, len(pairs), 2):
        if _bool(ctx.eval(pairs[i])):
            return ctx.eval(pairs[i + 1])
    raise NA


@_fn("SWITCH", lazy=True)
def _switch(ctx: FormulaEngine, expr: Node, *rest: Node) -> Any:
    target = _scalar(ctx.eval(expr))
    n = len(rest)
    for i in range(0, n - 1, 2):
        if _compare(target, _scalar(ctx.eval(rest[i]))) == 0:
            return ctx.eval(rest[i + 1])
    if n % 2:
        return ctx.eval(rest[-1])
    raise NA


@_fn("CHOOSE", lazy=True)
def _choose(ctx: FormulaEngine, index: Node, *options: Node) -> Any:
    i = _int(ctx.eval(index))
    if not 1 <= i <= len(options):
        raise VALUE
    return ctx.eval(options[i - 1])


@_fn("AND")
def _and(*args: Any) -> Any:
    vals = [v for v in _cells(args) if v is not None and not isinstance(v, str)]
    for v in vals:
        if isinstance(v, XlError):
            raise v
    if not vals:
        raise VALUE
    return all(_bool(v) for v in vals)


@_fn("OR")
def _or(*args: Any) -> Any:
    vals = [v for v in _cells(args) if v is not None and not isinstance(v, str)]
    for v in vals:
        if isinstance(v, XlError):
            raise v
    if not vals:
        raise VALUE
    return any(_bool(v) for v in vals)


@_fn("XOR")
def _xor(*args: Any) -> Any:
    vals = [_bool(v) for v in _cells(args) if v is not None and not isinstance(v, str)]
    return sum(vals) % 2 == 1


@_fn("NOT")
def _not(x: Any) -> Any:
    return not _bool(x)


@_fn("TRUE")
def _true() -> Any:
    return True


@_fn("FALSE")
def _false() -> Any:
    return False


@_fn("NA")
def _na() -> Any:
    raise NA


def _probe(ctx: FormulaEngine, node: Node) -> Any:
    """Evaluate an argument for the IS* family: errors are returned, not raised."""
    try:
        return _scalar_safe(ctx.eval_arg(node))
    except XlError as err:
        return err


def _scalar_safe(v: Any) -> Any:
    try:
        return _scalar(v)
    except XlError as err:
        return err


@_fn("ISNUMBER", lazy=True)
def _isnumber(ctx: FormulaEngine, node: Node) -> Any:
    v = _probe(ctx, node)
    return isinstance(v, int | float) and not isinstance(v, bool)


@_fn("ISTEXT", lazy=True)
def _istext(ctx: FormulaEngine, node: Node) -> Any:
    return isinstance(_probe(ctx, node), str)


@_fn("ISBLANK", lazy=True)
def _isblank(ctx: FormulaEngine, node: Node) -> Any:
    return _probe(ctx, node) is None


@_fn("ISLOGICAL", lazy=True)
def _islogical(ctx: FormulaEngine, node: Node) -> Any:
    return isinstance(_probe(ctx, node), bool)


@_fn("ISERROR", lazy=True)
def _iserror(ctx: FormulaEngine, node: Node) -> Any:
    return isinstance(_probe(ctx, node), XlError)


@_fn("ISERR", lazy=True)
def _iserr(ctx: FormulaEngine, node: Node) -> Any:
    v = _probe(ctx, node)
    return isinstance(v, XlError) and v != NA


@_fn("ISNA", lazy=True)
def _isna(ctx: FormulaEngine, node: Node) -> Any:
    return _probe(ctx, node) == NA


@_fn("N")
def _n(v: Any) -> Any:
    s = _scalar(v)
    return s if isinstance(s, int | float) and not isinstance(s, bool) else int(s is True)


@_fn("T")
def _t(v: Any) -> Any:
    s = _scalar(v)
    return s if isinstance(s, str) else ""


# -- text --------------------------------------------------------------------


@_fn("LEN")
def _len(s: Any) -> Any:
    return len(_text(s))


@_fn("LEFT")
def _left(s: Any, n: Any = 1) -> Any:
    k = _int(n)
    if k < 0:
        raise VALUE
    return _text(s)[:k]


@_fn("RIGHT")
def _right(s: Any, n: Any = 1) -> Any:
    k = _int(n)
    if k < 0:
        raise VALUE
    return _text(s)[len(_text(s)) - k :] if k else ""


@_fn("MID")
def _mid(s: Any, start: Any, n: Any) -> Any:
    a, k = _int(start), _int(n)
    if a < 1 or k < 0:
        raise VALUE
    return _text(s)[a - 1 : a - 1 + k]


@_fn("UPPER")
def _upper(s: Any) -> Any:
    return _text(s).upper()


@_fn("LOWER")
def _lower(s: Any) -> Any:
    return _text(s).lower()


@_fn("PROPER")
def _proper(s: Any) -> Any:
    return re.sub(r"[^\W\d_]+", lambda m: m.group().capitalize(), _text(s))


@_fn("TRIM")
def _trim(s: Any) -> Any:
    return re.sub(r" +", " ", _text(s).strip(" "))


@_fn("EXACT")
def _exact(a: Any, b: Any) -> Any:
    return _text(a) == _text(b)


@_fn("REPT")
def _rept(s: Any, n: Any) -> Any:
    k = _int(n)
    if k < 0:
        raise VALUE
    return _text(s) * k


@_fn("CHAR")
def _char(n: Any) -> Any:
    k = _int(n)
    if not 1 <= k <= 255:
        raise VALUE
    return chr(k)


@_fn("CODE", "UNICODE")
def _code(s: Any) -> Any:
    t = _text(s)
    if not t:
        raise VALUE
    return ord(t[0])


@_fn("CONCATENATE", "CONCAT")
def _concat(*args: Any) -> Any:
    return "".join(_text(v) for v in _cells(args))


@_fn("TEXTJOIN")
def _textjoin(delim: Any, ignore_empty: Any, *args: Any) -> Any:
    parts = [_text(v) for v in _cells(args) if not (_bool(ignore_empty) and _text(v) == "")]
    return _text(delim).join(parts)


@_fn("SUBSTITUTE")
def _substitute(s: Any, old: Any, new: Any, instance: Any = None) -> Any:
    text, o, n = _text(s), _text(old), _text(new)
    if o == "":
        return text
    if instance is None:
        return text.replace(o, n)
    k = _int(instance)
    if k < 1:
        raise VALUE
    idx = -1
    for _ in range(k):
        idx = text.find(o, idx + 1)
        if idx < 0:
            return text
    return text[:idx] + n + text[idx + len(o) :]


@_fn("REPLACE")
def _replace(s: Any, start: Any, n: Any, new: Any) -> Any:
    text, a, k = _text(s), _int(start), _int(n)
    if a < 1 or k < 0:
        raise VALUE
    return text[: a - 1] + _text(new) + text[a - 1 + k :]


@_fn("FIND")
def _find(needle: Any, hay: Any, start: Any = 1) -> Any:
    a = _int(start)
    h = _text(hay)
    if a < 1 or a > len(h) + 1:
        raise VALUE
    idx = h.find(_text(needle), a - 1)
    if idx < 0:
        raise VALUE
    return idx + 1


@_fn("SEARCH")
def _search(needle: Any, hay: Any, start: Any = 1) -> Any:
    a = _int(start)
    h = _text(hay)
    if a < 1 or a > len(h) + 1:
        raise VALUE
    m = re.compile(_wildcard_re(_text(needle)).pattern[1:-1], re.IGNORECASE | re.DOTALL).search(
        h, a - 1
    )
    if not m:
        raise VALUE
    return m.start() + 1


@_fn("VALUE")
def _value(s: Any) -> Any:
    return _num(s)


@_fn("TEXT")
def _text_fn(v: Any, fmt: Any) -> Any:
    return _format_text(_scalar(v), _text(fmt))


_DATE_TOKENS = re.compile(r"yyyy|yy|mmmm|mmm|mm|m|dddd|ddd|dd|d|hh|h|ss|s|AM/PM", re.IGNORECASE)


def _format_text(v: Any, fmt: str) -> str:
    if isinstance(v, str):
        return v
    if v is None:
        v = 0
    num = float(_num(v))
    if _DATE_TOKENS.search(fmt) and not re.fullmatch(r"[#0,.%\s]*", fmt):
        return _format_date(num, fmt)
    pct = fmt.count("%")
    num *= 100**pct
    m = re.search(r"[#0,]*\.?[0#]*", fmt)
    core = m.group() if m else ""
    decimals = len(core.split(".")[1]) if "." in core else 0
    body = f"{_round_half_up(abs(num), decimals):,.{decimals}f}"
    if "," not in core.split(".")[0]:
        body = body.replace(",", "")
    min_int = core.split(".")[0].replace(",", "").count("0")
    int_part, _, frac = body.partition(".")
    digits = int_part.replace(",", "")
    if min_int == 0 and digits == "0" and decimals:
        int_part = ""
    elif len(digits) < min_int:
        int_part = digits.rjust(min_int, "0")
    out = int_part + ("." + frac if frac else "")
    sign = "-" if num < 0 and float(out.replace(",", "") or 0) != 0 else ""
    pre = fmt[: m.start()] if m else fmt
    post = fmt[m.end() :] if m else ""
    return sign + _unquote(pre) + out + _unquote(post)


def _unquote(text: str) -> str:
    return text.replace('"', "").replace("\\", "")


def _format_date(serial: float, fmt: str) -> str:
    dt = _from_serial(serial)
    tokens = list(_DATE_TOKENS.finditer(fmt))
    ampm = any(t.group().upper() == "AM/PM" for t in tokens)
    out: list[str] = []
    last = 0
    for idx, t in enumerate(tokens):
        out.append(fmt[last : t.start()])
        tok = t.group().lower()
        prev_hour = any(x.group().lower() in ("h", "hh") for x in tokens[:idx][-1:])
        next_sec = any(x.group().lower() in ("s", "ss") for x in tokens[idx + 1 : idx + 2])
        minute = tok in ("m", "mm") and (prev_hour or next_sec)
        hour12 = (dt.hour % 12) or 12
        mapping = {
            "yyyy": f"{dt.year:04d}",
            "yy": f"{dt.year % 100:02d}",
            "mmmm": dt.strftime("%B"),
            "mmm": dt.strftime("%b"),
            "dddd": dt.strftime("%A"),
            "ddd": dt.strftime("%a"),
            "dd": f"{dt.day:02d}",
            "d": str(dt.day),
            "hh": f"{hour12 if ampm else dt.hour:02d}",
            "h": str(hour12 if ampm else dt.hour),
            "ss": f"{dt.second:02d}",
            "s": str(dt.second),
            "am/pm": "AM" if dt.hour < 12 else "PM",
        }
        if minute:
            out.append(f"{dt.minute:02d}" if tok == "mm" else str(dt.minute))
        elif tok == "mm":
            out.append(f"{dt.month:02d}")
        elif tok == "m":
            out.append(str(dt.month))
        else:
            out.append(mapping[tok])
        last = t.end()
    out.append(fmt[last:])
    return "".join(out)


# -- date / time -------------------------------------------------------------


def _to_dt(serial: Any) -> datetime:
    n = _num(serial)
    if n < 0:
        raise NUM
    return _from_serial(n)


@_fn("DATE")
def _date(y: Any, m: Any, d: Any) -> Any:
    year, month, day = _int(y), _int(m), _int(d)
    if year < 0 or year > 9999:
        raise NUM
    if year < 1900:
        year += 1900
    year += (month - 1) // 12
    month = (month - 1) % 12 + 1
    base = date(year, month, 1)
    return _serial(base) + (day - 1)


@_fn("YEAR")
def _year(s: Any) -> Any:
    return _to_dt(s).year


@_fn("MONTH")
def _month(s: Any) -> Any:
    return _to_dt(s).month


@_fn("DAY")
def _day(s: Any) -> Any:
    return _to_dt(s).day


@_fn("HOUR")
def _hour(s: Any) -> Any:
    return _to_dt(s).hour


@_fn("MINUTE")
def _minute(s: Any) -> Any:
    return _to_dt(s).minute


@_fn("SECOND")
def _second(s: Any) -> Any:
    return _to_dt(s).second


@_fn("WEEKDAY")
def _weekday(s: Any, kind: Any = 1) -> Any:
    wd = _to_dt(s).weekday()  # Monday=0
    k = _int(kind)
    if k == 1:
        return (wd + 1) % 7 + 1
    if k == 2:
        return wd + 1
    if k == 3:
        return wd
    raise NUM


@_fn("DAYS")
def _days(end: Any, start: Any) -> Any:
    return math.floor(_num(end)) - math.floor(_num(start))


@_fn("TODAY")
def _today() -> Any:
    return _serial(date.today())


@_fn("NOW")
def _now() -> Any:
    return _serial(datetime.now())


# -- lookup ------------------------------------------------------------------


def _as_rows(v: Any) -> list[list[Any]]:
    return _rows_of(v) if _is_arr(v) else [[v]]


@_fn("ROW")
def _row(ref: Any = None) -> Any:
    if isinstance(ref, Range):
        return ref.r1
    raise VALUE


@_fn("COLUMN")
def _column(ref: Any = None) -> Any:
    if isinstance(ref, Range):
        return ref.c1
    raise VALUE


@_fn("ROWS")
def _rows(ref: Any) -> Any:
    return ref.shape[0] if _is_arr(ref) else 1


@_fn("COLUMNS")
def _columns(ref: Any) -> Any:
    return ref.shape[1] if _is_arr(ref) else 1


def _match_pos(target: Any, values: list[Any], mode: int) -> int | None:
    if mode == 0:
        if isinstance(target, str):
            rx = _wildcard_re(target)
            for i, v in enumerate(values):
                if isinstance(v, str) and rx.match(v):
                    return i
            return None
        for i, v in enumerate(values):
            if (
                v is not None
                and not isinstance(v, XlError)
                and _type_rank(v) == _type_rank(target)
                and _compare(v, target) == 0
            ):
                return i
        return None
    best: int | None = None
    for i, v in enumerate(values):
        if v is None or isinstance(v, XlError) or _type_rank(v) != _type_rank(target):
            continue
        c = _compare(v, target)
        if (mode == 1 and c <= 0) or (mode == -1 and c >= 0):
            best = i
        elif (mode == 1 and c > 0) or (mode == -1 and c < 0):
            break
    return best


@_fn("MATCH")
def _match(target: Any, rng: Any, mode: Any = 1) -> Any:
    rows = _as_rows(rng)
    if len(rows) > 1 and len(rows[0]) > 1:
        raise NA
    values = [v for row in rows for v in row]
    pos = _match_pos(_scalar(target), values, _int(mode))
    if pos is None:
        raise NA
    return pos + 1


@_fn("INDEX")
def _index(rng: Any, row: Any = 0, col: Any = None) -> Any:
    rows = _as_rows(rng)
    r = _int(row)
    if col is None:
        # one-dimensional ranges take the position as the index
        if len(rows) == 1 and len(rows[0]) > 1:
            r, c = 1, _int(row)
        else:
            c = 1
    else:
        c = _int(col)
    if r < 0 or c < 0 or r > len(rows) or c > len(rows[0]):
        raise REF
    if r == 0 and c == 0:
        return Array(rows)
    if r == 0:
        return Array([[row_[c - 1]] for row_ in rows])
    if c == 0:
        return Array([rows[r - 1]])
    return rows[r - 1][c - 1]


@_fn("VLOOKUP")
def _vlookup(target: Any, table: Any, col: Any, approx: Any = True) -> Any:
    rows = _as_rows(table)
    c = _int(col)
    if c < 1 or c > len(rows[0]):
        raise REF
    pos = _match_pos(_scalar(target), [r[0] for r in rows], 1 if _bool(approx) else 0)
    if pos is None:
        raise NA
    return rows[pos][c - 1]


@_fn("HLOOKUP")
def _hlookup(target: Any, table: Any, row: Any, approx: Any = True) -> Any:
    rows = _as_rows(table)
    r = _int(row)
    if r < 1 or r > len(rows):
        raise REF
    pos = _match_pos(_scalar(target), rows[0], 1 if _bool(approx) else 0)
    if pos is None:
        raise NA
    return rows[r - 1][pos]


@_fn("LOOKUP")
def _lookup(target: Any, look: Any, result: Any = None) -> Any:
    lrows = _as_rows(look)
    values = [v for row in lrows for v in row] if len(lrows) == 1 or len(lrows[0]) == 1 else None
    if values is None:
        raise NA
    pos = _match_pos(_scalar(target), values, 1)
    if pos is None:
        raise NA
    res = [v for row in _as_rows(result if result is not None else look) for v in row]
    return res[pos]


@_fn("XLOOKUP")
def _xlookup(target: Any, look: Any, ret: Any, not_found: Any = None, mode: Any = 0) -> Any:
    values = [v for row in _as_rows(look) for v in row]
    pos = _match_pos(_scalar(target), values, 0)
    if pos is None:
        if not_found is not None:
            return not_found
        raise NA
    rrows = _as_rows(ret)
    flat = [v for row in rrows for v in row]
    return flat[pos] if len(rrows) == 1 or len(rrows[0]) == 1 else Array([rrows[pos]])


@_fn("INDIRECT", lazy=True)
def _indirect(ctx: FormulaEngine, ref: Node) -> Any:
    text = _text(ctx.eval(ref)).strip()
    try:
        node = parse_formula(text)
    except FormulaSyntaxError:
        raise REF from None
    if node[0] not in ("cell", "range", "name"):
        raise REF
    return ctx.eval(node)


@_fn("TRANSPOSE")
def _transpose(rng: Any) -> Any:
    rows = _as_rows(rng)
    return Array([list(c) for c in zip(*rows, strict=True)])


# ---------------------------------------------------------------------------
# Engine
# ---------------------------------------------------------------------------


class FormulaEngine:
    """Evaluates formulas of an ``openpyxl.Workbook`` lazily with memoisation.

    Create one engine per calculation: it reads cell contents when first needed and caches
    formula results, so edit the workbook afterwards only by creating a fresh engine.
    """

    def __init__(self, wb: Workbook) -> None:
        self.wb = wb
        self._memo: dict[tuple[str, int, int], Any] = {}
        self._active: set[tuple[str, int, int]] = set()
        self._sheet: Worksheet = wb.worksheets[0]
        self.circular: list[str] = []

    # -- public -----------------------------------------------------------

    def calculate(self) -> dict[str, dict[str, Any]]:
        """Evaluate every formula cell; returns ``{sheet: {coordinate: value}}``.

        Error results are returned as their text (``"#DIV/0!"``); blank results as ``None``.
        """
        limit = sys.getrecursionlimit()
        sys.setrecursionlimit(max(limit, 30000))
        try:
            out: dict[str, dict[str, Any]] = {}
            for ws in self.wb.worksheets:
                values: dict[str, Any] = {}
                for row in ws.iter_rows():
                    for cell in row:
                        if _formula_text(cell.value) is not None:
                            values[cell.coordinate] = _export(
                                self.cell_value(ws, cell.row or 0, cell.column or 0)
                            )
                if values:
                    out[ws.title] = values
            return out
        finally:
            sys.setrecursionlimit(limit)

    def evaluate(self, formula: str, sheet: str | int | None = None) -> Any:
        """Evaluate formula text in the context of ``sheet`` (default: first sheet)."""
        ws = self._find_sheet(sheet) if sheet is not None else self.wb.worksheets[0]
        previous, self._sheet = self._sheet, ws
        try:
            node = parse_formula(formula)
            return _export(self._final(self.eval(node)))
        except XlError as err:
            return _export(err)
        finally:
            self._sheet = previous

    # -- internals --------------------------------------------------------

    def _find_sheet(self, sheet: str | int) -> Worksheet:
        if isinstance(sheet, int):
            return self.wb.worksheets[sheet]
        for ws in self.wb.worksheets:
            if ws.title.lower() == sheet.lower():
                return ws
        raise REF

    def cell_value(self, ws: Worksheet, row: int, col: int) -> Any:
        """Value of one cell, computing formulas on demand."""
        key = (ws.title, row, col)
        if key in self._memo:
            return self._memo[key]
        raw = ws._cells.get((row, col))  # noqa: SLF001 - avoid creating cells while reading
        if raw is None:
            return None
        text = _formula_text(raw.value)
        if text is None:
            return _norm_cell(raw.value)
        if key in self._active:
            self.circular.append(f"{ws.title}!{raw.coordinate}")
            return CIRC
        self._active.add(key)
        previous, self._sheet = self._sheet, ws
        try:
            try:
                result = self._final(self.eval(parse_formula(text)))
            except XlError as err:
                result = err
            except FormulaSyntaxError:
                result = NAME
            except RecursionError:
                result = REF
        finally:
            self._sheet = previous
            self._active.discard(key)
        self._memo[key] = result
        return result

    @staticmethod
    def _final(v: Any) -> Any:
        if isinstance(v, Range | Array):
            return _scalar(v)
        return v

    def eval(self, node: Node) -> Any:
        """Evaluate an AST node (may return a ``Range``/``Array``; errors are raised)."""
        kind = node[0]
        if kind == "lit":
            v = node[1]
            if isinstance(v, XlError):
                raise v
            return v
        if kind == "cell":
            ws = self._ws(node[1])
            v = self.cell_value(ws, node[2], node[3])
            if isinstance(v, XlError):
                raise v
            return v
        if kind == "range":
            return self._range(node)
        if kind == "un":
            x = self.eval(node[2])
            if node[1] == "+":
                return x
            return _broadcast(x, 0, lambda a, _b: -_num(a))
        if kind == "pct":
            return _broadcast(self.eval(node[1]), 0, lambda a, _b: _num(a) / 100)
        if kind == "bin":
            return _binary(node[1], self.eval(node[2]), self.eval(node[3]))
        if kind == "call":
            return self._call(node[1], node[2])
        if kind == "array":
            return Array([list(r) for r in node[1]])
        if kind == "name":
            return self._named(node[1])
        raise FormulaSyntaxError(f"Unknown node {kind!r}")

    def eval_arg(self, node: Node) -> Any:
        """Evaluate a function argument; a single-cell reference stays a 1x1 ``Range``.

        Keeping the reference lets functions tell cell references (whose text/boolean
        content SUM and friends ignore) from literal arguments (which are coerced).
        """
        if node[0] == "cell":
            r, c = node[2], node[3]
            return Range(self, self._ws(node[1]), r, c, r, c)
        return self.eval(node)

    def _ws(self, name: str | None) -> Worksheet:
        return self._sheet if name is None else self._find_sheet(name)

    def _range(self, node: Node) -> Range:
        _, sheet, r1, c1, r2, c2 = node
        ws = self._ws(sheet)
        if r2 is None:  # whole columns
            r1, r2 = 1, max(ws.max_row, 1)
        if c2 is None:  # whole rows
            c1, c2 = 1, max(ws.max_column, 1)
        return Range(self, ws, r1, c1, r2, c2)

    def _named(self, name: str) -> Any:
        defined: Any = self._sheet.defined_names.get(name) or self.wb.defined_names.get(name)
        if defined is None or not defined.attr_text:
            raise NAME
        try:
            return self.eval(parse_formula(defined.attr_text))
        except FormulaSyntaxError:
            raise NAME from None

    def _call(self, name: str, arg_nodes: list[Node]) -> Any:
        entry = _FUNCS.get(name)
        if entry is None:
            raise NAME
        fn, lazy = entry
        if lazy:
            return self._invoke(fn, self, *arg_nodes)
        return self._invoke(fn, *(self.eval_arg(n) for n in arg_nodes))

    @staticmethod
    def _invoke(fn: Callable[..., Any], *args: Any) -> Any:
        try:
            return fn(*args)
        except TypeError as exc:
            if "positional argument" in str(exc) or "required" in str(exc):
                raise NAME from None  # wrong argument count
            raise
        except (OverflowError, ZeroDivisionError):
            raise NUM from None


def _formula_text(value: Any) -> str | None:
    if isinstance(value, str) and value.startswith("=") and len(value) > 1:
        return value
    text = getattr(value, "text", None)  # ArrayFormula
    if isinstance(text, str) and text:
        return text
    return None


def _export(v: Any) -> Any:
    if isinstance(v, XlError):
        return v.code
    if isinstance(v, float) and v == int(v) and abs(v) < 1e15:
        return int(v)
    return v


def evaluate_workbook(wb: Workbook) -> dict[str, dict[str, Any]]:
    """Calculate all formula cells of ``wb``; returns ``{sheet: {coordinate: value}}``."""
    return FormulaEngine(wb).calculate()


def evaluate_formula(wb: Workbook, formula: str, sheet: str | int | None = None) -> Any:
    """Evaluate one formula (e.g. ``"=SUM(A1:A3)*2"``) against ``wb``."""
    return FormulaEngine(wb).evaluate(formula, sheet)
