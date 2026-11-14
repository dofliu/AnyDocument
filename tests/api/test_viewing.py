"""API tests for the viewing endpoints (T101): info, parts, text and render."""

from __future__ import annotations

import io
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from anydoc.server import SessionStore
from anydoc.server.views import CACHE_DIR_NAME
from tests.api.conftest import TTL, Clock

Sample = Callable[[str], Path]

SVG = (
    '<svg xmlns="http://www.w3.org/2000/svg" width="100" height="60">'
    '<script>alert(1)</script><text x="5" y="30">標題</text></svg>'
)
MISSING = "0" * 32


def _upload(client: TestClient, name: str, data: bytes) -> dict[str, Any]:
    response = client.post("/docs", files={"file": (name, data)})
    assert response.status_code == 201, response.text
    body: dict[str, Any] = response.json()
    return body


def _upload_sample(client: TestClient, sample: Sample, sample_id: str) -> str:
    path = sample(sample_id)
    doc_id: str = _upload(client, path.name, path.read_bytes())["id"]
    return doc_id


def _code(response: Any) -> str:
    body = response.json()
    assert set(body) == {"error"}
    assert set(body["error"]) == {"code", "message"}
    assert body["error"]["message"]
    code: str = body["error"]["code"]
    return code


def _image(data: bytes) -> Image.Image:
    im = Image.open(io.BytesIO(data))
    im.load()
    return im


# -- /info ------------------------------------------------------------------------------


def test_info_pdf(client: TestClient, sample: Sample) -> None:
    doc_id = _upload_sample(client, sample, "pdf")
    response = client.get(f"/docs/{doc_id}/info")
    assert response.status_code == 200
    body = response.json()
    assert body["id"] == doc_id
    assert body["filename"] == "sample.pdf"
    assert body["format"] == "pdf"
    assert body["mime"] == "application/pdf"
    assert body["size"] == sample("pdf").stat().st_size
    assert body["metadata"]["format"] == "pdf"
    assert body["metadata"]["page_count"] == 2
    assert set(body["metadata"]) == {
        "format",
        "mime",
        "title",
        "author",
        "created",
        "modified",
        "page_count",
        "extra",
    }
    assert body["part_count"] == 2
    assert "read" in body["capabilities"]
    assert body["render"] == {"unit": "page", "formats": ["png", "jpeg", "webp"]}
    assert body["parts_url"] == f"/docs/{doc_id}/parts"
    assert body["text_url"] == f"/docs/{doc_id}/text"
    assert body["render_url"] == f"/docs/{doc_id}/render"
    for key in ("parts_url", "text_url", "render_url"):
        assert client.get(body[key]).status_code == 200


def test_info_reports_document_metadata(client: TestClient, sample: Sample) -> None:
    xlsx = client.get(f"/docs/{_upload_sample(client, sample, 'xlsx')}/info").json()
    assert xlsx["part_count"] == 2
    assert xlsx["render"]["unit"] == "page"
    dxf = client.get(f"/docs/{_upload_sample(client, sample, 'dxf')}/info").json()
    assert dxf["render"] == {"unit": "layout", "formats": ["png", "jpeg", "webp", "svg"]}
    assert dxf["metadata"]["extra"]["layouts"]


# -- /parts -----------------------------------------------------------------------------


def test_parts_pdf(client: TestClient, sample: Sample) -> None:
    doc_id = _upload_sample(client, sample, "pdf")
    body = client.get(f"/docs/{doc_id}/parts").json()
    assert (body["id"], body["format"], body["count"]) == (doc_id, "pdf", 2)
    assert [(p["kind"], p["index"], p["name"]) for p in body["parts"]] == [
        ("page", 0, "Page 1"),
        ("page", 1, "Page 2"),
    ]
    for part in body["parts"]:
        width, height = part["size"]
        assert width > 0 and height > 0


def test_parts_of_other_formats(client: TestClient, sample: Sample) -> None:
    sheets = client.get(f"/docs/{_upload_sample(client, sample, 'xlsx')}/parts").json()
    assert [p["kind"] for p in sheets["parts"]] == ["sheet", "sheet"]
    slides = client.get(f"/docs/{_upload_sample(client, sample, 'pptx')}/parts").json()
    assert [p["kind"] for p in slides["parts"]] == ["slide", "slide"]
    text = client.get(f"/docs/{_upload_sample(client, sample, 'txt')}/parts").json()
    assert text["parts"] == [{"kind": "text", "index": 0, "name": None, "size": None}]


# -- /text ------------------------------------------------------------------------------


def test_text_json(client: TestClient) -> None:
    content = "第一行：哈囉\nsecond line\n"
    doc_id = _upload(client, "說明.txt", content.encode())["id"]
    response = client.get(f"/docs/{doc_id}/text")
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/json"
    assert response.json() == {
        "id": doc_id,
        "format": "txt",
        "text": content,
        "offset": 0,
        "length": len(content),
        "total": len(content),
        "truncated": False,
    }


