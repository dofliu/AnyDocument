"""Documents and handlers for TXT, Markdown, JSON, YAML and XML."""

from __future__ import annotations

import json
import re
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any, ClassVar

import yaml
from defusedxml import DefusedXmlException
from defusedxml import ElementTree as SafeET

from anydoc.core.errors import CorruptFileError
from anydoc.core.model import Capability, Metadata, Part
from anydoc.core.registry import FormatHandler
from anydoc.formats.csv.parse import decode_bytes

_HEADING_RE = re.compile(r"^(#{1,6})[ \t]+(.+?)[ \t]*#*[ \t]*$")
_XML_ENCODING_RE = re.compile(rb'\s*<\?xml[^>]*encoding=["\']([\w.-]+)["\']')
_XML_DECL_RE = re.compile(r"^\s*<\?xml[^>]*\?>")
_FENCE_RE = re.compile(r"^\s*(```|~~~)")


def _read_source(source: Path | bytes) -> tuple[bytes, Path | None]:
    if isinstance(source, Path):
        try:
            return source.read_bytes(), source
        except OSError as exc:
            raise CorruptFileError(f"Cannot read {source}: {exc}") from exc
    return source, None


def _encode(text: str, encoding: str, has_bom: bool) -> bytes:
    data = text.encode(encoding)
    if has_bom and encoding == "utf-8":
        data = b"\xef\xbb\xbf" + data
    return data


class _TextBase:
    """Shared path/encoding/save behaviour of all text-family documents."""

    def __init__(
        self,
        *,
        format: str,  # noqa: A002
        mime: str,
        encoding: str = "utf-8",
        has_bom: bool = False,
        path: Path | None = None,
    ) -> None:
        self.path = path
        self.format = format
        self.encoding = encoding
        self.has_bom = has_bom
        self._mime = mime

    def _serialize(self) -> str:
        raise NotImplementedError

    def to_bytes(self) -> bytes:
        """Serialize the document using the detected encoding (and BOM)."""
        return _encode(self._serialize(), self.encoding, self.has_bom)

    def save(self, path: Path | None = None) -> Path:
        """Write the document to ``path`` (default: the source path) and return it."""
        target = path or self.path
        if target is None:
            raise ValueError("No path given and document has no source path")
        target.write_bytes(self.to_bytes())
        return target

    def close(self) -> None:
        """No resources to release."""


class PlainTextDocument(_TextBase):
    """A TXT or Markdown document; ``content`` always uses ``\\n`` line endings."""

    def __init__(
        self,
        content: str,
        *,
        format: str = "txt",  # noqa: A002
        encoding: str = "utf-8",
        has_bom: bool = False,
        newline: str = "\n",
        path: Path | None = None,
    ) -> None:
        mime = "text/markdown" if format == "markdown" else "text/plain"
        super().__init__(format=format, mime=mime, encoding=encoding, has_bom=has_bom, path=path)
        self.content = content
        self.newline = newline
        self.metadata = self._build_metadata()

    def headings(self) -> list[tuple[int, str]]:
        """Return ``(level, title)`` for each ATX heading (Markdown only; fences skipped)."""
        if self.format != "markdown":
            return []
        found: list[tuple[int, str]] = []
        in_fence = False
        for line in self.content.split("\n"):
            if _FENCE_RE.match(line):
                in_fence = not in_fence
            elif not in_fence and (m := _HEADING_RE.match(line)):
                found.append((len(m.group(1)), m.group(2)))
        return found

    def _build_metadata(self) -> Metadata:
        headings = self.headings()
        return Metadata(
            format=self.format,
            mime=self._mime,
            title=headings[0][1] if headings else None,
            page_count=1,
            extra={
                "encoding": self.encoding,
                "has_bom": self.has_bom,
                "newline": {"\n": "lf", "\r\n": "crlf"}.get(self.newline, "lf"),
                "line_count": len(self.content.splitlines()),
                "char_count": len(self.content),
                "headings": [{"level": lv, "title": t} for lv, t in headings],
            },
        )

    def parts(self) -> list[Part]:
        """Return a single text part."""
        return [Part(kind="text", index=0)]

    def text(self) -> str:
        """Return the content."""
        return self.content

    def set_text(self, content: str) -> None:
        """Replace the content (line endings are normalized to ``\\n``)."""
        self.content = content.replace("\r\n", "\n").replace("\r", "\n")
        self.metadata = self._build_metadata()

    def _serialize(self) -> str:
        return self.content.replace("\n", self.newline)


