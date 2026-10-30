"""HTML documents parsed with the standard library ``html.parser``."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from html.parser import HTMLParser
from pathlib import Path
from typing import ClassVar

from anydoc.core.errors import CorruptFileError
from anydoc.core.model import Capability, Document, Metadata, Part
from anydoc.core.registry import FormatHandler
from anydoc.formats.csv.parse import decode_bytes
from anydoc.formats.markup._base import read_source, save_raw

_BLOCK_TAGS = frozenset(
    {
        *("address", "article", "aside", "blockquote", "body", "dd", "div", "dl", "dt"),
        *("fieldset", "figcaption", "figure", "footer", "form", "h1", "h2", "h3", "h4"),
        *("h5", "h6", "header", "hr", "li", "main", "nav", "ol", "p", "pre", "section"),
        *("table", "tr", "ul"),
    }
)
_SKIP_TAGS = frozenset({"script", "style", "template", "noscript", "head", "title"})
_VOID_TAGS = frozenset({"br", "hr", "img", "meta", "link", "input", "area", "base", "col", "wbr"})
_HEADINGS = frozenset({"h1", "h2", "h3", "h4", "h5", "h6"})
_CHARSET_RE = re.compile(rb"""<meta[^>]+charset\s*=\s*["']?\s*([\w.:-]+)""", re.IGNORECASE)
_WS_RE = re.compile(r"[ \t\r\f\v\n]+")


@dataclass(frozen=True)
class Heading:
    """A heading element."""

    level: int
    text: str


@dataclass(frozen=True)
class Link:
    """An anchor with an ``href``."""

    href: str
    text: str


@dataclass(frozen=True)
class HtmlImage:
    """An ``<img>`` reference."""

    src: str
    alt: str


@dataclass
class _Parsed:
    title: str = ""
    lang: str | None = None
    meta: dict[str, str] = field(default_factory=dict)
    headings: list[Heading] = field(default_factory=list)
    links: list[Link] = field(default_factory=list)
    images: list[HtmlImage] = field(default_factory=list)
    tables: list[list[list[str]]] = field(default_factory=list)
    text: str = ""


class _Extractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.out = _Parsed()
        self._chunks: list[str] = []
        self._skip = 0
        self._in_title = False
        self._pre = 0
        self._heading: tuple[int, list[str]] | None = None
        self._link: tuple[str, list[str]] | None = None
        self._table_stack: list[list[list[str]]] = []
        self._row: list[str] | None = None
        self._cell: list[str] | None = None

    def _newline(self) -> None:
        if self._chunks and not self._chunks[-1].endswith("\n"):
            self._chunks.append("\n")

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        a = {k: v or "" for k, v in attrs}
        if tag == "html" and "lang" in a:
            self.out.lang = a["lang"]
        if tag == "title":
            self._in_title = True
        if tag == "meta" and a.get("name") and "content" in a:
            self.out.meta[a["name"].lower()] = a["content"]
        if tag in _SKIP_TAGS:
            if tag not in _VOID_TAGS and tag not in ("head", "title"):
                self._skip += 1
            return
        if tag == "img":
            self.out.images.append(HtmlImage(a.get("src", ""), a.get("alt", "")))
        if tag == "a" and "href" in a:
            self._link = (a["href"], [])
        if tag in _HEADINGS:
            self._heading = (int(tag[1]), [])
        if tag == "pre":
            self._pre += 1
        if tag == "table":
            self._table_stack.append([])
        elif tag == "tr" and self._table_stack:
            self._row = []
        elif tag in ("td", "th") and self._row is not None:
            self._cell = []
        if tag == "br":
            self._emit("\n")
        elif tag == "li":
            self._newline()
            self._chunks.append("- ")
        elif tag in ("td", "th") and self._chunks and not self._chunks[-1].endswith(("\n", "\t")):
            self._chunks.append("\t")
        elif tag in _BLOCK_TAGS:
            self._newline()

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)
        if tag not in _VOID_TAGS:
            self.handle_endtag(tag)

    def handle_endtag(self, tag: str) -> None:
        if tag == "title":
            self._in_title = False
        if tag in _SKIP_TAGS:
            if tag not in ("head", "title") and self._skip:
                self._skip -= 1
            return
        if tag == "a" and self._link is not None:
            href, parts = self._link
            self.out.links.append(Link(href, _collapse("".join(parts))))
            self._link = None
        if tag in _HEADINGS and self._heading is not None:
            level, parts = self._heading
            self.out.headings.append(Heading(level, _collapse("".join(parts))))
            self._heading = None
        if tag == "pre" and self._pre:
            self._pre -= 1
        if tag in ("td", "th") and self._cell is not None and self._row is not None:
            self._row.append(_collapse("".join(self._cell)))
            self._cell = None
        elif tag == "tr" and self._row is not None and self._table_stack:
            self._table_stack[-1].append(self._row)
            self._row = None
        elif tag == "table" and self._table_stack:
            self.out.tables.append(self._table_stack.pop())
        if tag in _BLOCK_TAGS:
            self._newline()

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self.out.title += data
            return
        if self._skip:
            return
        self._emit(data)

    def _emit(self, data: str) -> None:
        if not self._pre:
            data = _WS_RE.sub(" ", data) if data != "\n" else data
            if self._chunks and self._chunks[-1].endswith(("\n", "\t")):
                data = data.lstrip(" ")
        for buf in (self._heading, self._link):
            if buf is not None:
                buf[1].append(data)
        if self._cell is not None:
            self._cell.append(data)
        self._chunks.append(data)

    def finish(self) -> _Parsed:
        self.close()
        lines = ["".join(line).rstrip() for line in "".join(self._chunks).split("\n")]
        text = "\n".join(lines)
        self.out.text = re.sub(r"\n{3,}", "\n\n", text).strip()
        self.out.title = _collapse(self.out.title)
        return self.out