def test_text_window(client: TestClient) -> None:
    doc_id = _upload(client, "a.txt", "零一二三四五六七八九".encode())["id"]
    body = client.get(f"/docs/{doc_id}/text", params={"offset": 2, "limit": 3}).json()
    assert (body["text"], body["offset"], body["length"], body["total"]) == ("二三四", 2, 3, 10)
    assert body["truncated"] is True
    rest = client.get(f"/docs/{doc_id}/text", params={"offset": 8}).json()
    assert (rest["text"], rest["truncated"]) == ("八九", False)
    beyond = client.get(f"/docs/{doc_id}/text", params={"offset": 500}).json()
    assert (beyond["text"], beyond["offset"], beyond["length"]) == ("", 10, 0)


def test_text_plain(client: TestClient) -> None:
    doc_id = _upload(client, "a.txt", "零一二三四五六七八九".encode())["id"]
    response = client.get(f"/docs/{doc_id}/text", params={"format": "plain", "limit": 4})
    assert response.status_code == 200
    assert response.headers["content-type"] == "text/plain; charset=utf-8"
    assert response.text == "零一二三"
    assert response.headers["x-text-offset"] == "0"
    assert response.headers["x-text-total"] == "10"
    assert response.headers["x-text-truncated"] == "true"
    whole = client.get(f"/docs/{doc_id}/text", params={"format": "plain"})
    assert whole.content == "零一二三四五六七八九".encode()
    assert whole.headers["x-text-truncated"] == "false"


def test_text_of_big5_csv_is_decoded(client: TestClient, sample: Sample) -> None:
    doc_id = _upload_sample(client, sample, "csv-big5")
    utf8 = client.get(f"/docs/{_upload_sample(client, sample, 'csv')}/text").json()["text"]
    assert client.get(f"/docs/{doc_id}/text").json()["text"] == utf8


@pytest.mark.parametrize(
    "params",
    [{"offset": -1}, {"limit": 0}, {"offset": "x"}, {"format": "xml"}],
)
def test_text_rejects_invalid_parameters(client: TestClient, params: dict[str, Any]) -> None:
    doc_id = _upload(client, "a.txt", b"abc")["id"]
    response = client.get(f"/docs/{doc_id}/text", params=params)
    assert response.status_code == 422
    assert _code(response) == "validation_error"


# -- /render ----------------------------------------------------------------------------


def test_render_index(client: TestClient, sample: Sample) -> None:
    doc_id = _upload_sample(client, sample, "pdf")
    body = client.get(f"/docs/{doc_id}/render").json()
    assert body == {
        "id": doc_id,
        "format": "pdf",
        "unit": "page",
        "count": 2,
        "formats": ["png", "jpeg", "webp"],
        "urls": [f"/docs/{doc_id}/render/0", f"/docs/{doc_id}/render/1"],
    }
    for url in body["urls"]:
        assert client.get(url).status_code == 200


def test_render_png_default(client: TestClient, sample: Sample) -> None:
    doc_id = _upload_sample(client, sample, "pdf")
    response = client.get(f"/docs/{doc_id}/render/0")
    assert response.status_code == 200
    assert response.headers["content-type"] == "image/png"
    assert response.content.startswith(b"\x89PNG\r\n\x1a\n")
    image = _image(response.content)
    assert max(image.size) == 1024
    lo, hi = image.convert("L").getextrema()
    assert hi > lo
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["cache-control"] == "private, no-cache"
    assert client.get(f"/docs/{doc_id}/render/1").content != response.content


@pytest.mark.parametrize(
    ("fmt", "media_type", "pil"),
    [
        ("png", "image/png", "PNG"),
        ("jpeg", "image/jpeg", "JPEG"),
        ("jpg", "image/jpeg", "JPEG"),
        ("webp", "image/webp", "WEBP"),
    ],
)
def test_render_formats_and_size(
    client: TestClient, sample: Sample, fmt: str, media_type: str, pil: str
) -> None:
    doc_id = _upload_sample(client, sample, "pdf")
    response = client.get(f"/docs/{doc_id}/render/1", params={"format": fmt, "size": 200})
    assert response.status_code == 200
    assert response.headers["content-type"] == media_type
    image = _image(response.content)
    assert image.format == pil
    assert max(image.size) == 200


