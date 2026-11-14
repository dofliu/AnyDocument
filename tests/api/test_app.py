"""API tests for the FastAPI skeleton (T100): health, upload, sessions, download, OpenAPI."""

from __future__ import annotations

import time
from collections.abc import Iterator
from pathlib import Path
from urllib.parse import quote

import pytest
from fastapi.testclient import TestClient

from anydoc import __version__
from anydoc.core.errors import (
    AnyDocError,
    CorruptFileError,
    ExternalToolMissingError,
    OperationNotSupportedError,
)
from anydoc.server import ServerSettings, SessionStore, create_app
from tests.api.conftest import MAX_UPLOAD, TTL, Clock

TEXT = "第一行：哈囉\nsecond line\n".encode()


def _upload(client: TestClient, name: str = "說明.txt", data: bytes = TEXT) -> dict[str, str]:
    response = client.post("/docs", files={"file": (name, data, "application/octet-stream")})
    assert response.status_code == 201, response.text
    body: dict[str, str] = response.json()
    return body


def _error_code(response: object) -> str:
    body = response.json()  # type: ignore[attr-defined]
    assert set(body) == {"error"}
    assert set(body["error"]) == {"code", "message"}
    assert body["error"]["message"]
    code: str = body["error"]["code"]
    return code


# -- health / formats -------------------------------------------------------------------


def test_health(client: TestClient) -> None:
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "version": __version__,
        "documents": 0,
        "max_upload_bytes": MAX_UPLOAD,
        "session_ttl_seconds": TTL,
    }
    _upload(client)
    assert client.get("/health").json()["documents"] == 1


def test_formats_lists_registered_handlers(client: TestClient) -> None:
    rows = {row["format"]: row for row in client.get("/formats").json()}
    assert {"pdf", "docx", "xlsx", "pptx", "csv", "dxf", "png"} <= set(rows)
    assert "pdf" in rows["pdf"]["extensions"]
    assert "application/pdf" in rows["pdf"]["mime_types"]
    assert "read" in rows["pdf"]["capabilities"]


# -- upload -----------------------------------------------------------------------------


def test_upload_creates_session(client: TestClient, store: SessionStore, clock: Clock) -> None:
    response = client.post("/docs", files={"file": ("說明.txt", TEXT, "text/plain")})
    assert response.status_code == 201
    body = response.json()
    assert response.headers["location"] == f"/docs/{body['id']}"
    assert body == {
        "id": body["id"],
        "filename": "說明.txt",
        "format": "txt",
        "mime": "text/plain",
        "size": len(TEXT),
        "created": "2023-11-14T22:13:20Z",
        "accessed": "2023-11-14T22:13:20Z",
        "expires": "2023-11-14T22:15:00Z",
        "download_url": f"/docs/{body['id']}/download",
    }
    record = store.get(body["id"], touch=False)
    assert record.path.read_bytes() == TEXT
    assert record.path.is_relative_to(store.root)
    assert clock.now == record.created


def test_upload_detects_format_from_content(client: TestClient) -> None:
    body = _upload(client, "actually-json.pdf", b'{"key": "\xe5\x80\xbc"}')
    assert body["format"] == "json"
    assert body["mime"] == "application/json"


def test_upload_sanitises_filename(client: TestClient, store: SessionStore) -> None:
    body = _upload(client, "..\\..\\secret/../evil.txt")
    assert body["filename"] == "evil.txt"
    assert store.get(body["id"]).path.parent == store.root / body["id"]
    assert [p.name for p in store.root.iterdir()] == [body["id"]]


def test_upload_at_exact_limit_is_accepted(client: TestClient) -> None:
    assert int(_upload(client, "max.txt", b"a" * MAX_UPLOAD)["size"]) == MAX_UPLOAD


def test_upload_slightly_over_limit_is_rejected(client: TestClient, store: SessionStore) -> None:
    response = client.post("/docs", files={"file": ("big.txt", b"a" * (MAX_UPLOAD + 1))})
    assert response.status_code == 413
    assert _error_code(response) == "too_large"
    assert str(MAX_UPLOAD) in response.json()["error"]["message"]
    assert list(store.root.iterdir()) == []


def test_upload_far_over_limit_is_rejected_by_content_length(
    client: TestClient, store: SessionStore
) -> None:
    response = client.post("/docs", files={"file": ("big.txt", b"a" * (MAX_UPLOAD * 3))})
    assert response.status_code == 413
    assert _error_code(response) == "too_large"
    assert list(store.root.iterdir()) == []


