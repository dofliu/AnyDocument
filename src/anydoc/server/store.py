"""Document session storage: uploaded files in an isolated directory with a TTL.

Every uploaded document becomes a *session*: a directory ``<root>/<id>/`` holding the
file (under its sanitised original name, so the extension keeps working as a format
hint) and a ``meta.json`` record. The file system is the single source of truth, so
several server processes can share one storage directory.
"""

from __future__ import annotations

import contextlib
import json
import os
import re
import shutil
import tempfile
import threading
import time
import unicodedata
import uuid
from collections.abc import Callable, Iterable
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, BinaryIO

from anydoc.core.detect import detect
from anydoc.core.errors import AnyDocError, SecurityLimitError, UnsupportedFormatError

__all__ = [
    "DocumentRecord",
    "SessionNotFoundError",
    "SessionStore",
    "sanitize_filename",
]

_ID_RE = re.compile(r"[0-9a-f]{32}")
_META_NAME = "meta.json"
_FALLBACK_NAME = "upload"
_MAX_NAME_BYTES = 200
_CHUNK = 1024 * 1024
# Reserved device names on Windows; harmless elsewhere but kept portable.
_RESERVED = {"CON", "PRN", "AUX", "NUL"} | {f"{p}{n}" for p in ("COM", "LPT") for n in range(1, 10)}


class SessionNotFoundError(AnyDocError):
    """Raised when a document session does not exist or has expired."""

    def __init__(self, doc_id: str) -> None:
        self.doc_id = doc_id
        super().__init__(f"Document not found: {doc_id}")


def sanitize_filename(name: str | None) -> str:
    """Return a safe single path component derived from an uploaded file name.

    Directory parts (``/`` and ``\\``), control characters and characters that are
    invalid on common file systems are removed; the result never starts with a dot,
    is never empty or a reserved device name, and is at most 200 UTF-8 bytes long
    (the extension is preserved when shortening). Non-ASCII names are kept.
    """
    text = unicodedata.normalize("NFC", name or "")
    text = text.replace("\\", "/").rsplit("/", 1)[-1]
    text = "".join(ch for ch in text if unicodedata.category(ch)[0] != "C")
    text = re.sub(r'[<>:"|?*]', "_", text).strip().lstrip(".").rstrip(". ")
    if not text:
        return _FALLBACK_NAME
    if text == _META_NAME:
        text = f"_{text}"
    stem, dot, ext = text.rpartition(".")
    if not dot:
        stem, ext = text, ""
    if stem.upper() in _RESERVED:
        stem = f"_{stem}"
    suffix = f".{ext}" if dot else ""
    if len(suffix.encode()) > 32:
        stem, suffix = stem + suffix, ""
    budget = _MAX_NAME_BYTES - len(suffix.encode())
    stem = stem.encode()[:budget].decode(errors="ignore")
    return (stem + suffix) or _FALLBACK_NAME


