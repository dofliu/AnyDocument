"""AnyDocument API service (FastAPI).

Requires the ``server`` extra: ``pip install "anydoc[server]"``. Run it with
``anydoc serve`` or ``uvicorn --factory anydoc.server:create_app``.
"""

from __future__ import annotations

from anydoc.server.app import create_app
from anydoc.server.settings import ServerSettings
from anydoc.server.store import (
    DocumentRecord,
    SessionNotFoundError,
    SessionStore,
    sanitize_filename,
)

__all__ = [
    "DocumentRecord",
    "ServerSettings",
    "SessionNotFoundError",
    "SessionStore",
    "create_app",
    "sanitize_filename",
]
