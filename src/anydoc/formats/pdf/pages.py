"""Page-level PDF operations (merge, split, extract, rotate, reorder, delete, insert blank).

All functions take and return PDF ``bytes`` so they compose freely; the file-based helpers
(:func:`merge_files`, :func:`split_file`) and ``PdfDocument`` methods build on them.
Page indexes are 0-based in the Python API; :func:`parse_page_spec` converts the 1-based
specs used on the command line (``"1,3-5,8-"``).
"""

from __future__ import annotations

import io
from collections.abc import Iterable, Sequence
from pathlib import Path

from pypdf import PdfReader, PdfWriter

from anydoc.core.errors import CorruptFileError, OperationNotSupportedError

__all__ = [
    "delete_pages",
    "extract_pages",
    "insert_blank_page",
    "merge_files",
    "merge_pdfs",
    "parse_page_spec",
    "reorder_pages",
    "rotate_pages",
    "split_file",
    "split_pdf",
]


def _reader(data: bytes, password: str = "") -> PdfReader:
    try:
        reader = PdfReader(io.BytesIO(data))
        if reader.is_encrypted and not reader.decrypt(password):
            raise OperationNotSupportedError("PDF is encrypted; a valid password is required")
        len(reader.pages)  # force parsing of the page tree
    except OperationNotSupportedError:
        raise
    except Exception as exc:
        raise CorruptFileError(f"Invalid PDF: {exc}") from exc
    return reader


def _dump(writer: PdfWriter) -> bytes:
    buf = io.BytesIO()
    writer.write(buf)
    return buf.getvalue()


def _check(indexes: Iterable[int], count: int) -> list[int]:
    out = list(indexes)
    for i in out:
        if not 0 <= i < count:
            raise IndexError(f"No such page: {i} (document has {count} pages)")
    return out


def parse_page_spec(spec: str, page_count: int) -> list[int]:
    """Parse a 1-based page spec such as ``"1,3-5,8-"`` into 0-based indexes.

    Open ranges are allowed (``"3-"`` = page 3 to the end, ``"-2"`` = first two pages);
    ``"all"`` selects every page. Order and duplicates are preserved.

    Raises:
        ValueError: on malformed specs or pages outside ``1..page_count``.
    """
    spec = spec.strip().lower()
    if spec in ("", "all"):
        return list(range(page_count))
    out: list[int] = []
    for token in spec.split(","):
        token = token.strip()
        try:
            if "-" in token:
                lo_s, hi_s = token.split("-", 1)
                lo = int(lo_s) if lo_s.strip() else 1
                hi = int(hi_s) if hi_s.strip() else page_count
            else:
                lo = hi = int(token)
        except ValueError:
            raise ValueError(f"Invalid page spec: {token!r}") from None
        if lo < 1 or hi > page_count or lo > hi:
            raise ValueError(f"Page range {token!r} out of bounds (1-{page_count})")
        out.extend(range(lo - 1, hi))
    return out


def merge_pdfs(sources: Sequence[bytes], *, passwords: Sequence[str] = ()) -> bytes:
    """Concatenate PDFs (in order) into one document."""
    if not sources:
        raise ValueError("Nothing to merge")
    writer = PdfWriter()
    for n, data in enumerate(sources):
        reader = _reader(data, passwords[n] if n < len(passwords) else "")
        for page in reader.pages:
            writer.add_page(page)
    return _dump(writer)


def extract_pages(data: bytes, pages: Sequence[int], *, password: str = "") -> bytes:
    """Return a new PDF made of the given 0-based pages, in the given order."""
    reader = _reader(data, password)
    indexes = _check(pages, len(reader.pages))
    if not indexes:
        raise ValueError("No pages selected")
    writer = PdfWriter()
    for i in indexes:
        writer.add_page(reader.pages[i])
    return _dump(writer)


def delete_pages(data: bytes, pages: Iterable[int], *, password: str = "") -> bytes:
    """Return the PDF without the given 0-based pages (at least one page must remain)."""
    reader = _reader(data, password)
    drop = set(_check(pages, len(reader.pages)))
    keep = [i for i in range(len(reader.pages)) if i not in drop]
    if not keep:
        raise ValueError("Cannot delete every page")
    return extract_pages(data, keep, password=password)