class StructuredDocument(_TextBase):
    """A JSON or YAML document exposing its parsed value as ``data``."""

    def __init__(
        self,
        data: Any,
        *,
        format: str = "json",  # noqa: A002
        encoding: str = "utf-8",
        has_bom: bool = False,
        path: Path | None = None,
    ) -> None:
        mime = "application/yaml" if format == "yaml" else "application/json"
        super().__init__(format=format, mime=mime, encoding=encoding, has_bom=has_bom, path=path)
        self.data = data
        self.metadata = self._build_metadata()

    def _build_metadata(self) -> Metadata:
        keys = list(self.data) if isinstance(self.data, dict) else []
        return Metadata(
            format=self.format,
            mime=self._mime,
            page_count=1,
            extra={
                "encoding": self.encoding,
                "has_bom": self.has_bom,
                "root_type": type(self.data).__name__,
                "top_level_keys": [str(k) for k in keys],
                "length": len(self.data) if isinstance(self.data, dict | list) else None,
            },
        )

    def parts(self) -> list[Part]:
        """Return a single data part."""
        return [Part(kind="data", index=0)]

    def text(self) -> str:
        """Return the value pretty-printed in the document's own syntax."""
        return self._serialize()

    def to_dict(self) -> Any:
        """Return the parsed value (dict/list/scalar)."""
        return self.data

    def _serialize(self) -> str:
        if self.format == "yaml":
            return yaml.safe_dump(self.data, allow_unicode=True, sort_keys=False)
        return json.dumps(self.data, ensure_ascii=False, indent=2) + "\n"


class XmlDocument(_TextBase):
    """An XML document backed by an ``ElementTree`` root element."""

    def __init__(
        self,
        root: ET.Element,
        *,
        encoding: str = "utf-8",
        has_bom: bool = False,
        path: Path | None = None,
    ) -> None:
        super().__init__(
            format="xml", mime="application/xml", encoding=encoding, has_bom=has_bom, path=path
        )
        self.root = root
        self.metadata = Metadata(
            format="xml",
            mime="application/xml",
            page_count=1,
            extra={
                "encoding": encoding,
                "has_bom": has_bom,
                "root_tag": root.tag,
                "element_count": sum(1 for _ in root.iter()),
            },
        )

    def parts(self) -> list[Part]:
        """Return a single tree part named after the root tag."""
        return [Part(kind="tree", index=0, name=self.root.tag)]

    def tree(self) -> dict[str, Any]:
        """Return the element tree as nested ``{tag, attrs, text, children}`` dicts."""
        return _element_to_dict(self.root)

    def text(self) -> str:
        """Return all text nodes in document order, one non-empty node per line."""
        pieces = (t.strip() for t in self.root.itertext())
        return "\n".join(p for p in pieces if p)

    def _serialize(self) -> str:
        ET.indent(self.root)
        body = ET.tostring(self.root, encoding="unicode")
        return f'<?xml version="1.0" encoding="{self.encoding}"?>\n{body}\n'


def _element_to_dict(el: ET.Element) -> dict[str, Any]:
    return {
        "tag": el.tag,
        "attrs": dict(el.attrib),
        "text": (el.text or "").strip() or None,
        "children": [_element_to_dict(c) for c in el],
    }


class _TextFamilyHandler(FormatHandler):
    """Base: extension-based sniffing and shared byte decoding."""

    capabilities: ClassVar[frozenset[Capability]] = frozenset(
        {Capability.READ, Capability.WRITE, Capability.EXTRACT_TEXT, Capability.EDIT}
    )

    @classmethod
    def sniff(cls, head: bytes, path: Path | None) -> float:
        """Return confidence from the file extension (content detection lives in ``detect``)."""
        if path is not None and path.suffix.lower().lstrip(".") in cls.extensions:
            return 0.9
        return 0.0


