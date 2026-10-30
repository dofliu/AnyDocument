"""RTF documents parsed with a small group/control-word state machine."""

from __future__ import annotations

import re
from pathlib import Path
from typing import ClassVar

from anydoc.core.errors import CorruptFileError
from anydoc.core.model import Capability, Document, Metadata, Part
from anydoc.core.registry import FormatHandler
from anydoc.formats.markup._base import read_source, save_raw

_TOKEN_RE = re.compile(
    rb"\\(?P<word>[a-zA-Z]+)(?P<num>-?\d+)? ?"
    rb"|\\'(?P<hex>[0-9a-fA-F]{2})"
    rb"|\\(?P<sym>[^a-zA-Z])"
    rb"|(?P<open>\{)|(?P<close>\})"
    rb"|(?P<text>[^\\{}\r\n]+)"
    rb"|[\r\n]"
)
_SKIP_DESTINATIONS = frozenset(
    {
        b"fonttbl", b"colortbl", b"stylesheet", b"pict", b"info", b"header", b"footer",
        b"headerl", b"headerr", b"footerl", b"footerr", b"themedata", b"colorschememapping",
        b"latentstyles", b"datastore", b"listtable", b"listoverridetable", b"rsidtbl",
        b"generator", b"xmlnstbl", b"fldinst", b"object", b"revtbl", b"private",
    }
)  # fmt: skip
_CHARSETS = {0: "cp1252", 134: "gb2312", 136: "big5", 128: "cp932", 129: "cp949", 204: "cp1251"}
_SYMBOLS = {
    b"~": "\u00a0",
    b"_": "\u2011",
    b"-": "",
    b"|": "",
    b":": "",
}
_WORDS = {
    b"par": "\n",
    b"line": "\n",
    b"sect": "\n",
    b"page": "\n",
    b"tab": "\t",
    b"cell": "\t",
    b"row": "\n",
    b"emdash": "\u2014",
    b"endash": "\u2013",
    b"bullet": "\u2022",
    b"lquote": "\u2018",
    b"rquote": "\u2019",
    b"ldblquote": "\u201c",
    b"rdblquote": "\u201d",
    b"emspace": "\u2003",
    b"enspace": "\u2002",
}
_INFO_RE = re.compile(rb"\{\\info(.*)", re.DOTALL)
_MAX_DEPTH = 1000


class _State:
    __slots__ = ("skip", "uc", "codec")

    def __init__(self, skip: bool, uc: int, codec: str) -> None:
        self.skip = skip
        self.uc = uc
        self.codec = codec

    def copy(self) -> _State:
        return _State(self.skip, self.uc, self.codec)


def _decode_info(data: bytes, codec: str) -> dict[str, str]:
    """Extract ``title``/``author`` and friends from the ``\\info`` group."""
    info: dict[str, str] = {}
    m = _INFO_RE.search(data)
    if not m:
        return info
    depth, end = 1, 0
    body = m.group(1)
    for i, ch in enumerate(body):
        if ch == 0x7B:
            depth += 1
        elif ch == 0x7D:
            depth -= 1
            if depth == 0:
                end = i
                break
    body = body[:end] if end else body
    for key in (b"title", b"author", b"subject", b"keywords", b"company"):
        km = re.search(rb"\{\\" + key + rb"[ \r\n]*(.*?)\}", body, re.DOTALL)
        if km:
            text = _extract(km.group(1), codec=codec, in_body=True)[0].strip()
            if text:
                info[key.decode()] = text
    return info


