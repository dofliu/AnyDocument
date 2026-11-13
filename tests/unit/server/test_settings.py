"""Tests for the API server settings (T100)."""

from __future__ import annotations

from pathlib import Path

import pytest

from anydoc.server.settings import (
    DEFAULT_CLEANUP_INTERVAL,
    DEFAULT_MAX_UPLOAD_BYTES,
    DEFAULT_SESSION_TTL,
    ServerSettings,
)


def test_defaults_match_architecture() -> None:
    settings = ServerSettings()
    assert settings.storage_dir is None
    assert settings.max_upload_bytes == DEFAULT_MAX_UPLOAD_BYTES == 200 * 1024 * 1024
    assert settings.session_ttl == DEFAULT_SESSION_TTL == 3600.0
    assert settings.cleanup_interval == DEFAULT_CLEANUP_INTERVAL


@pytest.mark.parametrize(
    "kwargs",
    [{"max_upload_bytes": 0}, {"session_ttl": 0}, {"session_ttl": -1}, {"cleanup_interval": -1}],
)
def test_invalid_values_rejected(kwargs: dict[str, float]) -> None:
    with pytest.raises(ValueError):
        ServerSettings(**kwargs)  # type: ignore[arg-type]


def test_from_env_reads_variables(tmp_path: Path) -> None:
    settings = ServerSettings.from_env(
        {
            "ANYDOC_STORAGE_DIR": str(tmp_path / "文件"),
            "ANYDOC_MAX_UPLOAD_MB": "1.5",
            "ANYDOC_SESSION_TTL": "90",
            "ANYDOC_CLEANUP_INTERVAL": "0",
        }
    )
    assert settings.storage_dir == tmp_path / "文件"
    assert settings.max_upload_bytes == int(1.5 * 1024 * 1024)
    assert settings.session_ttl == 90.0
    assert settings.cleanup_interval == 0.0


def test_from_env_blank_keeps_defaults() -> None:
    assert ServerSettings.from_env({"ANYDOC_MAX_UPLOAD_MB": " ", "OTHER": "x"}) == ServerSettings()


def test_from_env_uses_process_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANYDOC_SESSION_TTL", "12")
    monkeypatch.delenv("ANYDOC_STORAGE_DIR", raising=False)
    monkeypatch.delenv("ANYDOC_MAX_UPLOAD_MB", raising=False)
    monkeypatch.delenv("ANYDOC_CLEANUP_INTERVAL", raising=False)
    assert ServerSettings.from_env().session_ttl == 12.0


@pytest.mark.parametrize(
    ("name", "value"),
    [("ANYDOC_MAX_UPLOAD_MB", "big"), ("ANYDOC_SESSION_TTL", "0"), ("ANYDOC_MAX_UPLOAD_MB", "0")],
)
def test_from_env_invalid(name: str, value: str) -> None:
    with pytest.raises(ValueError):
        ServerSettings.from_env({name: value})
