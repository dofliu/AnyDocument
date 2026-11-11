"""Tests for the ``anydoc csv`` commands (T080)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from anydoc.cli import app
from anydoc.formats.csv import CsvHandler

runner = CliRunner()

PEOPLE = "姓名,部門,分數\n王小明,研發部,90\n陳美玲,行銷部,85\n林志豪,財務部,78\n王小明,研發部,90\n"
DEPTS = "部門,主管\n研發部,張經理\n人資部,李經理\n"


@pytest.fixture
def people(tmp_path: Path) -> Path:
    path = tmp_path / "people.csv"
    path.write_text(PEOPLE, encoding="utf-8")
    return path


@pytest.fixture
def depts(tmp_path: Path) -> Path:
    path = tmp_path / "depts.csv"
    path.write_text(DEPTS, encoding="utf-8")
    return path


def test_filter_to_stdout(people: Path) -> None:
    result = runner.invoke(app, ["csv", "filter", str(people), "-w", "分數>=85", "-w", "部門~研發"])
    assert result.exit_code == 0, result.output
    assert result.stdout == "姓名,部門,分數\n王小明,研發部,90\n王小明,研發部,90\n"
    assert "kept 2 rows, removed 2" in result.stderr


def test_filter_any_invert_and_output_file(people: Path, tmp_path: Path) -> None:
    out = tmp_path / "out.csv"
    args = ["csv", "filter", str(people), "-w", "分數<80", "-w", "部門=行銷部", "--any"]
    result = runner.invoke(app, [*args, "--invert", "-o", str(out)])
    assert result.exit_code == 0, result.output
    assert result.stdout.strip() == str(out)
    assert out.read_text(encoding="utf-8") == "姓名,部門,分數\n王小明,研發部,90\n王小明,研發部,90\n"


def test_sort(people: Path) -> None:
    result = runner.invoke(app, ["csv", "sort", str(people), "-k", "分數:desc", "-k", "姓名"])
    assert result.exit_code == 0, result.output
    assert result.stdout.splitlines()[1:] == [
        "王小明,研發部,90",
        "王小明,研發部,90",
        "陳美玲,行銷部,85",
        "林志豪,財務部,78",
    ]
    result = runner.invoke(
        app, ["csv", "sort", str(people), "-k", "分數", "--desc", "--ignore-case"]
    )
    assert result.stdout.splitlines()[-1] == "林志豪,財務部,78"


def test_dedupe(people: Path) -> None:
    result = runner.invoke(app, ["csv", "dedupe", str(people)])
    assert result.exit_code == 0, result.output
    assert len(result.stdout.splitlines()) == 4
    assert "removed 1" in result.stderr
    result = runner.invoke(
        app, ["csv", "dedupe", str(people), "-c", "部門", "--keep", "last", "--ignore-case"]
    )
    assert result.stdout.splitlines()[1:] == [
        "陳美玲,行銷部,85",
        "林志豪,財務部,78",
        "王小明,研發部,90",
    ]
    result = runner.invoke(app, ["csv", "dedupe", str(people), "--keep", "middle"])
    assert result.exit_code == 1
    assert "keep must be" in result.stderr


def test_select_drop_rename(people: Path) -> None:
    result = runner.invoke(
        app, ["csv", "select", str(people), "-c", "分數", "-c", "姓名", "-r", "分數=score"]
    )
    assert result.exit_code == 0, result.output
    assert result.stdout.splitlines()[:2] == ["score,姓名", "90,王小明"]
    result = runner.invoke(app, ["csv", "select", str(people), "-d", "部門"])
    assert result.stdout.splitlines()[0] == "姓名,分數"
    result = runner.invoke(app, ["csv", "select", str(people)])
    assert result.exit_code == 2
    result = runner.invoke(app, ["csv", "select", str(people), "-r", "分數"])
    assert result.exit_code == 1
    assert "OLD=NEW" in result.stderr


def test_join(people: Path, depts: Path, tmp_path: Path) -> None:
    result = runner.invoke(app, ["csv", "join", str(people), str(depts), "--on", "部門"])
    assert result.exit_code == 0, result.output
    assert result.stdout.splitlines() == [
        "姓名,部門,分數,主管",
        "王小明,研發部,90,張經理",
        "王小明,研發部,90,張經理",
    ]
    out = tmp_path / "joined.tsv"
    result = runner.invoke(
        app,
        ["csv", "join", str(people), str(depts), "--on", "部門", "--right-on", "部門"]
        + ["--how", "outer", "-o", str(out)],
    )
    assert result.exit_code == 0, result.output
    lines = out.read_text(encoding="utf-8").splitlines()
    assert lines[0] == "姓名\t部門\t分數\t主管"
    assert lines[-1] == "\t人資部\t\t李經理"
    result = runner.invoke(
        app, ["csv", "join", str(people), str(depts), "--on", "部門", "--how", "x"]
    )
    assert result.exit_code == 1


def test_group(people: Path) -> None:
    result = runner.invoke(
        app,
        ["csv", "group", str(people), "-b", "部門", "-a", "count", "-a", "平均=mean:分數"],
    )
    assert result.exit_code == 0, result.output
    assert result.stdout.splitlines() == [
        "部門,count,平均",
        "研發部,2,90",
        "行銷部,1,85",
        "財務部,1,78",
    ]
    result = runner.invoke(app, ["csv", "group", str(people), "-a", "sum:分數", "--no-sort"])
    assert result.stdout == "sum_分數\n343\n"
    result = runner.invoke(app, ["csv", "group", str(people), "-a", "mode:分數"])
    assert result.exit_code == 1
    assert "Unknown aggregate" in result.stderr


def test_stats(people: Path) -> None:
    result = runner.invoke(app, ["csv", "stats", str(people), "-c", "分數"])
    assert result.exit_code == 0, result.output
    data = json.loads(result.stdout)
    assert data == [
        {
            "name": "分數",
            "type": "int",
            "count": 4,
            "empty": 0,
            "unique": 3,
            "min": 78,
            "max": 90,
            "sum": 343,
            "mean": 85.75,
            "median": 87.5,
        }
    ]
    assert len(json.loads(runner.invoke(app, ["csv", "stats", str(people)]).stdout)) == 3
    assert runner.invoke(app, ["csv", "stats", str(people), "-c", "x"]).exit_code == 1


def test_errors_missing_file_and_bad_column(people: Path, tmp_path: Path) -> None:
    result = runner.invoke(app, ["csv", "sort", str(tmp_path / "nope.csv"), "-k", "a"])
    assert result.exit_code == 2
    assert "file not found" in result.stderr
    result = runner.invoke(app, ["csv", "sort", str(people), "-k", "薪資"])
    assert result.exit_code == 1
    assert "No such column" in result.stderr
    result = runner.invoke(app, ["csv", "filter", str(people), "-w", "分數"])
    assert result.exit_code == 1
    bad = tmp_path / "bad.csv"
    bad.write_bytes(b"\xff\xfe\x00\xd8")
    assert runner.invoke(app, ["csv", "stats", str(bad)]).exit_code == 1


def test_big5_input_keeps_encoding_and_encoding_override(tmp_path: Path) -> None:
    src = tmp_path / "big5.csv"
    src.write_bytes(PEOPLE.encode("big5"))
    out = tmp_path / "out.csv"
    result = runner.invoke(app, ["csv", "sort", str(src), "-k", "分數", "-o", str(out)])
    assert result.exit_code == 0, result.output
    assert out.read_bytes().decode("big5").splitlines()[1] == "林志豪,財務部,78"
    assert CsvHandler().open(out).encoding == "big5"
    utf = tmp_path / "utf.csv"
    args = ["csv", "sort", str(src), "-k", "分數", "-o", str(utf)]
    assert runner.invoke(app, [*args, "--encoding", "utf-8"]).exit_code == 0
    assert utf.read_text(encoding="utf-8").startswith("姓名,部門,分數\n")
    result = runner.invoke(app, [*args, "--encoding", "no-such-codec"])
    assert result.exit_code == 1


def test_tsv_input_to_csv_output(tmp_path: Path) -> None:
    src = tmp_path / "t.tsv"
    src.write_text("name\tqty\n甲\t3\n乙\t1\n", encoding="utf-8")
    out = tmp_path / "t.csv"
    result = runner.invoke(app, ["csv", "sort", str(src), "-k", "qty", "-o", str(out)])
    assert result.exit_code == 0, result.output
    assert out.read_text(encoding="utf-8") == "name,qty\n乙,1\n甲,3\n"
    result = runner.invoke(app, ["csv", "sort", str(src), "-k", "qty"])
    assert result.stdout == "name\tqty\n乙\t1\n甲\t3\n"
