"""Integration tests (T101): view every sample format through the API.

Each sample is uploaded, then its info, parts and text are compared with what the
format handler reports for the original file, and every render unit is rendered.
"""

from __future__ import annotations

import io
from collections.abc import Callable
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image

import anydoc
from anydoc.converters.office_pdf import OFFICE_PDF_SOURCES
from anydoc.external import is_available
from tests.fixtures.generate import SAMPLES

EXPECTED_FORMAT = {"csv-bom": "csv", "csv-big5": "csv", "jpg": "jpeg"}
EXPECTED_UNITS = {"pdf": 2, "dxf": 2, "pptx": 2}


@pytest.mark.parametrize("sample_id", sorted(SAMPLES))
def test_view_every_sample(
    client: TestClient, sample: Callable[[str], Path], sample_id: str
) -> None:
    source = sample(sample_id)
    fmt = EXPECTED_FORMAT.get(sample_id, sample_id)
    doc = anydoc.open(source)
    try:
        metadata = doc.metadata.to_dict()
        parts = [p.to_dict() for p in doc.parts()]
        text = doc.text()
    finally:
        doc.close()

    uploaded = client.post("/docs", files={"file": (f"樣本-{source.name}", source.read_bytes())})
    assert uploaded.status_code == 201, uploaded.text
    base = f"/docs/{uploaded.json()['id']}"

    info = client.get(f"{base}/info")
    assert info.status_code == 200, info.text
    assert info.json()["format"] == fmt
    assert info.json()["metadata"] == metadata
    assert info.json()["part_count"] == len(parts)

    listed = client.get(f"{base}/parts")
    assert listed.status_code == 200, listed.text
    assert listed.json()["parts"] == parts
    assert listed.json()["count"] == len(parts)

    extracted = client.get(f"{base}/text")
    assert extracted.status_code == 200, extracted.text
    assert extracted.json()["text"] == text
    assert extracted.json()["total"] == len(text)
    plain = client.get(f"{base}/text", params={"format": "plain"})
    assert plain.content.decode("utf-8") == text

    if fmt in OFFICE_PDF_SOURCES and not is_available("libreoffice"):
        assert client.get(f"{base}/render").status_code == 501
        pytest.skip("external tool not available: libreoffice")

    index = client.get(f"{base}/render")
    assert index.status_code == 200, index.text
    body = index.json()
    assert body["unit"] == info.json()["render"]["unit"]
    assert body["formats"] == info.json()["render"]["formats"]
    assert body["count"] >= 1
    assert body["count"] == EXPECTED_UNITS.get(sample_id, body["count"])
    assert len(body["urls"]) == body["count"]
    for url in body["urls"]:
        rendered = client.get(url, params={"size": 240})
        assert rendered.status_code == 200, rendered.text
        assert rendered.headers["content-type"] == "image/png"
        image = Image.open(io.BytesIO(rendered.content))
        image.load()
        assert image.format == "PNG"
        assert 1 <= max(image.size) <= 240
    beyond = client.get(f"{base}/render/{body['count']}")
    assert beyond.status_code == 404
    assert beyond.json()["error"]["code"] == "part_not_found"
