"""Find and replace for DOCX that works across runs and preserves formatting."""

from __future__ import annotations

import re
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

_W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
_XML_SPACE = "{http://www.w3.org/XML/1998/namespace}space"
_P = f"{{{_W}}}p"
_T = f"{{{_W}}}t"
_MC_FALLBACK = "{http://schemas.openxmlformats.org/markup-compatibility/2006}Fallback"


@dataclass(frozen=True)
class TextMatch:
    """One occurrence found by :func:`find_text`."""

    location: str  # "body", "header" or "footer"
    paragraph_text: str
    text: str


def compile_pattern(
    find: str, *, regex: bool = False, ignore_case: bool = False, whole_word: bool = False
) -> re.Pattern[str]:
    """Compile the search pattern, raising ``ValueError`` for empty/invalid input."""
    if not find:
        raise ValueError("Search text must not be empty")
    source = find if regex else re.escape(find)
    if whole_word:
        source = rf"(?<!\w)(?:{source})(?!\w)"
    try:
        return re.compile(source, re.IGNORECASE if ignore_case else 0)
    except re.error as exc:
        raise ValueError(f"Invalid regular expression: {exc}") from exc


def _in_fallback(p: Any) -> bool:
    return any(a.tag == _MC_FALLBACK for a in p.iterancestors())


def _paragraphs(root: Any) -> Iterator[Any]:
    for p in root.iter(_P):
        if not _in_fallback(p):
            yield p


def _run_segments(p: Any) -> list[tuple[Any, str]]:
    segments: list[tuple[Any, str]] = []
    for t in p.iter(_T):
        if t.text:
            segments.append((t, t.text))
    return segments


def _set_text(t: Any, text: str) -> None:
    t.text = text
    t.set(_XML_SPACE, "preserve")


def _replace_in_paragraph(
    p: Any, pattern: re.Pattern[str], replacement: str, regex: bool, limit: int
) -> int:
    segments = _run_segments(p)
    if not segments:
        return 0
    joined = "".join(text for _, text in segments)
    matches = [m for m in pattern.finditer(joined) if m.end() > m.start()]
    if limit:
        matches = matches[:limit]
    if not matches:
        return 0
    starts: list[int] = []
    pos = 0
    for _, text in segments:
        starts.append(pos)
        pos += len(text)
    texts = [text for _, text in segments]

    def locate(offset: int) -> int:
        for i in range(len(segments) - 1, -1, -1):
            if starts[i] <= offset:
                return i
        return 0

    for m in reversed(matches):
        repl = m.expand(replacement) if regex else replacement
        i, j = locate(m.start()), locate(m.end() - 1)
        a, b = m.start() - starts[i], m.end() - starts[j]
        if i == j:
            texts[i] = texts[i][:a] + repl + texts[i][b:]
        else:
            texts[i] = texts[i][:a] + repl
            for k in range(i + 1, j):
                texts[k] = ""
            texts[j] = texts[j][b:]
    for (t, _), text in zip(segments, texts, strict=True):
        _set_text(t, text)
    return len(matches)


def _roots(doc: Any, body: bool, headers_footers: bool) -> Iterator[tuple[str, Any]]:
    if body:
        yield "body", doc.element.body
    if headers_footers:
        seen: set[int] = set()
        for section in doc.sections:
            for kind, parts in (
                ("header", (section.header, section.first_page_header, section.even_page_header)),
                ("footer", (section.footer, section.first_page_footer, section.even_page_footer)),
            ):
                for part in parts:
                    if part.is_linked_to_previous:
                        continue
                    element = part._element
                    if id(element) in seen:
                        continue
                    seen.add(id(element))
                    yield kind, element


def replace_text(
    doc: Any,
    find: str,
    replace: str,
    *,
    regex: bool = False,
    ignore_case: bool = False,
    whole_word: bool = False,
    count: int = 0,
    body: bool = True,
    headers_footers: bool = True,
) -> int:
    """Replace ``find`` with ``replace`` and return the number of replacements.

    Matches may span several runs; the replacement takes the formatting of the run where the
    match starts. Tables (including nested ones) are part of the body. ``count`` limits the
    total number of replacements (0 = all). With ``regex`` the replacement may use ``\\1``/
    ``\\g<name>`` group references.
    """
    pattern = compile_pattern(find, regex=regex, ignore_case=ignore_case, whole_word=whole_word)
    if regex:
        pattern.sub(replace, "")  # validate the replacement template early
    total = 0
    for _, root in _roots(doc, body, headers_footers):
        for p in _paragraphs(root):
            remaining = count - total if count else 0
            if count and remaining <= 0:
                return total
            total += _replace_in_paragraph(p, pattern, replace, regex, remaining)
    return total


def find_text(
    doc: Any,
    find: str,
    *,
    regex: bool = False,
    ignore_case: bool = False,
    whole_word: bool = False,
    body: bool = True,
    headers_footers: bool = True,
) -> list[TextMatch]:
    """Return every (non-empty) match of ``find`` without modifying the document."""
    pattern = compile_pattern(find, regex=regex, ignore_case=ignore_case, whole_word=whole_word)
    found: list[TextMatch] = []
    for location, root in _roots(doc, body, headers_footers):
        for p in _paragraphs(root):
            joined = "".join(text for _, text in _run_segments(p))
            for m in pattern.finditer(joined):
                if m.end() > m.start():
                    found.append(TextMatch(location, joined, m.group(0)))
    return found


__all__ = ["TextMatch", "compile_pattern", "find_text", "replace_text"]
