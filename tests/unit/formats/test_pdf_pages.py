"""Tests for PDF page operations (T040)."""

from __future__ import annotations

import io
from collections.abc import Callable
from pathlib import Path

import pytest
from pypdf import PdfReader, PdfWriter
from typer.testing import CliRunner

from anydoc.cli import app
from anydoc.core.errors import CorruptFileError, OperationNotSupportedError
from anydoc.core.model import Capability
from anydoc.formats.pdf import PdfHandler
from anydoc.formats.pdf import pages as ops
from anydoc.formats.pdf.pages import parse_page_spec

runner = CliRunner()
Sample = Callable[[str], Path]


def _numbered_pdf(n: int, sizes: list[tuple[float, float]] | None = None) -> bytes:
    """PDF whose page i (1-based) carries the text ``PAGE<i>`` and has MediaBox sizes."""
    from reportlab.pdfgen import canvas

    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=(200, 300))
    for i in range(1, n + 1):
        if sizes:
            c.setPageSize(sizes[(i - 1) % len(sizes)])
        c.setFont("Helvetica", 14)
        c.drawString(20, 100, f"PAGE{i}")
        c.showPage()
    c.save()
    return buf.getvalue()


def _labels(data: bytes) -> list[str]:
    reader = PdfReader(io.BytesIO(data))
    return [(p.extract_text() or "").strip() for p in reader.pages]


def _count(data: bytes) -> int:
    return len(PdfReader(io.BytesIO(data)).pages)


# -- parse_page_spec ------------------------------------------------------------------


def test_parse_page_spec() -> None:
    assert parse_page_spec("1,3-5", 6) == [0, 2, 3, 4]
    assert parse_page_spec("4-", 6) == [3, 4, 5]
    assert parse_page_spec("-2", 6) == [0, 1]
    assert parse_page_spec("all", 3) == [0, 1, 2]
    assert parse_page_spec("3,1,3", 3) == [2, 0, 2]


@pytest.mark.parametrize("spec", ["0", "7", "a", "3-1", "1,,2", "2-9"])
def test_parse_page_spec_invalid(spec: str) -> None:
    with pytest.raises(ValueError):
        parse_page_spec(spec, 6)


# -- operations -----------------------------------------------------------------------


def test_merge_preserves_order_and_chinese(sample: Sample) -> None:
    a = _numbered_pdf(2)
    b = sample("pdf").read_bytes()
    merged = ops.merge_pdfs([a, b])
    assert _count(merged) == 4
    assert _labels(merged)[:2] == ["PAGE1", "PAGE2"]
    assert any("hello world" in t for t in _labels(merged))


def test_merge_requires_input() -> None:
    with pytest.raises(ValueError):
        ops.merge_pdfs([])


def test_merge_corrupt_input() -> None:
    with pytest.raises(CorruptFileError):
        ops.merge_pdfs([_numbered_pdf(1), b"not a pdf"])


def test_extract_order_and_duplicates() -> None:
    out = ops.extract_pages(_numbered_pdf(4), [3, 0, 0])
    assert _labels(out) == ["PAGE4", "PAGE1", "PAGE1"]


def test_extract_bad_index_and_empty() -> None:
    with pytest.raises(IndexError):
        ops.extract_pages(_numbered_pdf(2), [2])
    with pytest.raises(ValueError):
        ops.extract_pages(_numbered_pdf(2), [])


def test_delete_pages() -> None:
    out = ops.delete_pages(_numbered_pdf(4), [1, 2])
    assert _labels(out) == ["PAGE1", "PAGE4"]
    with pytest.raises(ValueError):
        ops.delete_pages(_numbered_pdf(2), [0, 1])
    with pytest.raises(IndexError):
        ops.delete_pages(_numbered_pdf(2), [5])


def test_reorder_pages() -> None:
    out = ops.reorder_pages(_numbered_pdf(3), [2, 0, 1])
    assert _labels(out) == ["PAGE3", "PAGE1", "PAGE2"]
    with pytest.raises(ValueError):
        ops.reorder_pages(_numbered_pdf(3), [0, 1])
    with pytest.raises(ValueError):
        ops.reorder_pages(_numbered_pdf(3), [0, 0, 1])


def test_rotate_selected_and_cumulative() -> None:
    src = _numbered_pdf(3)
    once = ops.rotate_pages(src, 90, [0, 2])
    rot = [p.rotation for p in PdfReader(io.BytesIO(once)).pages]
    assert rot == [90, 0, 90]
    twice = ops.rotate_pages(once, 90, [0])
    assert [p.rotation for p in PdfReader(io.BytesIO(twice)).pages] == [180, 0, 90]
    neg = ops.rotate_pages(src, -90)
    assert [p.rotation for p in PdfReader(io.BytesIO(neg)).pages] == [270, 270, 270]
    full = ops.rotate_pages(src, 360)
    assert [p.rotation for p in PdfReader(io.BytesIO(full)).pages] == [0, 0, 0]
    with pytest.raises(ValueError):
        ops.rotate_pages(src, 45)