@dataclass(frozen=True)
class DocumentRecord:
    """An uploaded document held by the server.

    Times are POSIX timestamps (seconds); ``expires`` moves forward on each access.
    """

    id: str
    filename: str
    format: str
    mime: str
    size: int
    created: float
    accessed: float
    expires: float
    path: Path

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serialisable dict (times as ISO 8601 UTC, without ``path``)."""
        return {
            "id": self.id,
            "filename": self.filename,
            "format": self.format,
            "mime": self.mime,
            "size": self.size,
            "created": _iso(self.created),
            "accessed": _iso(self.accessed),
            "expires": _iso(self.expires),
        }


def _iso(timestamp: float) -> str:
    return datetime.fromtimestamp(timestamp, UTC).isoformat().replace("+00:00", "Z")


class SessionStore:
    """Thread-safe store of document sessions with sliding expiry.

    Args:
        root: Storage directory (created if missing). ``None`` creates a private
            temporary directory that :meth:`close` removes.
        ttl: Seconds a session lives after its last access.
        max_bytes: Largest accepted document; larger uploads raise
            :class:`~anydoc.core.errors.SecurityLimitError`.
        clock: Function returning the current POSIX time (injectable for tests).
    """

    def __init__(
        self,
        root: Path | None = None,
        *,
        ttl: float = 3600.0,
        max_bytes: int = 200 * 1024 * 1024,
        clock: Callable[[], float] = time.time,
    ) -> None:
        if ttl <= 0:
            raise ValueError("ttl must be positive")
        if max_bytes <= 0:
            raise ValueError("max_bytes must be positive")
        self.ttl = ttl
        self.max_bytes = max_bytes
        self._clock = clock
        self._lock = threading.RLock()
        self._owns_root = root is None
        if root is None:
            self.root = Path(tempfile.mkdtemp(prefix="anydoc-server-"))
        else:
            self.root = Path(root)
            self.root.mkdir(parents=True, exist_ok=True)
        self._closed = False

    # -- creation ---------------------------------------------------------------------

    def create(
        self, filename: str | None, data: bytes | BinaryIO | Iterable[bytes]
    ) -> DocumentRecord:
        """Store an uploaded document and return its record.

        ``data`` may be bytes, a binary file object or an iterable of byte chunks; it
        is copied in chunks and never held in memory as a whole.

        Raises:
            SecurityLimitError: if the document is larger than ``max_bytes``.
            UnsupportedFormatError: if the content is empty or of an unknown format.
        """
        self._check_open()
        doc_id = uuid.uuid4().hex
        name = sanitize_filename(filename)
        folder = self.root / doc_id
        folder.mkdir(mode=0o700)
        target = folder / name
        try:
            size = self._write(target, data)
            if size == 0:
                raise UnsupportedFormatError(f"Empty file: {name}")
            detection = detect(target)
            now = self._clock()
            record = DocumentRecord(
                id=doc_id,
                filename=name,
                format=detection.format,
                mime=detection.mime,
                size=size,
                created=now,
                accessed=now,
                expires=now + self.ttl,
                path=target,
            )
            with self._lock:
                self._write_meta(record)
        except BaseException:
            shutil.rmtree(folder, ignore_errors=True)
            raise
        return record

    def _write(self, target: Path, data: bytes | BinaryIO | Iterable[bytes]) -> int:
        chunks: Iterable[bytes]
        if isinstance(data, bytes | bytearray | memoryview):
            chunks = (bytes(data),)
        elif hasattr(data, "read"):
            chunks = iter(lambda: data.read(_CHUNK), b"")
        else:
            chunks = data
        size = 0
        with target.open("wb") as out:
            for chunk in chunks:
                size += len(chunk)
                if size > self.max_bytes:
                    raise SecurityLimitError(
                        f"Upload exceeds the size limit of {self.max_bytes} bytes"
                    )
                out.write(chunk)
        return size

    # -- lookup -----------------------------------------------------------------------

    def get(self, doc_id: str, *, touch: bool = True) -> DocumentRecord:
        """Return the record of ``doc_id``; ``touch`` renews its expiry.

        Raises:
            SessionNotFoundError: if the id is malformed, unknown or expired
                (an expired session is removed).
        """
        self._check_open()
        with self._lock:
            record = self._read_meta(doc_id)
            if record is None:
                raise SessionNotFoundError(doc_id)
            now = self._clock()
            if record.expires <= now:
                self._remove(doc_id)
                raise SessionNotFoundError(doc_id)
            if touch:
                record = replace(record, accessed=now, expires=now + self.ttl)
                self._write_meta(record)
            return record

    def all(self) -> list[DocumentRecord]:
        """Return all live sessions, oldest first, without renewing them."""
        self._check_open()
        now = self._clock()
        with self._lock:
            records = [r for r in self._scan() if r.expires > now]
        return sorted(records, key=lambda r: (r.created, r.id))

    def __len__(self) -> int:
        return len(self.all())

    def __contains__(self, doc_id: object) -> bool:
        if not isinstance(doc_id, str):
            return False
        try:
            self.get(doc_id, touch=False)
        except SessionNotFoundError:
            return False
        return True

    # -- removal ----------------------------------------------------------------------

    def delete(self, doc_id: str) -> None:
        """Remove a session and its file.

        Raises:
            SessionNotFoundError: if the session does not exist (or has expired).
        """
        with self._lock:
            self.get(doc_id, touch=False)
            self._remove(doc_id)

    def purge_expired(self) -> list[str]:
        """Remove every expired session (and stray directories); return the removed ids."""
        if self._closed:
            return []
        now = self._clock()
        removed: list[str] = []
        with self._lock:
            for folder in self._folders():
                record = self._read_meta(folder.name)
                if record is None:
                    # No readable record: an upload in progress (keep) or debris
                    # from a crash (remove once it is older than the TTL).
                    with contextlib.suppress(OSError):
                        if folder.stat().st_mtime + self.ttl <= now:
                            shutil.rmtree(folder, ignore_errors=True)
                elif record.expires <= now:
                    self._remove(record.id)
                    removed.append(record.id)
        return removed

    def close(self) -> None:
        """Release the store; a private temporary root is removed with all its files."""
        with self._lock:
            if self._closed:
                return
            self._closed = True
            if self._owns_root:
                shutil.rmtree(self.root, ignore_errors=True)

    # -- internals --------------------------------------------------------------------

    def _check_open(self) -> None:
        if self._closed:
            raise RuntimeError("SessionStore is closed")

    def _folders(self) -> list[Path]:
        try:
            return [p for p in self.root.iterdir() if p.is_dir() and _ID_RE.fullmatch(p.name)]
        except OSError:
            return []

    def _scan(self) -> list[DocumentRecord]:
        records = (self._read_meta(folder.name) for folder in self._folders())
        return [r for r in records if r is not None]

    def _read_meta(self, doc_id: str) -> DocumentRecord | None:
        if not isinstance(doc_id, str) or not _ID_RE.fullmatch(doc_id):
            return None
        folder = self.root / doc_id
        try:
            raw = json.loads((folder / _META_NAME).read_text(encoding="utf-8"))
            filename = str(raw["filename"])
            if filename != sanitize_filename(filename):
                return None
            path = folder / filename
            if not path.is_file():
                return None
            return DocumentRecord(
                id=doc_id,
                filename=filename,
                format=str(raw["format"]),
                mime=str(raw["mime"]),
                size=int(raw["size"]),
                created=float(raw["created"]),
                accessed=float(raw["accessed"]),
                expires=float(raw["expires"]),
                path=path,
            )
        except (OSError, ValueError, KeyError, TypeError):
            return None

    def _write_meta(self, record: DocumentRecord) -> None:
        folder = self.root / record.id
        payload = {
            "filename": record.filename,
            "format": record.format,
            "mime": record.mime,
            "size": record.size,
            "created": record.created,
            "accessed": record.accessed,
            "expires": record.expires,
        }
        tmp = folder / f".{_META_NAME}.{uuid.uuid4().hex}.tmp"
        tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, folder / _META_NAME)

    def _remove(self, doc_id: str) -> None:
        shutil.rmtree(self.root / doc_id, ignore_errors=True)