def reorder_pages(data: bytes, order: Sequence[int], *, password: str = "") -> bytes:
    """Reorder pages; ``order`` must be a permutation of ``range(page_count)``."""
    reader = _reader(data, password)
    n = len(reader.pages)
    if sorted(order) != list(range(n)):
        raise ValueError(f"order must be a permutation of 0..{n - 1}")
    return extract_pages(data, order, password=password)


def rotate_pages(
    data: bytes,
    angle: int,
    pages: Iterable[int] | None = None,
    *,
    password: str = "",
) -> bytes:
    """Rotate pages clockwise by ``angle`` (a multiple of 90, may be negative).

    ``pages`` are 0-based; ``None`` rotates every page. Rotation is added to any
    existing page rotation.
    """
    if angle % 90 != 0:
        raise ValueError("angle must be a multiple of 90")
    reader = _reader(data, password)
    n = len(reader.pages)
    targets = set(range(n) if pages is None else _check(pages, n))
    writer = PdfWriter()
    for i, page in enumerate(reader.pages):
        added = writer.add_page(page)
        if i in targets and angle % 360:
            added.rotate(angle % 360)
    return _dump(writer)


def insert_blank_page(
    data: bytes,
    index: int,
    *,
    size: tuple[float, float] | None = None,
    password: str = "",
) -> bytes:
    """Insert a blank page before 0-based ``index`` (``index == page_count`` appends).

    ``size`` is ``(width, height)`` in points; by default the size of the neighbouring page
    is used (A4 for an empty document).
    """
    reader = _reader(data, password)
    n = len(reader.pages)
    if not 0 <= index <= n:
        raise IndexError(f"Cannot insert at {index} (document has {n} pages)")
    if size is None:
        if n:
            ref = reader.pages[min(index, n - 1)]
            size = (float(ref.mediabox.width), float(ref.mediabox.height))
        else:
            size = (595.276, 841.89)
    if size[0] <= 0 or size[1] <= 0:
        raise ValueError("size must be positive")
    writer = PdfWriter()
    for i, page in enumerate(reader.pages):
        if i == index:
            writer.add_blank_page(width=size[0], height=size[1])
        writer.add_page(page)
    if index == n:
        writer.add_blank_page(width=size[0], height=size[1])
    return _dump(writer)


def split_pdf(
    data: bytes,
    *,
    chunk_size: int | None = None,
    ranges: Sequence[Sequence[int]] | None = None,
    password: str = "",
) -> list[bytes]:
    """Split a PDF into several documents.

    Give either ``chunk_size`` (pages per output; default 1) or ``ranges`` (a list of
    0-based page lists, one per output).
    """
    reader = _reader(data, password)
    n = len(reader.pages)
    if ranges is not None:
        groups = [list(r) for r in ranges]
    else:
        size = 1 if chunk_size is None else chunk_size
        if size < 1:
            raise ValueError("chunk_size must be >= 1")
        groups = [list(range(s, min(s + size, n))) for s in range(0, n, size)]
    return [extract_pages(data, g, password=password) for g in groups]


def merge_files(sources: Sequence[Path], target: Path) -> Path:
    """Merge PDF files in order into ``target`` and return it."""
    target.write_bytes(merge_pdfs([Path(p).read_bytes() for p in sources]))
    return target


def split_file(
    source: Path,
    out_dir: Path,
    *,
    chunk_size: int | None = None,
    ranges: Sequence[Sequence[int]] | None = None,
) -> list[Path]:
    """Split ``source`` into ``out_dir/<stem>-001.pdf`` ... and return the paths."""
    out_dir.mkdir(parents=True, exist_ok=True)
    parts = split_pdf(source.read_bytes(), chunk_size=chunk_size, ranges=ranges)
    paths: list[Path] = []
    for n, blob in enumerate(parts, 1):
        p = out_dir / f"{source.stem}-{n:03d}.pdf"
        p.write_bytes(blob)
        paths.append(p)
    return paths