def test_insert_blank_page_positions_and_size() -> None:
    src = _numbered_pdf(2, sizes=[(200, 300), (400, 500)])
    front = ops.insert_blank_page(src, 0)
    assert _labels(front) == ["", "PAGE1", "PAGE2"]
    middle = ops.insert_blank_page(src, 1)
    assert _labels(middle) == ["PAGE1", "", "PAGE2"]
    end = ops.insert_blank_page(src, 2)
    assert _labels(end) == ["PAGE1", "PAGE2", ""]
    sizes = [
        (float(p.mediabox.width), float(p.mediabox.height))
        for p in PdfReader(io.BytesIO(end)).pages
    ]
    assert sizes[2] == (400.0, 500.0)  # appended page copies the last page's size
    custom = ops.insert_blank_page(src, 1, size=(100, 50))
    assert float(PdfReader(io.BytesIO(custom)).pages[1].mediabox.width) == 100.0
    with pytest.raises(IndexError):
        ops.insert_blank_page(src, 3)
    with pytest.raises(ValueError):
        ops.insert_blank_page(src, 0, size=(0, 10))


def test_insert_blank_into_empty_document_uses_a4() -> None:
    writer = PdfWriter()
    buf = io.BytesIO()
    writer.write(buf)
    out = ops.insert_blank_page(buf.getvalue(), 0)
    page = PdfReader(io.BytesIO(out)).pages[0]
    assert round(float(page.mediabox.width)) == 595


def test_split_chunks_and_ranges() -> None:
    src = _numbered_pdf(5)
    parts = ops.split_pdf(src, chunk_size=2)
    assert [_labels(p) for p in parts] == [["PAGE1", "PAGE2"], ["PAGE3", "PAGE4"], ["PAGE5"]]
    default = ops.split_pdf(src)
    assert len(default) == 5
    by_range = ops.split_pdf(src, ranges=[[0, 4], [1, 2, 3]])
    assert [_labels(p) for p in by_range] == [["PAGE1", "PAGE5"], ["PAGE2", "PAGE3", "PAGE4"]]
    with pytest.raises(ValueError):
        ops.split_pdf(src, chunk_size=0)


def test_encrypted_requires_password() -> None:
    writer = PdfWriter(clone_from=io.BytesIO(_numbered_pdf(2)))
    writer.encrypt("pw")
    buf = io.BytesIO()
    writer.write(buf)
    with pytest.raises(OperationNotSupportedError):
        ops.extract_pages(buf.getvalue(), [0])
    out = ops.extract_pages(buf.getvalue(), [1], password="pw")
    assert _labels(out) == ["PAGE2"]


# -- file helpers ---------------------------------------------------------------------


def test_merge_and_split_files(tmp_path: Path) -> None:
    a, b = tmp_path / "a.pdf", tmp_path / "b.pdf"
    a.write_bytes(_numbered_pdf(2))
    b.write_bytes(_numbered_pdf(1))
    merged = ops.merge_files([a, b], tmp_path / "m.pdf")
    assert _count(merged.read_bytes()) == 3
    files = ops.split_file(merged, tmp_path / "out", chunk_size=2)
    assert [f.name for f in files] == ["m-001.pdf", "m-002.pdf"]
    assert [_count(f.read_bytes()) for f in files] == [2, 1]


# -- PdfDocument API & round trip -----------------------------------------------------


def test_handler_declares_edit() -> None:
    assert Capability.EDIT in PdfHandler.capabilities


def test_document_edit_roundtrip(sample: Sample, tmp_path: Path) -> None:
    doc = PdfHandler().open(sample("pdf"))
    assert doc.page_count == 2
    doc.insert_blank_page(1)
    doc.rotate_pages(90, [0])
    doc.merge(_numbered_pdf(2))
    assert doc.page_count == 5
    assert doc.metadata.page_count == 5
    doc.reorder_pages([4, 0, 1, 2, 3])
    doc.delete_pages([2])  # the blank page
    out = doc.save(tmp_path / "edited.pdf")
    doc.close()

    reopened = PdfHandler().open(out)
    assert reopened.page_count == 4
    assert reopened.page_text(0) == "PAGE2"
    assert "hello world" in reopened.text()
    assert [p.rotation for p in reopened.pages()] == [0, 90, 0, 0]
    assert reopened.render_page(0)[:4] == b"\x89PNG"
    reopened.close()


def test_document_extract_and_split(sample: Sample) -> None:
    doc = PdfHandler().open(sample("pdf"))
    assert _count(doc.extract_pages([1])) == 1
    assert [_count(b) for b in doc.split(chunk_size=1)] == [1, 1]
    assert doc.page_count == 2  # unchanged
    doc.close()