class TxtHandler(_TextFamilyHandler):
    """Handler for plain text."""

    format_id: ClassVar[str] = "txt"
    extensions: ClassVar[tuple[str, ...]] = ("txt", "text", "log")
    mime_types: ClassVar[tuple[str, ...]] = ("text/plain",)
    _format: ClassVar[str] = "txt"

    def open(self, source: Path | bytes) -> PlainTextDocument:
        """Open a text file, detecting UTF-8/BOM/UTF-16/Big5 encodings and newline style."""
        data, path = _read_source(source)
        text, encoding, has_bom = decode_bytes(data)
        newline = "\r\n" if "\r\n" in text else "\n"
        content = text.replace("\r\n", "\n").replace("\r", "\n")
        return PlainTextDocument(
            content,
            format=self._format,
            encoding=encoding,
            has_bom=has_bom,
            newline=newline,
            path=path,
        )

    def create(self) -> PlainTextDocument:
        """Create an empty document."""
        return PlainTextDocument("", format=self._format)


class MarkdownHandler(TxtHandler):
    """Handler for Markdown."""

    format_id: ClassVar[str] = "markdown"
    extensions: ClassVar[tuple[str, ...]] = ("md", "markdown")
    mime_types: ClassVar[tuple[str, ...]] = ("text/markdown",)
    _format: ClassVar[str] = "markdown"


class _StructuredHandler(_TextFamilyHandler):
    _format: ClassVar[str]

    def _parse(self, text: str) -> Any:
        raise NotImplementedError

    def open(self, source: Path | bytes) -> StructuredDocument:
        """Open and parse the file; malformed content raises ``CorruptFileError``."""
        data, path = _read_source(source)
        text, encoding, has_bom = decode_bytes(data)
        try:
            value = self._parse(text)
        except (ValueError, yaml.YAMLError) as exc:
            raise CorruptFileError(f"Invalid {self._format.upper()}: {exc}") from exc
        return StructuredDocument(
            value, format=self._format, encoding=encoding, has_bom=has_bom, path=path
        )

    def create(self) -> StructuredDocument:
        """Create an empty mapping."""
        return StructuredDocument({}, format=self._format)


class JsonHandler(_StructuredHandler):
    """Handler for JSON."""

    format_id: ClassVar[str] = "json"
    extensions: ClassVar[tuple[str, ...]] = ("json",)
    mime_types: ClassVar[tuple[str, ...]] = ("application/json",)
    _format: ClassVar[str] = "json"

    def _parse(self, text: str) -> Any:
        return json.loads(text)


class YamlHandler(_StructuredHandler):
    """Handler for YAML (single-document, safe loading only)."""

    format_id: ClassVar[str] = "yaml"
    extensions: ClassVar[tuple[str, ...]] = ("yaml", "yml")
    mime_types: ClassVar[tuple[str, ...]] = ("application/yaml", "text/yaml")
    _format: ClassVar[str] = "yaml"

    def _parse(self, text: str) -> Any:
        return yaml.safe_load(text)


class XmlHandler(_TextFamilyHandler):
    """Handler for XML (parsed with defusedxml: no entity expansion attacks)."""

    format_id: ClassVar[str] = "xml"
    extensions: ClassVar[tuple[str, ...]] = ("xml",)
    mime_types: ClassVar[tuple[str, ...]] = ("application/xml", "text/xml")

    @staticmethod
    def _parse(data: bytes, encoding: str) -> ET.Element:
        try:
            return SafeET.fromstring(data)
        except DefusedXmlException:
            raise
        except (ET.ParseError, ValueError):
            # expat only knows a few encodings; transcode e.g. Big5 to UTF-8 and retry.
            if encoding in ("utf-8", "utf8"):
                raise
            try:
                text = data.decode(encoding)
            except (LookupError, UnicodeDecodeError):
                raise ET.ParseError(f"Cannot decode as {encoding}") from None
            text = _XML_DECL_RE.sub("", text, count=1)
            return SafeET.fromstring(text.encode("utf-8"))

    def open(self, source: Path | bytes) -> XmlDocument:
        """Open and parse the file; malformed or hostile XML raises ``CorruptFileError``."""
        data, path = _read_source(source)
        has_bom = data.startswith(b"\xef\xbb\xbf")
        m = _XML_ENCODING_RE.match(data[:200])
        encoding = m.group(1).decode("ascii").lower() if m else "utf-8"
        try:
            root = self._parse(data, encoding)
        except (ET.ParseError, ValueError, LookupError) as exc:
            raise CorruptFileError(f"Invalid XML: {exc}") from exc
        return XmlDocument(root, encoding=encoding, has_bom=has_bom, path=path)

    def create(self) -> XmlDocument:
        """Create a document with an empty ``<root/>`` element."""
        return XmlDocument(ET.Element("root"))
