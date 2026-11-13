"""Tests for ``anydoc serve`` (T100); uvicorn is replaced so no socket is opened."""

from __future__ import annotations

import builtins
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from anydoc.cli import app

runner = CliRunner()


@pytest.fixture
def served(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    import uvicorn

    calls: list[dict[str, Any]] = []

    def fake_run(application: Any, **kwargs: Any) -> None:
        calls.append({"app": application, **kwargs})

    monkeypatch.setattr(uvicorn, "run", fake_run)
    for name in ("ANYDOC_STORAGE_DIR", "ANYDOC_MAX_UPLOAD_MB", "ANYDOC_SESSION_TTL"):
        monkeypatch.delenv(name, raising=False)
    return calls


def test_serve_defaults(served: list[dict[str, Any]]) -> None:
    result = runner.invoke(app, ["serve"])
    assert result.exit_code == 0, result.output
    (call,) = served
    assert call["host"] == "127.0.0.1"
    assert call["port"] == 8000
    settings = call["app"].state.settings
    assert settings.max_upload_bytes == 200 * 1024 * 1024
    assert settings.session_ttl == 3600
    call["app"].state.store.close()


def test_serve_options(served: list[dict[str, Any]], tmp_path: Path) -> None:
    result = runner.invoke(
        app,
        [
            "serve",
            "--host",
            "0.0.0.0",
            "-p",
            "9001",
            "--storage-dir",
            str(tmp_path / "s"),
            "--max-upload-mb",
            "0.5",
            "--session-ttl",
            "30",
        ],
    )
    assert result.exit_code == 0, result.output
    (call,) = served
    assert (call["host"], call["port"]) == ("0.0.0.0", 9001)
    store = call["app"].state.store
    assert store.root == tmp_path / "s"
    assert store.max_bytes == 512 * 1024
    assert store.ttl == 30


def test_serve_reads_environment(
    served: list[dict[str, Any]], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ANYDOC_STORAGE_DIR", str(tmp_path / "env"))
    monkeypatch.setenv("ANYDOC_SESSION_TTL", "77")
    result = runner.invoke(app, ["serve", "--max-upload-mb", "2"])
    assert result.exit_code == 0, result.output
    store = served[0]["app"].state.store
    assert (store.root, store.ttl, store.max_bytes) == (tmp_path / "env", 77, 2 * 1024 * 1024)


@pytest.mark.parametrize(
    "args", [["--max-upload-mb", "0"], ["--session-ttl", "-5"], ["--port", "70000"]]
)
def test_serve_invalid_options(served: list[dict[str, Any]], args: list[str]) -> None:
    result = runner.invoke(app, ["serve", *args])
    assert result.exit_code != 0
    assert served == []


def test_serve_invalid_environment(
    served: list[dict[str, Any]], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ANYDOC_SESSION_TTL", "soon")
    result = runner.invoke(app, ["serve"])
    assert result.exit_code == 1
    assert "ANYDOC_SESSION_TTL" in result.output
    assert served == []


def test_serve_without_server_extra(monkeypatch: pytest.MonkeyPatch) -> None:
    real_import = builtins.__import__

    def fake_import(name: str, *args: Any, **kwargs: Any) -> Any:
        if name == "uvicorn":
            raise ImportError("No module named 'uvicorn'", name="uvicorn")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    result = runner.invoke(app, ["serve"])
    assert result.exit_code == 1
    assert "anydoc[server]" in result.output
    assert "uvicorn" in result.output
