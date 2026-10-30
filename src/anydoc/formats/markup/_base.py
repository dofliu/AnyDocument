"""Shared helpers for the markup-family documents."""

from __future__ import annotations

from pathlib import Path

from anydoc.core.errors import CorruptFileError


def read_source(source: Path | bytes) -> tuple[bytes, Path | None]:
    """Return ``(bytes, path)`` for a path or raw bytes source."""
    if isinstance(source, Path):
        try:
            return source.read_bytes(), source
        except OSError as exc:
            raise CorruptFileError(f"Cannot read {source}: {exc}") from exc
    return source, None


def save_raw(raw: bytes, source_path: Path | None, path: Path | None) -> Path:
    """Write the original bytes to ``path`` (default: the source path)."""
    target = path or source_path
    if target is None:
        raise ValueError("No path given and document has no source path")
    target.write_bytes(raw)
    return target
