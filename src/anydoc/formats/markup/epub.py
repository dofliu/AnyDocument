"""EPUB packages: OPF metadata, spine order and chapter text."""

from __future__ import annotations

import io
import posixpath
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import ClassVar
from urllib.parse import unquote
from xml.etree.ElementTree import Element

from defusedxml import DefusedXmlException
from defusedxml import ElementTree as SafeET

from anydoc.core.errors import CorruptFileError, SecurityLimitError
from anydoc.core.model import Capability, Document, Metadata, Part
from anydoc.core.registry import FormatHandler
from anydoc.formats.markup._base import read_source, save_raw
from anydoc.formats.markup.html import decode_html, parse_html

_CONTAINER_NS = "{urn:oasis:names:tc:opendocument:xmlns:container}"
_OPF_NS = "{http://www.idpf.org/2007/opf}"
_DC_NS = "{http://purl.org/dc/elements/1.1/}"
_MAX_ENTRY_BYTES = 128 * 1024 * 1024
_HTML_TYPES = ("application/xhtml+xml", "text/html")


@dataclass(frozen=True)
class Chapter:
    """One spine item of the book."""

    index: int
    href: str
    title: str | None
    text: str


class EpubDocument:
    """A read-only EPUB document."""

    def __init__(self, raw: bytes, *, path: Path | None = None) -> None:
        self.path = path
        self.format = "epub"
        self._raw = raw
        try:
            zf = zipfile.ZipFile(io.BytesIO(raw))
        except zipfile.BadZipFile as exc:
            raise CorruptFileError(f"Invalid EPUB package: {exc}") from exc
        with zf:
            self._load(zf)

    def _read(self, zf: zipfile.ZipFile, name: str) -> bytes:
        try:
            info = zf.getinfo(name)
        except KeyError:
            raise CorruptFileError(f"EPUB entry missing: {name}") from None
        if info.file_size > _MAX_ENTRY_BYTES:
            raise SecurityLimitError(f"EPUB entry too large: {name}")
        return zf.read(info)

    def _xml(self, zf: zipfile.ZipFile, name: str) -> Element:
        try:
            return SafeET.fromstring(self._read(zf, name))
        except (SafeET.ParseError, DefusedXmlException) as exc:
            raise CorruptFileError(f"Invalid XML in {name}: {exc}") from exc

    def _load(self, zf: zipfile.ZipFile) -> None:
        container = self._xml(zf, "META-INF/container.xml")
        rootfile = container.find(f".//{_CONTAINER_NS}rootfile")
        opf_path = rootfile.get("full-path") if rootfile is not None else None
        if not opf_path:
            raise CorruptFileError("EPUB container.xml has no rootfile")
        opf = self._xml(zf, opf_path)
        base = posixpath.dirname(opf_path)

        meta = opf.find(f"{_OPF_NS}metadata")

        def dc(tag: str) -> list[str]:
            if meta is None:
                return []
            return [(e.text or "").strip() for e in meta.findall(f"{_DC_NS}{tag}") if e.text]

        manifest = {
            item.get("id", ""): (item.get("href", ""), item.get("media-type", ""))
            for item in opf.findall(f"{_OPF_NS}manifest/{_OPF_NS}item")
        }
        self.chapters: list[Chapter] = []
        names = set(zf.namelist())
        for ref in opf.findall(f"{_OPF_NS}spine/{_OPF_NS}itemref"):
            href, media = manifest.get(ref.get("idref", ""), ("", ""))
            if not href or media not in _HTML_TYPES:
                continue
            member = posixpath.normpath(posixpath.join(base, unquote(href)))
            if member not in names:
                continue
            html, _ = decode_html(self._read(zf, member))
            parsed = parse_html(html)
            title = parsed.headings[0].text if parsed.headings else (parsed.title or None)
            self.chapters.append(Chapter(len(self.chapters), href, title, parsed.text))
        if not self.chapters:
            raise CorruptFileError("EPUB spine has no readable chapters")

        titles, creators = dc("title"), dc("creator")
        self.metadata = Metadata(
            format="epub",
            mime="application/epub+zip",
            title=titles[0] if titles else None,
            author=", ".join(creators) or None,
            page_count=len(self.chapters),
            extra={
                "language": next(iter(dc("language")), None),
                "identifier": next(iter(dc("identifier")), None),
                "publisher": next(iter(dc("publisher")), None),
                "version": opf.get("version"),
                "chapters": len(self.chapters),
            },
        )

    def parts(self) -> list[Part]:
        """Return one ``chapter`` part per spine item."""
        return [Part(kind="chapter", index=c.index, name=c.title) for c in self.chapters]

    def text(self) -> str:
        """Return all chapter text in spine order."""
        return "\n\n".join(c.text for c in self.chapters if c.text)

    def save(self, path: Path | None = None) -> Path:
        """Write the original package bytes to ``path`` (default: the source path)."""
        return save_raw(self._raw, self.path, path)

    def close(self) -> None:
        """Release resources (nothing to close)."""


class EpubHandler(FormatHandler):
    """Handler for EPUB e-books (``.epub``)."""

    format_id: ClassVar[str] = "epub"
    extensions: ClassVar[tuple[str, ...]] = ("epub",)
    mime_types: ClassVar[tuple[str, ...]] = ("application/epub+zip",)
    capabilities: ClassVar[frozenset[Capability]] = frozenset(
        {Capability.READ, Capability.EXTRACT_TEXT}
    )

    @classmethod
    def sniff(cls, head: bytes, path: Path | None) -> float:
        """Return confidence from the uncompressed ``mimetype`` entry or the extension."""
        if b"application/epub+zip" in head[:100]:
            return 0.95
        if path is not None and path.suffix.lower() == ".epub":
            return 0.7
        return 0.0

    def open(self, source: Path | bytes) -> Document:
        """Open an EPUB; broken packages raise ``CorruptFileError``."""
        data, path = read_source(source)
        return EpubDocument(data, path=path)
