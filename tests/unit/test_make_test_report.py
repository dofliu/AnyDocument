"""Tests for scripts/make_test_report.py (T005)."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "make_test_report.py"

JUNIT = """<?xml version="1.0"?>
<testsuites><testsuite name="pytest" tests="4">
<testcase classname="tests.unit.test_a" name="test_ok" time="0.5"/>
<testcase classname="tests.unit.test_a" name="test_skip" time="0.1">
<skipped message="requires tesseract"/></testcase>
<testcase classname="tests.unit.test_b" name="test_bad" time="0.2">
<failure message="assert 1 == 2"/></testcase>
<testcase classname="tests.unit.test_b" name="test_err" time="0.2"><error message="boom"/>
</testcase>
</testsuite></testsuites>
"""


@pytest.fixture(scope="module")
def mod() -> ModuleType:
    spec = importlib.util.spec_from_file_location("make_test_report", SCRIPT)
    assert spec and spec.loader
    m = importlib.util.module_from_spec(spec)
    sys.modules["make_test_report"] = m
    spec.loader.exec_module(m)
    return m


@pytest.fixture
def junit_file(tmp_path: Path) -> Path:
    p = tmp_path / "junit.xml"
    p.write_text(JUNIT, encoding="utf-8")
    return p


def test_parse_junit(mod: ModuleType, junit_file: Path) -> None:
    s = mod.parse_junit(junit_file)
    assert (s.passed, s.skipped, s.failed) == (1, 1, 2)
    assert s.skip_reasons == [("tests.unit.test_a::test_skip", "requires tesseract")]
    assert round(s.time, 1) == 1.0


def test_parse_coverage(mod: ModuleType, tmp_path: Path) -> None:
    cov = {
        "totals": {"percent_covered": 90.0},
        "files": {
            "src/anydoc/a.py": {"summary": {"num_statements": 10, "covered_lines": 5}},
            "src/anydoc/b.py": {"summary": {"num_statements": 10, "covered_lines": 10}},
        },
    }
    p = tmp_path / "c.json"
    p.write_text(json.dumps(cov), encoding="utf-8")
    assert mod.parse_coverage(p) == (90.0, None)
    assert mod.parse_coverage(p, "anydoc/a.py") == (90.0, 50.0)
    assert mod.parse_coverage(p, "nothing") == (90.0, None)


def test_select_cases(mod: ModuleType, junit_file: Path) -> None:
    cases = mod.parse_junit(junit_file).cases
    assert len(mod.select_cases(cases, ["tests/unit/test_a.py"])) == 2
    assert len(mod.select_cases(cases, ["test_bad"])) == 1


def test_render_report_sections(mod: ModuleType, junit_file: Path) -> None:
    s = mod.parse_junit(junit_file)
    text = mod.render_report(
        task="T999",
        title="示範",
        date="2026-01-02",
        commit="abc123",
        env="env",
        ruff_ok=True,
        mypy_ok=False,
        junit=s,
        coverage=88.0,
        module_coverage=92.1,
        new_cases=s.cases[:1],
        criteria=["條件一"],
        notes=[],
    )
    assert text.startswith("# 測試報告 — T999 示範")
    for heading in (
        "## 摘要",
        "## 本次新增/修改的測試",
        "## 驗收條件檢查",
        "## 跳過的測試與原因",
        "## 已知問題 / 後續建議",
        "## 失敗的測試",
    ):
        assert heading in text
    assert "1 passed, 1 skipped, 2 failed" in text
    assert "88.0%（本次新增模組：92.1%）" in text
    assert "mypy | ❌" in text
    assert "- [ ] 條件一" in text  # failures present -> not checked
    assert "requires tesseract" in text


def test_update_index_prepends_and_replaces(
    mod: ModuleType, junit_file: Path, tmp_path: Path
) -> None:
    s = mod.parse_junit(junit_file)
    idx = tmp_path / "INDEX.md"
    kw = dict(date="2026-01-02", task="T1", title="x", ok=True, junit=s, coverage=90.0)
    mod.update_index(idx, report_name="2026-01-02_T1.md", **kw)
    mod.update_index(idx, report_name="2026-01-03_T2.md", **{**kw, "task": "T2"})
    mod.update_index(idx, report_name="2026-01-02_T1.md", **kw)  # replace, no duplicate
    rows = [ln for ln in idx.read_text(encoding="utf-8").splitlines() if ln.startswith("| 2026")]
    assert len(rows) == 2
    assert "T2" in rows[0] or "T1" in rows[0]
    assert idx.read_text(encoding="utf-8").count("2026-01-02_T1.md") == 1


def test_main_writes_report(
    mod: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, junit_file: Path
) -> None:
    def fake_run(cmd: list[str]) -> bool:
        for a in cmd:
            if a.startswith("--junitxml="):
                Path(a.split("=", 1)[1]).write_text(JUNIT, encoding="utf-8")
            if a.startswith("--cov-report=json:"):
                Path(a.split(":", 1)[1]).write_text(
                    json.dumps({"totals": {"percent_covered": 77.0}, "files": {}}), encoding="utf-8"
                )
        return True

    monkeypatch.setattr(mod, "_run", fake_run)
    rc = mod.main(
        [
            "--task",
            "T999",
            "--title",
            "示範",
            "--date",
            "2026-01-02",
            "--report-dir",
            str(tmp_path),
            "--criterion",
            "c1",
            "--new-tests",
            "test_a",
        ]
    )
    assert rc == 1  # junit contains failures
    report = (tmp_path / "2026-01-02_T999.md").read_text(encoding="utf-8")
    assert "77.0%" in report and "c1" in report
    assert "T999 示範 | ❌ 失敗" in (tmp_path / "INDEX.md").read_text(encoding="utf-8")
