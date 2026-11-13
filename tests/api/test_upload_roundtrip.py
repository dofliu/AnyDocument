"""Round-trip tests (T100): upload every sample format, download it, reopen it."""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import anydoc
from anydoc.server import SessionStore
from tests.fixtures.generate import SAMPLES

EXPECTED_FORMAT = {"csv-bom": "csv", "csv-big5": "csv", "jpg": "jpeg"}


@pytest.mark.parametrize("sample_id", sorted(SAMPLES))
def test_upload_download_roundtrip(
    client: TestClient,
    store: SessionStore,
    sample: Callable[[str], Path],
    tmp_path: Path,
    sample_id: str,
) -> None:
    source = sample(sample_id)
    original = source.read_bytes()
    name = f"樣本-{source.name}"

    uploaded = client.post("/docs", files={"file": (name, original)})
    assert uploaded.status_code == 201, uploaded.text
    info = uploaded.json()
    assert info["filename"] == name
    assert info["size"] == len(original)
    assert info["format"] == EXPECTED_FORMAT.get(sample_id, sample_id)
    assert info["format"] == anydoc.detect(source).format
    assert info["mime"] == anydoc.detect(source).mime

    downloaded = client.get(info["download_url"])
    assert downloaded.status_code == 200
    assert hashlib.sha256(downloaded.content).digest() == hashlib.sha256(original).digest()
    assert downloaded.headers["content-type"].split(";")[0] == info["mime"]

    # The stored session file and the downloaded copy both open with the format handler.
    saved = tmp_path / source.name
    saved.write_bytes(downloaded.content)
    for path in (store.get(info["id"]).path, saved):
        doc = anydoc.open(path)
        try:
            assert doc.format == info["format"]
        finally:
            doc.close()

    assert client.delete(f"/docs/{info['id']}").status_code == 204
    assert client.get(info["download_url"]).status_code == 404


def test_many_documents_are_isolated(client: TestClient, store: SessionStore) -> None:
    payloads = {f"檔案{n}.txt": f"內容 {n}\n".encode() * (n + 1) for n in range(12)}
    ids = {
        name: client.post("/docs", files={"file": (name, data)}).json()["id"]
        for name, data in payloads.items()
    }
    assert len(set(ids.values())) == len(payloads)
    assert client.get("/health").json()["documents"] == len(payloads)
    for name, data in payloads.items():
        assert client.get(f"/docs/{ids[name]}/download").content == data
    assert sorted(p.name for p in store.root.iterdir()) == sorted(ids.values())