def test_document_merge_accepts_documents_and_paths(tmp_path: Path) -> None:
    p = tmp_path / "x.pdf"
    p.write_bytes(_numbered_pdf(1))
    h = PdfHandler()
    doc = h.open(_numbered_pdf(1))
    doc.merge(h.open(_numbered_pdf(2)), p)
    assert doc.page_count == 4
    doc.close()


def test_document_edit_refused_when_locked_or_encrypted() -> None:
    writer = PdfWriter(clone_from=io.BytesIO(_numbered_pdf(2)))
    writer.encrypt("pw")
    buf = io.BytesIO()
    writer.write(buf)
    locked = PdfHandler().open(buf.getvalue())
    with pytest.raises(OperationNotSupportedError):
        locked.delete_pages([0])
    unlocked = PdfHandler().open(buf.getvalue(), password="pw")
    with pytest.raises(OperationNotSupportedError):
        unlocked.delete_pages([0])


# -- CLI ------------------------------------------------------------------------------


def _write(tmp_path: Path, name: str, n: int) -> Path:
    p = tmp_path / name
    p.write_bytes(_numbered_pdf(n))
    return p


def test_cli_merge_extract_delete(tmp_path: Path) -> None:
    a, b = _write(tmp_path, "a.pdf", 3), _write(tmp_path, "b.pdf", 2)
    out = tmp_path / "m.pdf"
    r = runner.invoke(app, ["pdf", "merge", str(a), str(b), "-o", str(out)])
    assert r.exit_code == 0, r.output
    assert _count(out.read_bytes()) == 5

    ex = tmp_path / "ex.pdf"
    r = runner.invoke(app, ["pdf", "extract", str(out), "1,4-", "-o", str(ex)])
    assert r.exit_code == 0, r.output
    assert _labels(ex.read_bytes()) == ["PAGE1", "PAGE1", "PAGE2"]

    de = tmp_path / "de.pdf"
    r = runner.invoke(app, ["pdf", "delete", str(a), "2", "-o", str(de)])
    assert r.exit_code == 0
    assert _labels(de.read_bytes()) == ["PAGE1", "PAGE3"]


def test_cli_rotate_reorder_insert_split(tmp_path: Path) -> None:
    a = _write(tmp_path, "a.pdf", 3)
    out = tmp_path / "o.pdf"
    r = runner.invoke(app, ["pdf", "rotate", str(a), "-o", str(out), "-a", "180", "-p", "2"])
    assert r.exit_code == 0, r.output
    assert [p.rotation for p in PdfReader(str(out)).pages] == [0, 180, 0]

    r = runner.invoke(app, ["pdf", "reorder", str(a), "3,1,2", "-o", str(out)])
    assert r.exit_code == 0, r.output
    assert _labels(out.read_bytes()) == ["PAGE3", "PAGE1", "PAGE2"]

    r = runner.invoke(app, ["pdf", "insert-blank", str(a), "-o", str(out), "-b", "2"])
    assert r.exit_code == 0, r.output
    assert _labels(out.read_bytes()) == ["PAGE1", "", "PAGE2", "PAGE3"]
    r = runner.invoke(app, ["pdf", "insert-blank", str(a), "-o", str(out)])
    assert _labels(out.read_bytes())[-1] == ""

    r = runner.invoke(app, ["pdf", "split", str(a), "-d", str(tmp_path / "s"), "-n", "2"])
    assert r.exit_code == 0, r.output
    assert len(r.output.split()) == 2


def test_cli_errors(tmp_path: Path) -> None:
    a = _write(tmp_path, "a.pdf", 2)
    out = tmp_path / "o.pdf"
    missing = tmp_path / "none.pdf"
    r = runner.invoke(app, ["pdf", "extract", str(missing), "1", "-o", str(out)])
    assert r.exit_code == 2
    r = runner.invoke(app, ["pdf", "extract", str(a), "9", "-o", str(out)])
    assert r.exit_code == 1 and "out of bounds" in r.output
    r = runner.invoke(app, ["pdf", "delete", str(a), "1-2", "-o", str(out)])
    assert r.exit_code == 1
    r = runner.invoke(app, ["pdf", "reorder", str(a), "1", "-o", str(out)])
    assert r.exit_code == 1
    r = runner.invoke(app, ["pdf", "merge", str(a), str(missing), "-o", str(out)])
    assert r.exit_code == 2
    bad = tmp_path / "bad.pdf"
    bad.write_bytes(b"junk")
    r = runner.invoke(app, ["pdf", "extract", str(bad), "1", "-o", str(out)])
    assert r.exit_code == 1
    r = runner.invoke(app, ["pdf", "split", str(a), "-d", str(tmp_path / "s"), "-n", "0"])
    assert r.exit_code == 1
    r = runner.invoke(app, ["pdf", "insert-blank", str(a), "-o", str(out), "-b", "9"])
    assert r.exit_code == 1
