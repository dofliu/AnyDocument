"""Tests for the document session store (T100)."""

from __future__ import annotations

import io
import json
import os
import threading
from pathlib import Path

import pytest

from anydoc.core.errors import AnyDocError, SecurityLimitError, UnsupportedFormatError
from anydoc.server.store import SessionNotFoundError, SessionStore, sanitize_filename

TEXT = "哈囉，AnyDocument\n".encode()


class Clock:
    def __init__(self, now: float = 1_000_000.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def store(tmp_path: Path, clock: Clock) -> SessionStore:
    return SessionStore(tmp_path / "store", ttl=100, max_bytes=1000, clock=clock)


# -- sanitize_filename ------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("report.pdf", "report.pdf"),
        ("報告 2026.docx", "報告 2026.docx"),
        ("../../etc/passwd", "passwd"),
        ("C:\\Users\\me\\圖面.dxf", "圖面.dxf"),
        ("/abs/path/a.txt", "a.txt"),
        ("..", "upload"),
        (".", "upload"),
        ("", "upload"),
        (None, "upload"),
        ("dir/", "upload"),
        (".hidden.txt", "hidden.txt"),
        ("a\x00b\nc.txt", "abc.txt"),
        ('a<b>:"c"|?*.txt', "a_b___c____.txt"),
        ("trailing. ", "trailing"),
        ("meta.json", "_meta.json"),
        ("CON.txt", "_CON.txt"),
        ("nul", "_nul"),
    ],
)
def test_sanitize_filename(raw: str | None, expected: str) -> None:
    assert sanitize_filename(raw) == expected


def test_sanitize_filename_is_idempotent_and_bounded() -> None:
    long_name = "長" * 300 + ".xlsx"
    safe = sanitize_filename(long_name)
    assert safe.endswith(".xlsx")
    assert len(safe.encode()) <= 200
    assert sanitize_filename(safe) == safe
    no_ext = sanitize_filename("x" * 500)
    assert len(no_ext) == 200
    weird = sanitize_filename("a." + "e" * 100)
    assert len(weird.encode()) <= 200


def test_sanitize_filename_normalises_unicode() -> None:
    assert sanitize_filename("cafe\u0301.txt") == "caf\u00e9.txt"


# -- create / get -----------------------------------------------------------------------


def test_create_stores_file_and_record(store: SessionStore, clock: Clock) -> None:
    record = store.create("說明.txt", TEXT)
    assert len(record.id) == 32
    assert record.filename == "說明.txt"
    assert record.format == "txt"
    assert record.mime == "text/plain"
    assert record.size == len(TEXT)
    assert record.created == record.accessed == clock.now
    assert record.expires == clock.now + 100
    assert record.path == store.root / record.id / "說明.txt"
    assert record.path.read_bytes() == TEXT
    assert store.get(record.id, touch=False) == record
    assert record.id in store
    assert len(store) == 1


def test_create_accepts_file_objects_and_chunks(store: SessionStore) -> None:
    from_file = store.create("a.txt", io.BytesIO(TEXT))
    from_chunks = store.create("b.txt", iter([TEXT[:5], TEXT[5:]]))
    from_bytearray = store.create("c.txt", bytearray(TEXT))
    for record in (from_file, from_chunks, from_bytearray):
        assert record.path.read_bytes() == TEXT


def test_create_detects_by_content_not_extension(store: SessionStore) -> None:
    record = store.create("data.pdf", b'{"a": 1}')
    assert record.format == "json"
    assert record.filename == "data.pdf"


def test_create_sanitises_traversal(store: SessionStore) -> None:
    record = store.create("../../outside.txt", TEXT)
    assert record.path.parent == store.root / record.id
    assert not (store.root.parent / "outside.txt").exists()


def test_create_rejects_oversize_and_cleans_up(store: SessionStore) -> None:
    with pytest.raises(SecurityLimitError, match="1000 bytes"):
        store.create("big.txt", iter([b"x" * 600, b"x" * 600]))
    assert list(store.root.iterdir()) == []
    assert store.create("ok.txt", b"x" * 1000).size == 1000


def test_create_rejects_empty_and_unknown(store: SessionStore) -> None:
    with pytest.raises(UnsupportedFormatError, match="Empty file: a.txt"):
        store.create("a.txt", b"")
    with pytest.raises(UnsupportedFormatError):
        store.create("a.bin", bytes(range(256)) * 2)
    assert list(store.root.iterdir()) == []
    assert len(store) == 0


def test_to_dict_has_iso_times_and_no_path(store: SessionStore) -> None:
    data = store.create("a.txt", TEXT).to_dict()
    assert data["created"] == "1970-01-12T13:46:40Z"
    assert data["expires"] == "1970-01-12T13:48:20Z"
    assert "path" not in data
    json.dumps(data)


@pytest.mark.parametrize("bad", ["", "nope", "../x", "A" * 32, "g" * 32, "0" * 31, "0" * 32])
def test_get_unknown_or_malformed_id(store: SessionStore, bad: str) -> None:
    with pytest.raises(SessionNotFoundError) as info:
        store.get(bad)
    assert info.value.doc_id == bad
    assert isinstance(info.value, AnyDocError)
    assert bad not in store


def test_contains_non_string(store: SessionStore) -> None:
    assert 123 not in store


# -- expiry -----------------------------------------------------------------------------


def test_touch_renews_expiry(store: SessionStore, clock: Clock) -> None:
    record = store.create("a.txt", TEXT)
    clock.now += 60
    touched = store.get(record.id)
    assert touched.accessed == clock.now
    assert touched.expires == clock.now + 100
    assert touched.created == record.created
    clock.now += 60  # 120 s after upload, but only 60 s after the last access
    assert store.get(record.id, touch=False).expires == touched.expires