def _extract(data: bytes, *, codec: str = "cp1252", in_body: bool = False) -> tuple[str, str]:
    """Return ``(text, codec)`` of an RTF byte stream."""
    out: list[str] = []
    stack: list[_State] = []
    st = _State(False, 1, codec)
    pending_skip = 0  # characters to drop after a \uN escape
    pending_bytes = bytearray()

    def flush() -> None:
        if pending_bytes:
            if not st.skip:
                out.append(bytes(pending_bytes).decode(st.codec, "replace"))
            pending_bytes.clear()

    first_word_after_open = False
    for m in _TOKEN_RE.finditer(data):
        if m.group("hex") is None:
            flush()
        if m.group("open"):
            if len(stack) >= _MAX_DEPTH:
                raise CorruptFileError("RTF nesting too deep")
            stack.append(st.copy())
            first_word_after_open = True
            continue
        if m.group("close"):
            if stack:
                st = stack.pop()
            first_word_after_open = False
            continue
        if m.group("word") is not None:
            word, num = m.group("word"), m.group("num")
            if first_word_after_open and word in _SKIP_DESTINATIONS:
                st.skip = True
            first_word_after_open = False
            if word == b"ansicpg" and num:
                st.codec = f"cp{int(num)}"
            elif word == b"fcharset" and num and int(num) in _CHARSETS:
                st.codec = _CHARSETS[int(num)]
            elif word == b"uc" and num:
                st.uc = int(num)
            elif word == b"u" and num is not None:
                code = int(num)
                if code < 0:
                    code += 65536
                if not st.skip:
                    out.append(chr(code))
                pending_skip = st.uc
            elif word in _WORDS and not st.skip:
                out.append(_WORDS[word])
            continue
        first_word_after_open = False
        if m.group("hex") is not None:
            if pending_skip:
                pending_skip -= 1
            else:
                pending_bytes.append(int(m.group("hex"), 16))
            continue
        sym = m.group("sym")
        if sym is not None:
            if sym == b"*":
                st.skip = True
            elif sym in _SYMBOLS and not st.skip:
                out.append(_SYMBOLS[sym])
            elif sym in (b"\\", b"{", b"}") and not st.skip:
                out.append(sym.decode())
            continue
        text = m.group("text")
        if text is not None:
            if pending_skip:
                n = min(pending_skip, len(text))
                pending_skip -= n
                text = text[n:]
            if text and not st.skip:
                out.append(text.decode(st.codec, "replace"))
    flush()
    result = "".join(out)
    if not in_body:
        result = re.sub(r"[ \t]+\n", "\n", result)
        result = re.sub(r"\n{3,}", "\n\n", result)
    return result, st.codec


class RtfDocument:
    """A read-only RTF document."""

    def __init__(self, raw: bytes, *, path: Path | None = None) -> None:
        if not raw.lstrip().startswith(b"{\\rtf"):
            raise CorruptFileError("Not an RTF document: missing {\\rtf header")
        self.path = path
        self.format = "rtf"
        self._raw = raw
        m = re.search(rb"\\ansicpg(\d+)", raw[:4096])
        codec = f"cp{m.group(1).decode()}" if m else "cp1252"
        try:
            self._text, self.codec = _extract(raw, codec=codec)
        except LookupError as exc:
            raise CorruptFileError(f"Unsupported RTF code page: {exc}") from exc
        info = _decode_info(raw, codec)
        self.metadata = Metadata(
            format="rtf",
            mime="application/rtf",
            title=info.get("title"),
            author=info.get("author"),
            extra={
                "codepage": codec,
                **{k: v for k, v in info.items() if k in ("subject", "keywords", "company")},
            },
        )

    def parts(self) -> list[Part]:
        """Return a single ``document`` part."""
        return [Part(kind="document", index=0)]

    def text(self) -> str:
        """Return the plain text of the document."""
        return self._text.strip()

    def paragraphs(self) -> list[str]:
        """Return non-empty paragraphs (lines) of text."""
        return [line for line in self._text.split("\n") if line.strip()]

    def save(self, path: Path | None = None) -> Path:
        """Write the original bytes to ``path`` (default: the source path)."""
        return save_raw(self._raw, self.path, path)

    def close(self) -> None:
        """Release resources (nothing to close)."""


class RtfHandler(FormatHandler):
    """Handler for Rich Text Format (``.rtf``)."""

    format_id: ClassVar[str] = "rtf"
    extensions: ClassVar[tuple[str, ...]] = ("rtf",)
    mime_types: ClassVar[tuple[str, ...]] = ("application/rtf", "text/rtf")
    capabilities: ClassVar[frozenset[Capability]] = frozenset(
        {Capability.READ, Capability.EXTRACT_TEXT}
    )

    @classmethod
    def sniff(cls, head: bytes, path: Path | None) -> float:
        """Return confidence from the ``{\\rtf`` header."""
        return 0.95 if head.lstrip().startswith(b"{\\rtf") else 0.0

    def open(self, source: Path | bytes) -> Document:
        """Open an RTF file; input without an RTF header raises ``CorruptFileError``."""
        data, path = read_source(source)
        return RtfDocument(data, path=path)
