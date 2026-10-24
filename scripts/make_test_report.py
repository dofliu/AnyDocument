"""Generate a test report (docs/04-TESTING.md section 4) and update the report index.

Runs ruff, mypy and pytest (junit XML + coverage JSON), then writes
``docs/test-reports/<YYYY-MM-DD>_<TaskID>.md`` and prepends a row to ``INDEX.md``.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import platform
import shutil
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REPORT_DIR = ROOT / "docs" / "test-reports"
INDEX_HEADER = (
    "# 測試報告索引\n\n"
    "| 日期 | 工作 | 結果 | 測試數 (pass/skip/fail) | 覆蓋率 | 報告 |\n"
    "|------|------|------|------|------|------|\n"
)


@dataclass
class TestCase:
    """One pytest test case parsed from junit XML."""

    __test__ = False  # not a pytest test class

    name: str
    classname: str
    status: str  # passed | skipped | failed
    message: str = ""


@dataclass
class JunitSummary:
    """Aggregated junit results."""

    passed: int = 0
    skipped: int = 0
    failed: int = 0
    time: float = 0.0
    cases: list[TestCase] = field(default_factory=list)
    skip_reasons: list[tuple[str, str]] = field(default_factory=list)


def parse_junit(path: Path) -> JunitSummary:
    """Parse a pytest junit XML file into a ``JunitSummary``."""
    summary = JunitSummary()
    root = ET.parse(path).getroot()
    for tc in root.iter("testcase"):
        name = tc.get("name", "")
        classname = tc.get("classname", "")
        summary.time += float(tc.get("time", "0") or 0)
        failure = tc.find("failure")
        error = tc.find("error")
        skipped = tc.find("skipped")
        if failure is not None or error is not None:
            node = failure if failure is not None else error
            assert node is not None
            summary.failed += 1
            summary.cases.append(TestCase(name, classname, "failed", node.get("message", "")))
        elif skipped is not None:
            summary.skipped += 1
            msg = skipped.get("message", "")
            summary.cases.append(TestCase(name, classname, "skipped", msg))
            summary.skip_reasons.append((f"{classname}::{name}", msg))
        else:
            summary.passed += 1
            summary.cases.append(TestCase(name, classname, "passed"))
    return summary


def parse_coverage(path: Path, module_prefix: str | None = None) -> tuple[float, float | None]:
    """Return (total %, % for files whose path contains ``module_prefix``) from coverage JSON."""
    data = json.loads(path.read_text(encoding="utf-8"))
    total = float(data["totals"]["percent_covered"])
    if not module_prefix:
        return total, None
    stmts = covered = 0
    for fname, info in data.get("files", {}).items():
        if module_prefix in fname.replace("\\", "/"):
            s = info["summary"]
            stmts += int(s["num_statements"])
            covered += int(s["covered_lines"])
    return total, (100.0 * covered / stmts if stmts else None)


def select_cases(cases: list[TestCase], patterns: list[str]) -> list[TestCase]:
    """Keep cases whose dotted classname or name contains any pattern (path or dotted form)."""
    pats = [p.replace("/", ".").removesuffix(".py") for p in patterns]
    return [c for c in cases if any(p in f"{c.classname}.{c.name}" for p in pats)]


def _mark(ok: bool) -> str:
    return "✅" if ok else "❌"


def render_report(
    *,
    task: str,
    title: str,
    date: str,
    commit: str,
    env: str,
    ruff_ok: bool,
    mypy_ok: bool,
    junit: JunitSummary,
    coverage: float | None,
    module_coverage: float | None,
    new_cases: list[TestCase],
    criteria: list[str],
    notes: list[str],
) -> str:
    """Render the Markdown report."""
    pytest_ok = junit.failed == 0
    cov = "—"
    if coverage is not None:
        cov = f"{coverage:.1f}%"
        if module_coverage is not None:
            cov += f"（本次新增模組：{module_coverage:.1f}%）"
    lines = [
        f"# 測試報告 — {task} {title}",
        "",
        f"- 日期：{date}",
        f"- Commit：{commit}（報告產生時的 HEAD；完成 commit 見 PROGRESS.md）",
        f"- 環境：{env}",
        "",
        "## 摘要",
        "| 項目 | 結果 |",
        "|------|------|",
        f"| ruff | {_mark(ruff_ok)} {'通過' if ruff_ok else '失敗'} |",
        f"| mypy | {_mark(mypy_ok)} {'通過' if mypy_ok else '失敗'} |",
        f"| pytest | {_mark(pytest_ok)} {junit.passed} passed, {junit.skipped} skipped, "
        f"{junit.failed} failed（{junit.time:.1f}s） |",
        f"| 覆蓋率 | {cov} |",
        "",
        "## 本次新增/修改的測試",
        "| 測試 | 驗證內容 | 結果 |",
        "|------|------|------|",
    ]
    if new_cases:
        for c in new_cases:
            lines.append(f"| {c.classname}::{c.name} | — | {_mark(c.status != 'failed')} |")
    else:
        lines.append("| （未指定） | — | — |")
    lines += ["", "## 驗收條件檢查"]
    lines += [f"- [x] {c}" if pytest_ok else f"- [ ] {c}" for c in criteria] or ["- （未指定）"]
    lines += ["", "## 跳過的測試與原因"]
    if junit.skip_reasons:
        lines += [f"- `{n}` — {r}" for n, r in junit.skip_reasons]
    else:
        lines.append("無。")
    failures = [c for c in junit.cases if c.status == "failed"]
    if failures:
        lines += ["", "## 失敗的測試"]
        lines += [f"- `{c.classname}::{c.name}` — {c.message}" for c in failures]
    lines += ["", "## 已知問題 / 後續建議"]
    lines += [f"- {n}" for n in notes] or ["無。"]
    return "\n".join(lines) + "\n"


def update_index(
    index: Path,
    *,
    date: str,
    task: str,
    title: str,
    ok: bool,
    junit: JunitSummary,
    coverage: float | None,
    report_name: str,
) -> None:
    """Prepend (or replace) the row for this report in INDEX.md."""
    row = (
        f"| {date} | {task} {title} | {'✅ 完成' if ok else '❌ 失敗'} | "
        f"{junit.passed}/{junit.skipped}/{junit.failed} | "
        f"{'—' if coverage is None else f'{coverage:.0f}%'} | [報告]({report_name}) |"
    )
    text = index.read_text(encoding="utf-8") if index.exists() else INDEX_HEADER
    lines = text.rstrip("\n").split("\n")
    lines = [ln for ln in lines if f"]({report_name})" not in ln]
    sep = next((i for i, ln in enumerate(lines) if ln.startswith("|---")), None)
    if sep is None:
        lines = INDEX_HEADER.rstrip("\n").split("\n")
        sep = len(lines) - 1
    lines.insert(sep + 1, row)
    index.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _run(cmd: list[str]) -> bool:
    return subprocess.run(cmd, cwd=ROOT, check=False).returncode == 0


def _tool_status() -> str:
    def mark(*names: str) -> str:
        return "✅" if any(shutil.which(n) for n in names) else "❌"

    return (
        f"Python {platform.python_version()} / {platform.system()} / 外部工具：LibreOffice "
        f"{mark('soffice', 'libreoffice')} Tesseract {mark('tesseract')} "
        f"ODA {mark('ODAFileConverter')}"
    )


def _git_head() -> str:
    res = subprocess.run(
        ["git", "rev-parse", "--short", "HEAD"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    return res.stdout.strip() or "unknown"


def main(argv: list[str] | None = None) -> int:
    """CLI entry point. Returns 0 when all checks pass, 1 otherwise (report is still written)."""
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--task", required=True, help="Task ID, e.g. T005")
    p.add_argument("--title", required=True, help="Task title")
    p.add_argument("--date", default=dt.date.today().isoformat())
    p.add_argument("--new-tests", action="append", default=[], help="test file/name pattern")
    p.add_argument("--module", default=None, help="path fragment for new-module coverage")
    p.add_argument("--criterion", action="append", default=[], help="acceptance criterion line")
    p.add_argument("--note", action="append", default=[], help="known issue / suggestion line")
    p.add_argument("--report-dir", type=Path, default=REPORT_DIR)
    args = p.parse_args(argv)

    py = sys.executable
    ruff_ok = _run([py, "-m", "ruff", "check", "src", "tests", "scripts"]) and _run(
        [py, "-m", "ruff", "format", "--check", "src", "tests", "scripts"]
    )
    mypy_ok = _run([py, "-m", "mypy", "src"])
    with tempfile.TemporaryDirectory() as tmp:
        junit_path = Path(tmp) / "junit.xml"
        cov_path = Path(tmp) / "cov.json"
        _run(
            [
                py,
                "-m",
                "pytest",
                "-q",
                f"--junitxml={junit_path}",
                "--cov=anydoc",
                f"--cov-report=json:{cov_path}",
                "--cov-report=term",
            ]
        )
        if not junit_path.exists():
            print("pytest produced no junit output", file=sys.stderr)
            return 1
        junit = parse_junit(junit_path)
        coverage, module_cov = (
            parse_coverage(cov_path, args.module) if cov_path.exists() else (None, None)
        )

    report = render_report(
        task=args.task,
        title=args.title,
        date=args.date,
        commit=_git_head(),
        env=_tool_status(),
        ruff_ok=ruff_ok,
        mypy_ok=mypy_ok,
        junit=junit,
        coverage=coverage,
        module_coverage=module_cov,
        new_cases=select_cases(junit.cases, args.new_tests),
        criteria=args.criterion,
        notes=args.note,
    )
    args.report_dir.mkdir(parents=True, exist_ok=True)
    name = f"{args.date}_{args.task}.md"
    (args.report_dir / name).write_text(report, encoding="utf-8")
    ok = ruff_ok and mypy_ok and junit.failed == 0
    update_index(
        args.report_dir / "INDEX.md",
        date=args.date,
        task=args.task,
        title=args.title,
        ok=ok,
        junit=junit,
        coverage=coverage,
        report_name=name,
    )
    print(f"wrote {args.report_dir / name}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