def test_render_dxf_svg_and_png(client: TestClient, sample: Sample) -> None:
    doc_id = _upload_sample(client, sample, "dxf")
    index = client.get(f"/docs/{doc_id}/render").json()
    assert (index["unit"], index["count"]) == ("layout", 2)
    assert "svg" in index["formats"]
    for url in index["urls"]:
        svg = client.get(url, params={"format": "svg"})
        assert svg.status_code == 200
        assert svg.headers["content-type"] == "image/svg+xml"
        assert b"<svg" in svg.content[:400]
    png = client.get(f"/docs/{doc_id}/render/0", params={"size": 300})
    assert png.headers["content-type"] == "image/png"
    assert max(_image(png.content).size) <= 300


def test_render_uploaded_svg_is_sandboxed(client: TestClient) -> None:
    doc_id = _upload(client, "drawing.svg", SVG.encode())["id"]
    response = client.get(f"/docs/{doc_id}/render/0", params={"format": "svg"})
    assert response.status_code == 200
    assert response.content == SVG.encode()
    assert response.headers["content-type"] == "image/svg+xml"
    policy = response.headers["content-security-policy"]
    assert "default-src 'none'" in policy and "sandbox" in policy
    assert "script-src" not in policy
    assert response.headers["x-content-type-options"] == "nosniff"
    missing = client.get(f"/docs/{doc_id}/render/1", params={"format": "svg"})
    assert (missing.status_code, _code(missing)) == (404, "part_not_found")


def test_render_text_and_image_documents(client: TestClient, sample: Sample) -> None:
    txt = _upload_sample(client, sample, "txt")
    preview = client.get(f"/docs/{txt}/render/0", params={"size": 256})
    assert preview.status_code == 200
    assert max(_image(preview.content).size) <= 256
    assert client.get(f"/docs/{txt}/render").json()["count"] == 1
    png = _upload_sample(client, sample, "png")
    frame = client.get(f"/docs/{png}/render/0", params={"format": "jpeg"})
    assert _image(frame.content).size == (64, 48)  # never enlarged
    assert client.get(f"/docs/{png}/render").json()["unit"] == "frame"


@pytest.mark.parametrize("part", [2, 99, -1])
def test_render_unknown_part(client: TestClient, sample: Sample, part: int) -> None:
    doc_id = _upload_sample(client, sample, "pdf")
    response = client.get(f"/docs/{doc_id}/render/{part}")
    assert response.status_code == 404
    assert _code(response) == "part_not_found"
    assert str(part) in response.json()["error"]["message"]
    assert "etag" not in response.headers


@pytest.mark.parametrize(
    ("path", "params"),
    [
        ("render/abc", {}),
        ("render/0", {"format": "bmp"}),
        ("render/0", {"size": 8}),
        ("render/0", {"size": 5000}),
        ("render/0", {"size": "big"}),
    ],
)
def test_render_rejects_invalid_parameters(
    client: TestClient, sample: Sample, path: str, params: dict[str, Any]
) -> None:
    doc_id = _upload_sample(client, sample, "pdf")
    response = client.get(f"/docs/{doc_id}/{path}", params=params)
    assert response.status_code == 422
    assert _code(response) == "validation_error"


def test_render_svg_of_unsupported_format(client: TestClient, sample: Sample) -> None:
    doc_id = _upload_sample(client, sample, "pdf")
    response = client.get(f"/docs/{doc_id}/render/0", params={"format": "svg"})
    assert response.status_code == 400
    assert _code(response) == "operation_not_supported"
    assert "png" in response.json()["error"]["message"]


def test_render_etag_and_conditional_request(client: TestClient, sample: Sample) -> None:
    doc_id = _upload_sample(client, sample, "pdf")
    url = f"/docs/{doc_id}/render/0"
    first = client.get(url, params={"size": 128})
    etag = first.headers["etag"]
    assert etag.startswith('"') and etag.endswith('"')
    assert client.get(url, params={"size": 128}).headers["etag"] == etag
    assert client.get(url, params={"size": 256}).headers["etag"] != etag
    assert client.get(f"/docs/{doc_id}/render/1", params={"size": 128}).headers["etag"] != etag

    for header in (etag, f"W/{etag}", f'"other", {etag}'):
        cached = client.get(url, params={"size": 128}, headers={"If-None-Match": header})
        assert cached.status_code == 304
        assert cached.content == b""
        assert cached.headers["etag"] == etag
    stale = client.get(url, params={"size": 128}, headers={"If-None-Match": '"stale"'})
    assert stale.status_code == 200
    assert stale.content == first.content


# -- errors shared by all viewing endpoints ---------------------------------------------

VIEW_PATHS = ("info", "parts", "text", "render", "render/0")


@pytest.mark.parametrize("path", VIEW_PATHS)
def test_unknown_document(client: TestClient, path: str) -> None:
    for doc_id in (MISSING, "not-an-id"):
        response = client.get(f"/docs/{doc_id}/{path}")
        assert response.status_code == 404
        assert _code(response) == "not_found"


