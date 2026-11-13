"""Configuration of the AnyDocument API server."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

DEFAULT_MAX_UPLOAD_BYTES = 200 * 1024 * 1024
DEFAULT_SESSION_TTL = 3600.0
DEFAULT_CLEANUP_INTERVAL = 60.0

ENV_STORAGE_DIR = "ANYDOC_STORAGE_DIR"
ENV_MAX_UPLOAD_MB = "ANYDOC_MAX_UPLOAD_MB"
ENV_SESSION_TTL = "ANYDOC_SESSION_TTL"
ENV_CLEANUP_INTERVAL = "ANYDOC_CLEANUP_INTERVAL"


@dataclass(frozen=True)
class ServerSettings:
    """Settings of the API server.

    Attributes:
        storage_dir: Directory holding uploaded documents. ``None`` uses a private
            temporary directory that is removed when the server shuts down.
        max_upload_bytes: Largest accepted upload (architecture default: 200 MB).
        session_ttl: Seconds a document session lives after its last access.
        cleanup_interval: Seconds between background sweeps of expired sessions;
            ``0`` disables the background task (expired sessions are still
            removed lazily when they are accessed or listed).
    """

    storage_dir: Path | None = None
    max_upload_bytes: int = DEFAULT_MAX_UPLOAD_BYTES
    session_ttl: float = DEFAULT_SESSION_TTL
    cleanup_interval: float = DEFAULT_CLEANUP_INTERVAL

    def __post_init__(self) -> None:
        if self.max_upload_bytes <= 0:
            raise ValueError("max_upload_bytes must be positive")
        if self.session_ttl <= 0:
            raise ValueError("session_ttl must be positive")
        if self.cleanup_interval < 0:
            raise ValueError("cleanup_interval must not be negative")

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> ServerSettings:
        """Build settings from ``ANYDOC_*`` environment variables.

        ``ANYDOC_STORAGE_DIR`` (path), ``ANYDOC_MAX_UPLOAD_MB`` (megabytes, may be
        fractional), ``ANYDOC_SESSION_TTL`` and ``ANYDOC_CLEANUP_INTERVAL`` (seconds).
        Unset or blank variables keep the defaults.

        Raises:
            ValueError: if a variable is not a number or is out of range.
        """
        env = os.environ if environ is None else environ

        def number(name: str) -> float | None:
            raw = env.get(name, "").strip()
            if not raw:
                return None
            try:
                return float(raw)
            except ValueError:
                raise ValueError(f"{name} must be a number, got {raw!r}") from None

        storage = env.get(ENV_STORAGE_DIR, "").strip()
        max_mb = number(ENV_MAX_UPLOAD_MB)
        ttl = number(ENV_SESSION_TTL)
        interval = number(ENV_CLEANUP_INTERVAL)
        return cls(
            storage_dir=Path(storage) if storage else None,
            max_upload_bytes=(
                DEFAULT_MAX_UPLOAD_BYTES if max_mb is None else int(max_mb * 1024 * 1024)
            ),
            session_ttl=DEFAULT_SESSION_TTL if ttl is None else ttl,
            cleanup_interval=DEFAULT_CLEANUP_INTERVAL if interval is None else interval,
        )