def test_chunked_upload_without_content_length_is_limited(
    client: TestClient, store: SessionStore
) -> None:
    boundary = "anydocboundary"

    def body() -> Iterator[bytes]:
        yield (
            f"--{boundary}\r\n"
            'Content-Disposition: form-data; name="file"; filename="stream.txt"\r\n'
            "Content-Type: text/plain\r\n\r\n"
        ).encode()
        for _ in range(MAX_UPLOAD * 4 // 65536):
            yield b"z" * 65536
        yield f"\r\n--{boundary}--\r\n".encode()

    response = client.post(
        "/docs",
        content=body(),
        headers={"content-type": f"multipart/form-data; boundary={boundary}"},
    )
    assert "content-length" not in response.request.headers
    assert response.status_code == 413
    assert _error_code(response) == "too_large"
    assert list(store.root.iterdir()) == []


def test_chunked_upload_within_limit(client: TestClient) -> None:
    boundary = "anydocboundary"
    parts = [
        (
            f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="s.txt"\r\n\r\n'
        ).encode(),
        TEXT,
        f"\r\n--{boundary}--\r\n".encode(),
    ]
    response = client.post(
        "/docs",
        content=iter(parts),
        headers={"content-type": f"multipart/form-data; boundary={boundary}"},
    )
    assert response.status_code == 201
    assert response.json()["size"] == len(TEXT)


def test_upload_empty_file(client: TestClient, store: SessionStore) -> None:
    response = client.post("/docs", files={"file": ("empty.txt", b"")})
    assert response.status_code == 415
    assert _error_code(response) == "unsupported_format"
    assert str(store.root) not in response.text
    assert list(store.root.iterdir()) == []


def test_upload_unknown_format(client: TestClient, store: SessionStore) -> None:
    response = client.post("/docs", files={"file": ("blob.bin", bytes(range(256)) * 4)})
    assert response.status_code == 415
    assert _error_code(response) == "unsupported_format"
    assert list(store.root.iterdir()) == []


def test_upload_missing_file_field(client: TestClient) -> None:
    wrong_name = client.post("/docs", files={"document": ("a.txt", TEXT)})
    assert wrong_name.status_code == 400
    assert _error_code(wrong_name) == "bad_request"
    text_field = client.post("/docs", files={"file": (None, "just a string")})
    assert text_field.status_code == 400


def test_upload_requires_multipart(client: TestClient) -> None:
    for kwargs in ({"content": TEXT}, {"json": {"file": "x"}}, {"data": {"file": "x"}}, {}):
        response = client.post("/docs", **kwargs)  # type: ignore[arg-type]
        assert response.status_code == 400
        assert _error_code(response) == "bad_request"


def test_upload_malformed_multipart(client: TestClient) -> None:
    response = client.post(
        "/docs", content=b"garbage", headers={"content-type": "multipart/form-data"}
    )
    assert response.status_code == 400
    assert _error_code(response) == "bad_request"


def test_upload_too_many_files(client: TestClient, store: SessionStore) -> None:
    response = client.post("/docs", files=[("file", ("a.txt", TEXT)), ("file", ("b.txt", TEXT))])
    assert response.status_code == 400
    assert list(store.root.iterdir()) == []


# -- sessions ---------------------------------------------------------------------------


def test_get_document_renews_expiry(client: TestClient, clock: Clock) -> None:
    body = _upload(client)
    clock.now += 60
    fetched = client.get(f"/docs/{body['id']}").json()
    assert fetched["created"] == body["created"]
    assert fetched["accessed"] == "2023-11-14T22:14:20Z"
    assert fetched["expires"] == "2023-11-14T22:16:00Z"
    clock.now += 60  # past the original expiry, within the renewed one
    assert client.get(f"/docs/{body['id']}").status_code == 200


def test_session_expires_after_ttl(client: TestClient, store: SessionStore, clock: Clock) -> None:
    body = _upload(client)
    clock.now += TTL
    for url in (f"/docs/{body['id']}", f"/docs/{body['id']}/download"):
        response = client.get(url)
        assert response.status_code == 404
        assert _error_code(response) == "not_found"
    assert client.delete(f"/docs/{body['id']}").status_code == 404
    assert client.get("/docs").json() == []
    assert not (store.root / body["id"]).exists()


def test_list_documents(client: TestClient, clock: Clock) -> None:
    assert client.get("/docs").json() == []
    first = _upload(client, "一.txt")
    clock.now += 10
    second = _upload(client, "二.csv", "名稱,數量\n螺絲,3\n".encode())
    listed = client.get("/docs").json()
    assert [d["id"] for d in listed] == [first["id"], second["id"]]
    assert listed[1]["format"] == "csv"
    assert listed[0]["expires"] == first["expires"]  # listing does not renew


def test_delete_document(client: TestClient, store: SessionStore) -> None:
    body = _upload(client)
    response = client.delete(f"/docs/{body['id']}")
    assert response.status_code == 204
    assert response.content == b""
    assert not (store.root / body["id"]).exists()
    again = client.delete(f"/docs/{body['id']}")
    assert again.status_code == 404
    assert _error_code(again) == "not_found"
    assert client.get(f"/docs/{body['id']}").status_code == 404


@pytest.mark.parametrize("doc_id", ["nope", "0" * 32, "..%2F..%2Fetc", "A" * 32])
def test_unknown_document(client: TestClient, doc_id: str) -> None:
    for method, url in (
        ("GET", f"/docs/{doc_id}"),
        ("GET", f"/docs/{doc_id}/download"),
        ("DELETE", f"/docs/{doc_id}"),
    ):
        response = client.request(method, url)
        assert response.status_code == 404
        assert _error_code(response) == "not_found"


# -- download ---------------------------------------------------------------------------


def test_download_returns_original_bytes(client: TestClient) -> None:
    body = _upload(client, "說明 v2.txt")
    response = client.get(body["download_url"])
    assert response.status_code == 200
    assert response.content == TEXT
    assert response.headers["content-type"].startswith("text/plain")
    assert response.headers["content-length"] == str(len(TEXT))
    disposition = response.headers["content-disposition"]
    assert disposition == f"attachment; filename*=utf-8''{quote('說明 v2.txt')}"


def test_download_ascii_filename_and_range(client: TestClient) -> None:
    body = _upload(client, "plain.txt", b"0123456789")
    response = client.get(body["download_url"], headers={"range": "bytes=2-5"})
    assert response.status_code == 206
    assert response.content == b"2345"
    assert 'filename="plain.txt"' in response.headers["content-disposition"]


def test_download_renews_expiry(client: TestClient, clock: Clock) -> None:
    body = _upload(client)
    clock.now += 90
    assert client.get(body["download_url"]).status_code == 200
    clock.now += 90
    assert client.get(body["download_url"]).status_code == 200


# -- errors -----------------------------------------------------------------------------


def test_unknown_route_and_method_use_error_envelope(client: TestClient) -> None:
    missing = client.get("/no-such-route")
    assert missing.status_code == 404
    assert _error_code(missing) == "not_found"
    wrong_method = client.put("/docs")
    assert wrong_method.status_code == 405
    assert _error_code(wrong_method) == "method_not_allowed"
    assert "allow" in wrong_method.headers


@pytest.mark.parametrize(
    ("exc", "status", "code"),
    [
        (CorruptFileError("壞掉的檔案"), 422, "corrupt_file"),
        (ExternalToolMissingError("libreoffice"), 501, "external_tool_missing"),
        (OperationNotSupportedError("nope"), 400, "operation_not_supported"),
        (AnyDocError("generic"), 500, "internal_error"),
    ],
)
def test_anydoc_errors_map_to_http_status(
    store: SessionStore, exc: AnyDocError, status: int, code: str
) -> None:
    app = create_app(ServerSettings(cleanup_interval=0), store=store)

    @app.get("/boom")
    def boom() -> None:
        raise exc

    @app.get("/typed/{number}")
    def typed(number: int) -> int:
        return number

    with TestClient(app) as test_client:
        response = test_client.get("/boom")
        assert response.status_code == status
        assert response.json() == {"error": {"code": code, "message": str(exc)}}
        invalid = test_client.get("/typed/abc")
        assert invalid.status_code == 422
        assert _error_code(invalid) == "validation_error"
        assert "path.number" in invalid.json()["error"]["message"]


# -- OpenAPI ----------------------------------------------------------------------------


def test_openapi_schema(client: TestClient) -> None:
    schema = client.get("/openapi.json").json()
    assert schema["info"]["title"] == "AnyDocument API"
    assert schema["info"]["version"] == __version__
    assert set(schema["paths"]) == {
        "/health",
        "/formats",
        "/docs",
        "/docs/{doc_id}",
        "/docs/{doc_id}/download",
        "/docs/{doc_id}/info",
        "/docs/{doc_id}/parts",
        "/docs/{doc_id}/text",
        "/docs/{doc_id}/render",
        "/docs/{doc_id}/render/{part}",
    }
    assert set(schema["paths"]["/docs"]) == {"get", "post"}
    assert set(schema["paths"]["/docs/{doc_id}"]) == {"get", "delete"}
    upload = schema["paths"]["/docs"]["post"]
    file_schema = upload["requestBody"]["content"]["multipart/form-data"]["schema"]
    assert file_schema["required"] == ["file"]
    assert file_schema["properties"]["file"]["format"] == "binary"
    assert {"201", "400", "413", "415"} <= set(upload["responses"])
    assert "404" in schema["paths"]["/docs/{doc_id}"]["get"]["responses"]
    components = schema["components"]["schemas"]
    assert {"DocumentInfo", "HealthResponse", "FormatInfo", "ErrorResponse"} <= set(components)
    for operation in ("get", "delete"):
        params = schema["paths"]["/docs/{doc_id}"][operation]["parameters"]
        assert [(p["name"], p["in"]) for p in params] == [("doc_id", "path")]


def test_interactive_docs_do_not_shadow_document_routes(client: TestClient) -> None:
    swagger = client.get("/api/docs")
    assert swagger.status_code == 200
    assert "swagger" in swagger.text.lower()
    assert client.get("/api/redoc").status_code == 200
    assert client.get("/docs").headers["content-type"] == "application/json"


# -- application lifecycle --------------------------------------------------------------


def test_default_store_uses_temp_dir_removed_on_shutdown() -> None:
    app = create_app(ServerSettings(max_upload_bytes=1024, session_ttl=5, cleanup_interval=0))
    store: SessionStore = app.state.store
    assert app.state.settings.max_upload_bytes == 1024
    assert store.ttl == 5
    with TestClient(app) as test_client:
        body = _upload(test_client)
        assert (store.root / body["id"]).is_dir()
        assert test_client.get("/health").json()["max_upload_bytes"] == 1024
        too_big = test_client.post("/docs", files={"file": ("a.txt", b"a" * 1025)})
        assert too_big.status_code == 413
    assert not store.root.exists()


def test_storage_dir_setting_and_injected_store_survive_shutdown(tmp_path: Path) -> None:
    settings = ServerSettings(storage_dir=tmp_path / "上傳", cleanup_interval=0)
    with TestClient(create_app(settings)) as test_client:
        body = _upload(test_client)
    assert (tmp_path / "上傳" / body["id"] / "說明.txt").read_bytes() == TEXT

    store = SessionStore(tmp_path / "injected", ttl=10)
    with TestClient(create_app(settings, store=store)) as test_client:
        body = _upload(test_client)
    assert store.get(body["id"]).size == len(TEXT)  # caller-owned store is not closed


def test_settings_default_to_environment(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("ANYDOC_STORAGE_DIR", str(tmp_path / "env"))
    monkeypatch.setenv("ANYDOC_MAX_UPLOAD_MB", "1")
    monkeypatch.setenv("ANYDOC_SESSION_TTL", "42")
    monkeypatch.setenv("ANYDOC_CLEANUP_INTERVAL", "0")
    app = create_app()
    assert app.state.store.root == tmp_path / "env"
    assert app.state.store.max_bytes == 1024 * 1024
    assert app.state.store.ttl == 42


def test_startup_purges_expired_sessions(store: SessionStore, clock: Clock) -> None:
    record = store.create("old.txt", TEXT)
    clock.now += TTL + 1
    with TestClient(create_app(ServerSettings(cleanup_interval=0), store=store)):
        assert not record.path.parent.exists()


def test_background_sweep_removes_expired_sessions(store: SessionStore, clock: Clock) -> None:
    settings = ServerSettings(cleanup_interval=0.02)
    with TestClient(create_app(settings, store=store)) as test_client:
        body = _upload(test_client)
        folder = store.root / body["id"]
        time.sleep(0.15)
        assert folder.is_dir()  # still alive: the sweep leaves live sessions alone
        clock.now += TTL + 1
        deadline = time.monotonic() + 5
        while folder.exists() and time.monotonic() < deadline:
            time.sleep(0.02)
        assert not folder.exists()