def test_get_without_touch_does_not_renew(store: SessionStore, clock: Clock) -> None:
    record = store.create("a.txt", TEXT)
    clock.now += 60
    assert store.get(record.id, touch=False).expires == record.expires
    clock.now += 40
    with pytest.raises(SessionNotFoundError):
        store.get(record.id)


def test_expired_session_is_removed_on_access(store: SessionStore, clock: Clock) -> None:
    record = store.create("a.txt", TEXT)
    clock.now += 100
    with pytest.raises(SessionNotFoundError):
        store.get(record.id)
    assert not record.path.parent.exists()


def test_all_hides_expired_and_sorts_by_creation(store: SessionStore, clock: Clock) -> None:
    first = store.create("1.txt", TEXT)
    clock.now += 50
    second = store.create("2.txt", TEXT)
    clock.now += 10
    third = store.create("3.txt", TEXT)
    assert [r.id for r in store.all()] == [first.id, second.id, third.id]
    clock.now += 45  # first expired (105 s), others alive
    assert [r.id for r in store.all()] == [second.id, third.id]
    assert len(store) == 2
    assert first.path.exists()  # listing does not delete; purge does


def test_purge_expired(store: SessionStore, clock: Clock) -> None:
    old = store.create("old.txt", TEXT)
    clock.now += 80
    fresh = store.create("fresh.txt", TEXT)
    assert store.purge_expired() == []
    clock.now += 30
    assert store.purge_expired() == [old.id]
    assert not old.path.parent.exists()
    assert fresh.path.exists()
    clock.now += 1000
    assert store.purge_expired() == [fresh.id]
    assert list(store.root.iterdir()) == []


def test_purge_removes_stale_debris_but_keeps_recent(store: SessionStore, clock: Clock) -> None:
    stale = store.root / ("a" * 32)
    recent = store.root / ("b" * 32)
    unrelated = store.root / "notes"
    for folder in (stale, recent, unrelated):
        folder.mkdir()
    os.utime(stale, (clock.now - 500, clock.now - 500))
    os.utime(recent, (clock.now - 5, clock.now - 5))
    assert store.purge_expired() == []
    assert not stale.exists()
    assert recent.exists()
    assert unrelated.exists()


# -- delete / close ---------------------------------------------------------------------


def test_delete(store: SessionStore) -> None:
    record = store.create("a.txt", TEXT)
    store.delete(record.id)
    assert not record.path.parent.exists()
    with pytest.raises(SessionNotFoundError):
        store.delete(record.id)
    with pytest.raises(SessionNotFoundError):
        store.get(record.id)


def test_close_removes_private_temp_root() -> None:
    store = SessionStore(ttl=10)
    root = store.root
    record = store.create("a.txt", TEXT)
    assert record.path.exists()
    store.close()
    store.close()  # idempotent
    assert not root.exists()
    assert store.purge_expired() == []
    with pytest.raises(RuntimeError, match="closed"):
        store.create("b.txt", TEXT)
    with pytest.raises(RuntimeError, match="closed"):
        store.get(record.id)


def test_close_keeps_explicit_root(tmp_path: Path) -> None:
    store = SessionStore(tmp_path / "keep", ttl=10)
    record = store.create("a.txt", TEXT)
    store.close()
    assert record.path.exists()


def test_sessions_survive_a_new_store_on_the_same_root(tmp_path: Path, clock: Clock) -> None:
    first = SessionStore(tmp_path, ttl=100, clock=clock)
    record = first.create("共用.txt", TEXT)
    second = SessionStore(tmp_path, ttl=100, clock=clock)
    assert second.get(record.id, touch=False) == record
    second.delete(record.id)
    assert record.id not in first


# -- robustness -------------------------------------------------------------------------


@pytest.mark.parametrize(
    "meta",
    [
        "not json",
        "{}",
        '{"filename": "../../etc/passwd", "format": "txt", "mime": "text/plain", "size": 1,'
        ' "created": 1, "accessed": 1, "expires": 9e12}',
        '{"filename": "gone.txt", "format": "txt", "mime": "text/plain", "size": 1,'
        ' "created": 1, "accessed": 1, "expires": 9e12}',
        '{"filename": "a.txt", "format": "txt", "mime": "text/plain", "size": "x",'
        ' "created": 1, "accessed": 1, "expires": 9e12}',
    ],
)
def test_tampered_metadata_is_treated_as_missing(store: SessionStore, meta: str) -> None:
    record = store.create("a.txt", TEXT)
    (record.path.parent / "meta.json").write_text(meta, encoding="utf-8")
    with pytest.raises(SessionNotFoundError):
        store.get(record.id)
    assert store.all() == []


def test_invalid_arguments(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        SessionStore(tmp_path, ttl=0)
    with pytest.raises(ValueError):
        SessionStore(tmp_path, max_bytes=0)


def test_missing_root_lists_nothing(tmp_path: Path) -> None:
    store = SessionStore(tmp_path / "s", ttl=10)
    store.root.rmdir()
    assert store.all() == []
    assert store.purge_expired() == []


def test_concurrent_creates_and_touches(store: SessionStore) -> None:
    ids: list[str] = []
    errors: list[BaseException] = []
    shared = store.create("shared.txt", TEXT)

    def work(n: int) -> None:
        try:
            record = store.create(f"{n}.txt", TEXT)
            ids.append(record.id)
            for _ in range(20):
                store.get(shared.id)
                store.get(record.id)
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=work, args=(n,)) for n in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert errors == []
    assert len(set(ids)) == 8
    assert len(store) == 9