def _collapse(text: str) -> str:
    return _WS_RE.sub(" ", text).strip()


def parse_html(text: str) -> _Parsed:
    """Parse HTML text into its title, structure and reading-order text."""
    parser = _Extractor()
    parser.feed(text)
    return parser.finish()


def decode_html(data: bytes) -> tuple[str, str]:
    """Decode HTML bytes using a BOM, ``<meta charset>`` or content probing."""
    if data.startswith((b"\xef\xbb\xbf", b"\xff\xfe", b"\xfe\xff")):
        text, enc, _ = decode_bytes(data)
        return text, enc
    m = _CHARSET_RE.search(data[:2048])
    if m:
        enc = m.group(1).decode("ascii").lower()
        try:
            return data.decode(enc), enc
        except (LookupError, UnicodeDecodeError):
            pass
    text, enc, _ = decode_bytes(data)
    return text, enc


class HtmlDocument:
    """A read-only HTML document."""

    def __init__(self, raw: bytes, *, path: Path | None = None) -> None:
        self.path = path
        self.format = "html"
        self._raw = raw
        text, self.encoding = decode_html(raw)
        if "<" not in text:
            raise CorruptFileError("Not an HTML document: no markup found")
        self._parsed = parse_html(text)
        p = self._parsed
        self.metadata = Metadata(
            format="html",
            mime="text/html",
            title=p.title or None,
            author=p.meta.get("author"),
            page_count=1,
            extra={
                "encoding": self.encoding,
                "lang": p.lang,
                "description": p.meta.get("description"),
            },
        )

    @property
    def headings(self) -> list[Heading]:
        """Headings (``h1``-``h6``) in document order."""
        return list(self._parsed.headings)

    @property
    def links(self) -> list[Link]:
        """Anchors that carry an ``href``."""
        return list(self._parsed.links)

    @property
    def images(self) -> list[HtmlImage]:
        """``<img>`` references."""
        return list(self._parsed.images)

    @property
    def tables(self) -> list[list[list[str]]]:
        """Tables as lists of rows of cell texts."""
        return [[list(r) for r in t] for t in self._parsed.tables]

    def parts(self) -> list[Part]:
        """Return a single ``document`` part."""
        return [Part(kind="document", index=0, name=self._parsed.title or None)]

    def text(self) -> str:
        """Return the visible text in reading order."""
        return self._parsed.text

    def save(self, path: Path | None = None) -> Path:
        """Write the original bytes to ``path`` (default: the source path)."""
        return save_raw(self._raw, self.path, path)

    def close(self) -> None:
        """Release resources (nothing to close)."""


class HtmlHandler(FormatHandler):
    """Handler for HTML (``.html``, ``.htm``, ``.xhtml``)."""

    format_id: ClassVar[str] = "html"
    extensions: ClassVar[tuple[str, ...]] = ("html", "htm", "xhtml")
    mime_types: ClassVar[tuple[str, ...]] = ("text/html", "application/xhtml+xml")
    capabilities: ClassVar[frozenset[Capability]] = frozenset(
        {Capability.READ, Capability.EXTRACT_TEXT}
    )

    @classmethod
    def sniff(cls, head: bytes, path: Path | None) -> float:
        """Return confidence from leading markup or the extension."""
        lower = head[:512].lstrip().lower()
        if lower.startswith((b"<!doctype html", b"<html")):
            return 0.9
        if path is not None and path.suffix.lower().lstrip(".") in cls.extensions:
            return 0.6
        return 0.0

    def open(self, source: Path | bytes) -> Document:
        """Open an HTML file; undecodable or markup-free input raises ``CorruptFileError``."""
        data, path = read_source(source)
        return HtmlDocument(data, path=path)
