"""Fixtures for the API tests (FastAPI TestClient)."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from anydoc.server import ServerSettings, SessionStore, create_app

MAX_UPLOAD = 256 * 1024
TTL = 100.0


class Clock:
    """Manually advanced clock."""

    def __init__(self, now: float = 1_700_000_000.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def store(tmp_path: Path, clock: Clock) -> SessionStore:
    return SessionStore(tmp_path / "sessions", ttl=TTL, max_bytes=MAX_UPLOAD, clock=clock)


@pytest.fixture
def client(store: SessionStore) -> Iterator[TestClient]:
    settings = ServerSettings(max_upload_bytes=MAX_UPLOAD, session_ttl=TTL, cleanup_interval=0)
    with TestClient(create_app(settings, store=store)) as test_client:
        yield test_client
