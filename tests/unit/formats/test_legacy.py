"""Tests for legacy DOC/XLS/PPT reading via LibreOffice (T017)."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest

import anydoc
from anydoc.core.errors import CorruptFileError, ExternalToolMissingError
from anydoc.core.model import Capability
from anydoc.core.registry import registry
from anydoc.external import convert_with_libreoffice
from anydoc.external.tools import ExternalToolError
from anydoc.formats.legacy import DocHandler, LegacyHandler, PptHandler, XlsHandler
from tests.fixtures.generate import BODY, TABLE_ROWS, TITLE

Sample = Callable[[str], Path]
HANDLERS = (DocHandler, XlsHandler, PptHandler)


@pytest.fixture(autouse=True)
def _handlers() -> None:
    for h in HANDLERS:
        registry.register(h, replace=True)


@pytest.fixture(scope="module")
def legacy_files(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Path]:
    """Create real .doc/.xls/.ppt files by converting generated OOXML samples."""
    from tests.fixtures.generate import generate_all

    samples = generate_all()
    out = tmp_path_factory.mktemp("legacy")
    files: dict[str, Path] = {}
    for new, old in (("doc", "docx"), ("xls", "xlsx"), ("ppt", "pptx")):
        files[new] = convert_with_libreoffice(samples[old], new, out)
    return files


# -- no external tool needed -------------------------------------------------


def test_handler_metadata() -> None:
    assert DocHandler.extensions == ("doc",)
    assert XlsHandler.format_id == "xls"
    assert PptHandler.target_format == "pptx"
    for h in HANDLERS:
        assert issubclass(h, LegacyHandler)
        assert Capability.READ in h.capabilities
        assert Capability.EXTRACT_TEXT in h.capabilities


def test_sniff() -> None:
    ole = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\0" * 8
    assert DocHandler.sniff(ole, Path("a.doc")) == 0.9
    assert DocHandler.sniff(b"x", Path("a.doc")) == 0.5
    assert DocHandler.sniff(ole, Path("a.xls")) == 0.0
    assert DocHandler.sniff(ole, None) == 0.0


def test_entry_points_registered() -> None:
    from anydoc.core.registry import HandlerRegistry

    reg = HandlerRegistry()
    loaded = reg.load_entry_points()
    assert {"doc", "xls", "ppt"} <= set(loaded)


@pytest.mark.parametrize("handler", HANDLERS)
def test_missing_libreoffice_raises(
    handler: type[LegacyHandler], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ANYDOC_LIBREOFFICE", "definitely-not-installed-xyz")
    f = tmp_path / f"a.{handler.format_id}"
    f.write_bytes(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1")
    with pytest.raises(ExternalToolMissingError) as ei:
        handler().open(f)
    assert ei.value.tool == "libreoffice"


def test_missing_libreoffice_via_anydoc_open(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ANYDOC_LIBREOFFICE", "definitely-not-installed-xyz")
    f = tmp_path / "a.doc"
    f.write_bytes(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + "WordDocument".encode("utf-16-le"))
    with pytest.raises(ExternalToolMissingError):
        anydoc.open(f)


def test_convert_failure_becomes_corrupt(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    import anydoc.formats.legacy.handler as mod

    monkeypatch.setattr(mod, "require_tool", lambda _t: Path("/bin/true"))

    def boom(*_a: object, **_k: object) -> Path:
        raise ExternalToolError("conversion failed")

    monkeypatch.setattr(mod, "convert_with_libreoffice", boom)
    with pytest.raises(CorruptFileError, match="DOC"):
        DocHandler().open(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1junk")


def test_unreadable_path_becomes_corrupt(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    import anydoc.formats.legacy.handler as mod

    monkeypatch.setattr(mod, "require_tool", lambda _t: Path("/bin/true"))
    with pytest.raises(CorruptFileError, match="Cannot read"):
        DocHandler().open(tmp_path / "missing.doc")


def test_convert_helper_no_output(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    import anydoc.external.libreoffice as lo
    from anydoc.external.tools import ToolResult

    monkeypatch.setattr(lo, "run_tool", lambda *a, **k: ToolResult((), 0, "", ""))
    src = tmp_path / "a.doc"
    src.write_bytes(b"x")
    with pytest.raises(ExternalToolError, match="no .docx output"):
        convert_with_libreoffice(src, ".docx", tmp_path)


# -- real LibreOffice --------------------------------------------------------


@pytest.mark.requires("libreoffice")
def test_detect_legacy_files(legacy_files: dict[str, Path]) -> None:
    for fmt, path in legacy_files.items():
        assert anydoc.detect(path).format == fmt


@pytest.mark.requires("libreoffice")
def test_open_doc(legacy_files: dict[str, Path]) -> None:
    doc = anydoc.open(legacy_files["doc"])
    assert doc.metadata.format == "doc"
    assert doc.metadata.mime == "application/msword"
    assert doc.metadata.extra["converted_to"] == "docx"
    text = doc.text()
    assert TITLE in text
    assert BODY in text
    assert TABLE_ROWS[0][0] in text
    assert doc.parts()
    with pytest.raises(ValueError):
        doc.save()  # type: ignore[attr-defined]


@pytest.mark.requires("libreoffice")
def test_open_xls_from_bytes(legacy_files: dict[str, Path]) -> None:
    doc = XlsHandler().open(legacy_files["xls"].read_bytes())
    assert doc.metadata.format == "xls"
    assert doc.format == "xls"
    assert TABLE_ROWS[0][0] in doc.text()
    assert len(doc.parts()) == 2


@pytest.mark.requires("libreoffice")
def test_open_ppt(legacy_files: dict[str, Path]) -> None:
    doc = anydoc.open(legacy_files["ppt"])
    assert doc.metadata.format == "ppt"
    assert doc.metadata.page_count == 2
    assert TITLE in doc.text()


@pytest.mark.requires("libreoffice")
def test_roundtrip_content_matches_ooxml(sample: Sample, legacy_files: dict[str, Path]) -> None:
    """Every word of the OOXML original's text survives the legacy round trip."""
    for new, old in (("doc", "docx"), ("xls", "xlsx"), ("ppt", "pptx")):
        original = anydoc.open(sample(old)).text()
        legacy = anydoc.open(legacy_files[new]).text()
        missing = [w for w in original.split() if w not in legacy]
        assert not missing, (new, missing)


@pytest.mark.requires("libreoffice")
def test_source_file_untouched(legacy_files: dict[str, Path]) -> None:
    before = legacy_files["doc"].read_bytes()
    anydoc.open(legacy_files["doc"]).close()
    assert legacy_files["doc"].read_bytes() == before


def test_non_ole_is_corrupt_without_conversion(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import anydoc.formats.legacy.handler as mod

    monkeypatch.setattr(mod, "require_tool", lambda _t: Path("/bin/true"))
    f = tmp_path / "bad.doc"
    f.write_bytes(b"not an office file at all")
    with pytest.raises(CorruptFileError, match="OLE2"):
        DocHandler().open(f)


@pytest.mark.requires("libreoffice")
def test_truncated_ole_is_corrupt(tmp_path: Path) -> None:
    f = tmp_path / "bad.doc"
    f.write_bytes(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\0" * 64)
    with pytest.raises(CorruptFileError):
        DocHandler().open(f)
