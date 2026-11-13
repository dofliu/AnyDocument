r"""DXF loading that decodes ``\U+XXXX`` escapes so names and text are real Unicode.

Drawings in a non-Unicode code page (DXF R12 … R2004, ``$DWGCODEPAGE`` such as
``ANSI_1252``) cannot store CJK characters directly, so writers emit them as
``\U+XXXX`` escapes.  ``ezdxf`` keeps those escapes verbatim, which makes a layer
saved as ``牆面`` come back as ``\U+7246\U+9762`` – unusable for look-ups and
filters.  Decoding every string while the drawing is loaded restores the original
names for layers, blocks, linetypes, text styles and all references to them; on
save ``ezdxf`` escapes whatever the code page cannot represent again.
"""

from __future__ import annotations

import io
import re
from collections.abc import Iterable, Iterator
from pathlib import Path

from ezdxf import filemanagement as ezdxf_files
from ezdxf.document import Drawing
from ezdxf.lldxf.tagger import binary_tags_loader
from ezdxf.lldxf.types import DXFTag
from ezdxf.lldxf.validator import is_binary_dxf_file, is_dxf_file

__all__ = ["decode_escapes", "read_dxf"]

# ``\U+XXXXXXXX`` (code points above the BMP, 0x10000–0x10FFFF) or ``\U+XXXX``.
_ESCAPE = re.compile(r"\\U\+(000[1-9A-Fa-f][0-9A-Fa-f]{4}|0010[0-9A-Fa-f]{4}|[0-9A-Fa-f]{4})")
_MARKER = "\\U+"


def _replace(match: re.Match[str]) -> str:
    code = int(match.group(1), 16)
    # Control characters would break the line-based DXF structure and lone
    # surrogates cannot be written back, so both stay escaped.
    if code < 0x20 or 0x7F <= code <= 0x9F or 0xD800 <= code <= 0xDFFF:
        return match.group(0)
    return chr(code)


def decode_escapes(text: str) -> str:
    r"""Replace ``\U+XXXX`` / ``\U+XXXXXXXX`` escapes in ``text`` by their characters.

    Escapes for control characters and surrogates are left untouched.
    """
    if _MARKER not in text:
        return text
    return _ESCAPE.sub(_replace, text)


def _decoded_tags(tags: Iterable[DXFTag]) -> Iterator[DXFTag]:
    for tag in tags:
        value = tag.value
        if isinstance(value, str) and _MARKER in value:
            yield DXFTag(tag.code, decode_escapes(value))
        else:
            yield tag


def read_dxf(path: Path) -> Drawing:
    r"""Load an ASCII or binary DXF file with all ``\U+XXXX`` escapes decoded.

    The text encoding is detected like :func:`ezdxf.readfile` does and undecodable
    bytes are preserved (``surrogateescape``).

    Raises:
        OSError: ``path`` does not exist or is not a DXF file.
        ezdxf.DXFError: the DXF structure is invalid.
    """
    filename = str(path)
    if is_binary_dxf_file(filename):
        tags = binary_tags_loader(path.read_bytes(), errors="surrogateescape")
        doc = Drawing.load(_decoded_tags(tags))
    else:
        if not is_dxf_file(filename):
            raise OSError(f"File '{filename}' is not a DXF file.")
        encoding = ezdxf_files.dxf_file_info(filename).encoding
        with open(filename, encoding=encoding, errors="surrogateescape") as stream:
            text = stream.read()
        doc = ezdxf_files.read(io.StringIO(decode_escapes(text)))
    doc.filename = filename
    return doc
