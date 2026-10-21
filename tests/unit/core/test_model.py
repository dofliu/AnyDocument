import json
from datetime import UTC, datetime
from pathlib import Path

from anydoc.core import Capability, Document, Metadata, Part


def test_capability_values() -> None:
    assert Capability.READ == "read"
    assert Capability("extract_text") is Capability.EXTRACT_TEXT
    assert {c.value for c in Capability} >= {"read", "write", "render", "edit", "thumbnail"}


def test_metadata_defaults() -> None:
    meta = Metadata(format="pdf", mime="application/pdf")
    assert meta.title is None and meta.page_count is None
    assert meta.extra == {}
    assert Metadata(format="a", mime="b").extra is not meta.extra


def test_metadata_dict_roundtrip() -> None:
    meta = Metadata(
        format="docx",
        mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        title="測試文件",
        author="王小明",
        created=datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC),
        modified=datetime(2026, 2, 3, 4, 5, 6),
        page_count=3,
        extra={"keywords": ["甲", "乙"]},
    )
    data = meta.to_dict()
    assert data["created"] == "2026-01-02T03:04:05+00:00"
    assert Metadata.from_dict(data) == meta


def test_metadata_json_keeps_chinese_and_roundtrips() -> None:
    meta = Metadata(format="txt", mime="text/plain", title="標題")
    text = meta.to_json()
    assert "標題" in text
    assert Metadata.from_dict(json.loads(text)) == meta


def test_metadata_from_minimal_dict() -> None:
    meta = Metadata.from_dict({"format": "csv", "mime": "text/csv"})
    assert meta.created is None and meta.extra == {}


def test_part_dict_roundtrip() -> None:
    part = Part(kind="page", index=0, name="第一頁", size=(595.0, 842.0))
    data = part.to_dict()
    assert data["size"] == [595.0, 842.0]
    assert Part.from_dict(json.loads(json.dumps(data))) == part


def test_part_without_size() -> None:
    part = Part(kind="sheet", index=1)
    assert part.to_dict()["size"] is None
    assert Part.from_dict(part.to_dict()) == part


class _Dummy:
    def __init__(self) -> None:
        self.path: Path | None = None
        self.format = "txt"
        self.metadata = Metadata(format="txt", mime="text/plain")

    def parts(self) -> list[Part]:
        return [Part(kind="page", index=0)]

    def text(self) -> str:
        return "hello"

    def save(self, path: Path | None = None) -> Path:
        return path or Path("x")

    def close(self) -> None:
        return None


def test_document_protocol_is_structural() -> None:
    doc = _Dummy()
    assert isinstance(doc, Document)
    assert not isinstance(object(), Document)