@pytest.mark.parametrize("path", VIEW_PATHS)
def test_expired_document(client: TestClient, clock: Clock, path: str) -> None:
    doc_id = _upload(client, "a.txt", b"abc")["id"]
    clock.now += TTL + 1
    assert client.get(f"/docs/{doc_id}/{path}").status_code == 404


@pytest.mark.parametrize("path", VIEW_PATHS)
def test_viewing_renews_expiry(client: TestClient, clock: Clock, path: str) -> None:
    doc_id = _upload(client, "a.txt", b"abc")["id"]
    for _ in range(3):
        clock.now += TTL * 0.8
        assert client.get(f"/docs/{doc_id}/{path}").status_code == 200


@pytest.mark.parametrize("path", VIEW_PATHS)
def test_corrupt_document(client: TestClient, path: str) -> None:
    doc_id = _upload(client, "broken.pdf", b"%PDF-1.7\nthis is not a real pdf\n")["id"]
    response = client.get(f"/docs/{doc_id}/{path}")
    assert response.status_code == 422
    assert _code(response) == "corrupt_file"


@pytest.mark.parametrize("path", ("info", "parts", "text"))
def test_viewing_does_not_modify_the_document(
    client: TestClient, sample: Sample, path: str
) -> None:
    original = sample("docx").read_bytes()
    doc_id = _upload(client, "報告.docx", original)["id"]
    assert client.get(f"/docs/{doc_id}/{path}").status_code == 200
    assert client.get(f"/docs/{doc_id}/download").content == original


# -- Office documents (LibreOffice) -----------------------------------------------------


@pytest.mark.requires("libreoffice")
def test_render_office_document_and_cache(
    client: TestClient, store: SessionStore, sample: Sample
) -> None:
    original = sample("pptx").read_bytes()
    doc_id = _upload(client, "簡報.pptx", original)["id"]
    index = client.get(f"/docs/{doc_id}/render").json()
    assert (index["unit"], index["count"]) == ("page", 2)
    images = [client.get(url, params={"size": 320}) for url in index["urls"]]
    assert [r.status_code for r in images] == [200, 200]
    assert images[0].content != images[1].content
    assert all(max(_image(r.content).size) == 320 for r in images)
    missing = client.get(f"/docs/{doc_id}/render/2")
    assert (missing.status_code, _code(missing)) == (404, "part_not_found")

    # The PDF rendition is cached inside the session and never mistaken for the document.
    folder = store.get(doc_id).path.parent
    assert [p.suffix for p in (folder / CACHE_DIR_NAME).iterdir()] == [".pdf"]
    assert client.get(f"/docs/{doc_id}").json()["size"] == len(original)
    assert client.get(f"/docs/{doc_id}/download").content == original
    assert client.delete(f"/docs/{doc_id}").status_code == 204
    assert not folder.exists()


def test_render_office_without_libreoffice(
    client: TestClient, sample: Sample, monkeypatch: pytest.MonkeyPatch
) -> None:
    doc_id = _upload_sample(client, sample, "docx")
    monkeypatch.setenv("PATH", "")
    monkeypatch.delenv("ANYDOC_LIBREOFFICE", raising=False)
    for path in ("render", "render/0"):
        response = client.get(f"/docs/{doc_id}/{path}")
        assert response.status_code == 501
        assert _code(response) == "external_tool_missing"
    # Native reading still works without LibreOffice.
    assert client.get(f"/docs/{doc_id}/info").status_code == 200
    assert client.get(f"/docs/{doc_id}/text").json()["total"] > 0


# -- OpenAPI ----------------------------------------------------------------------------


def test_openapi_documents_viewing_endpoints(client: TestClient) -> None:
    schema = client.get("/openapi.json").json()
    paths = schema["paths"]
    for path in ("info", "parts", "text", "render", "render/{part}"):
        operation = paths[f"/docs/{{doc_id}}/{path}"]["get"]
        assert operation["tags"] == ["viewing"]
        assert "404" in operation["responses"]
    render = paths["/docs/{doc_id}/render/{part}"]["get"]
    assert set(render["responses"]["200"]["content"]) >= {"image/png", "image/svg+xml"}
    assert {"304", "400", "501"} <= set(render["responses"])
    params = {p["name"]: p for p in render["parameters"]}
    assert set(params) == {"doc_id", "part", "format", "size"}
    assert params["format"]["schema"]["enum"] == ["png", "jpeg", "jpg", "webp", "svg"]
    assert (params["size"]["schema"]["minimum"], params["size"]["schema"]["maximum"]) == (16, 4096)
    text = {p["name"] for p in paths["/docs/{doc_id}/text"]["get"]["parameters"]}
    assert text == {"doc_id", "offset", "limit", "format"}
    components = set(schema["components"]["schemas"])
    assert {"DocumentDetails", "PartList", "TextResponse", "RenderList"} <= components
