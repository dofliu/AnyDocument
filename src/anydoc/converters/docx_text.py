"""DOCX <-> Markdown / HTML conversion (headings, lists, tables, inline styles, links, images)."""

from __future__ import annotations

import base64
import binascii
import html as html_lib
import io
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

from docx import Document as new_docx
from docx.opc.constants import RELATIONSHIP_TYPE as RT
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Emu, Inches, Pt, RGBColor
from docx.text.paragraph import Paragraph
from docx.text.run import Run as DocxRun
from markdown_it import MarkdownIt

from anydoc.core.convert import ConversionGraph, Converter, graph
from anydoc.core.errors import CorruptFileError
from anydoc.formats.docx import DocxHandler

_MONO_FONTS = {
    "consolas",
    "courier new",
    "courier",
    "monaco",
    "menlo",
    "source code pro",
    "lucida console",
    "dejavu sans mono",
}
_CODE_STYLES = {"code block", "code", "source code", "preformatted text", "html preformatted"}
_CODE_FONT = "Consolas"
_QUOTE_STYLES = {"quote", "block text", "intense quote"}
_HEADING_RE = re.compile(r"^heading\s*(\d)$", re.IGNORECASE)
_LIST_RE = re.compile(r"^list (bullet|number)(?: (\d))?$", re.IGNORECASE)
_MAX_PICTURE_WIDTH = Inches(6)


# --------------------------------------------------------------------------------------------
# Intermediate model
# --------------------------------------------------------------------------------------------


@dataclass
class Run:
    """An inline piece of text (or an image) with character formatting."""

    text: str = ""
    bold: bool = False
    italic: bool = False
    strike: bool = False
    code: bool = False
    href: str | None = None
    image: tuple[bytes, str] | None = None  # (data, extension)

    def key(self) -> tuple[bool, bool, bool, bool, str | None]:
        return (self.bold, self.italic, self.strike, self.code, self.href)


@dataclass
class Block:
    """A block-level element: heading, para, item, code, quote, table or rule."""

    kind: str
    runs: list[Run] = field(default_factory=list)
    level: int = 0  # heading level (1-6) or list depth (0-based)
    ordered: bool = False
    text: str = ""  # code blocks
    rows: list[list[list[Run]]] = field(default_factory=list)  # tables: rows -> cells -> runs


ImageRef = Callable[[bytes, str], str]


# --------------------------------------------------------------------------------------------
# DOCX -> blocks
# --------------------------------------------------------------------------------------------


def _merge(runs: list[Run]) -> list[Run]:
    out: list[Run] = []
    for r in runs:
        if out and r.image is None and out[-1].image is None and out[-1].key() == r.key():
            out[-1] = Run(out[-1].text + r.text, *r.key()[:4], r.href)
        elif r.image is not None or r.text:
            out.append(r)
    return out


def _run_from_docx(run: DocxRun, href: str | None, doc: Any) -> list[Run]:
    out: list[Run] = []
    blips = run._r.xpath(".//a:blip/@r:embed")
    for rid in blips:
        try:
            part = doc.part.related_parts[rid]
        except KeyError:
            continue
        ext = Path(str(part.partname)).suffix.lstrip(".").lower() or "png"
        out.append(Run(image=(part.blob, ext)))
    text = run.text
    if text:
        style = run.style.name.lower() if run.style is not None and run.style.name else ""
        font = (run.font.name or "").lower()
        out.append(
            Run(
                text,
                bold=bool(run.bold) or style == "strong",
                italic=bool(run.italic) or style == "emphasis",
                strike=bool(run.font.strike),
                code=font in _MONO_FONTS,
                href=href,
            )
        )
    return out


def _paragraph_runs(par: Paragraph, doc: Any) -> list[Run]:
    runs: list[Run] = []
    for child in par._p.iterchildren():
        if child.tag == qn("w:r"):
            runs.extend(_run_from_docx(DocxRun(child, par), None, doc))
        elif child.tag == qn("w:hyperlink"):
            href = None
            rid = child.get(qn("r:id"))
            if rid and rid in doc.part.rels and doc.part.rels[rid].is_external:
                href = doc.part.rels[rid].target_ref
            for r in child.iterchildren(qn("w:r")):
                runs.extend(_run_from_docx(DocxRun(r, par), href, doc))
    return _merge(runs)


def _numbering_ordered(par: Paragraph, doc: Any) -> tuple[bool, int] | None:
    num_pr = par._p.xpath("./w:pPr/w:numPr")
    if not num_pr:
        return None
    num_ids = num_pr[0].xpath("./w:numId/@w:val")
    ilvl = num_pr[0].xpath("./w:ilvl/@w:val")
    level = int(ilvl[0]) if ilvl else 0
    ordered = False
    try:
        root = doc.part.numbering_part.element
        abstract = root.xpath(f'./w:num[@w:numId="{num_ids[0]}"]/w:abstractNumId/@w:val')
        fmt = root.xpath(
            f'./w:abstractNum[@w:abstractNumId="{abstract[0]}"]'
            f'/w:lvl[@w:ilvl="{level}"]/w:numFmt/@w:val'
        )
        ordered = bool(fmt) and fmt[0] != "bullet"
    except (IndexError, KeyError, NotImplementedError):
        pass
    return ordered, level


def _has_bottom_border(par: Paragraph) -> bool:
    return bool(par._p.xpath("./w:pPr/w:pBdr/w:bottom"))


def _table_block(table: Any, doc: Any) -> Block:
    rows: list[list[list[Run]]] = []
    for row in table.rows:
        cells: list[list[Run]] = []
        seen: set[int] = set()
        for cell in row.cells:
            if id(cell._tc) in seen:  # merged cells repeat the same element
                continue
            seen.add(id(cell._tc))
            runs: list[Run] = []
            for i, par in enumerate(cell.paragraphs):
                if i and runs:
                    runs.append(Run("\n"))
                runs.extend(_paragraph_runs(par, doc))
            cells.append(_merge(runs))
        rows.append(cells)
    return Block("table", rows=rows)


def docx_blocks(doc: Any) -> list[Block]:
    """Convert a python-docx ``Document`` into the intermediate block list."""
    from docx.table import Table

    blocks: list[Block] = []
    for child in doc.element.body.iterchildren():
        if child.tag == qn("w:tbl"):
            blocks.append(_table_block(Table(child, doc), doc))
            continue
        if child.tag != qn("w:p"):
            continue
        par = Paragraph(child, doc)
        style = par.style.name if par.style is not None and par.style.name else ""
        lower = style.lower()
        runs = _paragraph_runs(par, doc)
        has_content = any(r.image is not None or r.text.strip() for r in runs)
        if not has_content:
            if _has_bottom_border(par):
                blocks.append(Block("rule"))
            continue
        if lower == "title":
            blocks.append(Block("heading", runs, level=1))
        elif m := _HEADING_RE.match(lower):
            blocks.append(Block("heading", runs, level=min(6, max(1, int(m.group(1))))))
        elif lower in _CODE_STYLES:
            text = "".join(r.text for r in runs)
            if blocks and blocks[-1].kind == "code":
                blocks[-1].text += "\n" + text
            else:
                blocks.append(Block("code", text=text))
        elif m := _LIST_RE.match(lower):
            depth = int(m.group(2) or 1) - 1
            blocks.append(Block("item", runs, level=depth, ordered=m.group(1) == "number"))
        elif (num := _numbering_ordered(par, doc)) is not None:
            blocks.append(Block("item", runs, level=num[1], ordered=num[0]))
        elif lower in _QUOTE_STYLES:
            if blocks and blocks[-1].kind == "quote":
                blocks[-1].rows.append([runs])
            else:
                blocks.append(Block("quote", rows=[[runs]]))
        else:
            blocks.append(Block("para", runs))
    return blocks


# --------------------------------------------------------------------------------------------
# blocks -> Markdown
# --------------------------------------------------------------------------------------------

_MD_ESCAPE = re.compile(r"([\\`*_\[\]<>~|])")
_LEAD_ESCAPE = re.compile(r"^(\s*)(#{1,6}(?=\s|$)|[-+](?=\s)|\d+(?=[.)]\s)|>)")


def _lead_sub(m: re.Match[str]) -> str:
    lead, tok = m.group(1), m.group(2)
    if tok.isdigit():  # escape the delimiter that follows the digits: "1\\." not "\\1."
        return f"{lead}{tok}\\"
    return f"{lead}\\{tok}"


def _md_text(text: str, *, table: bool = False) -> str:
    out = []
    for i, line in enumerate(text.split("\n")):
        esc = _MD_ESCAPE.sub(r"\\\1", line)
        if not table:
            esc = _LEAD_ESCAPE.sub(_lead_sub, esc) if i == 0 else esc
        out.append(esc)
    sep = "<br>" if table else "\\\n"
    return sep.join(out)


def _md_href(href: str) -> str:
    return href.replace(" ", "%20").replace("(", "%28").replace(")", "%29")


def _md_code_span(text: str) -> str:
    body = text.replace("\n", " ")
    longest = max((len(m) for m in re.findall(r"`+", body)), default=0)
    fence = "`" * (longest + 1)
    pad = " " if body.startswith(("`", " ")) or body.endswith(("`", " ")) else ""
    return f"{fence}{pad}{body}{pad}{fence}"


def _md_run(run: Run, image_ref: ImageRef, table: bool) -> str:
    if run.image is not None:
        return f"![]({_md_href(image_ref(*run.image))})"
    text = run.text
    stripped = text.strip(" \t")
    if not stripped:
        return _md_text(text, table=table)
    lead = text[: len(text) - len(text.lstrip(" \t"))]
    trail = text[len(text.rstrip(" \t")) :]
    core = _md_code_span(stripped) if run.code else _md_text(stripped, table=table)
    if run.italic:
        core = f"*{core}*"
    if run.bold:
        core = f"**{core}**"
    if run.strike:
        core = f"~~{core}~~"
    return f"{lead}{core}{trail}"


def _md_inline(runs: list[Run], image_ref: ImageRef, *, table: bool = False) -> str:
    parts: list[str] = []
    i = 0
    runs = _merge(runs)
    while i < len(runs):
        href = runs[i].href
        j = i
        while j < len(runs) and runs[j].href == href:
            j += 1
        inner = "".join(_md_run(r, image_ref, table) for r in runs[i:j])
        if href and inner.strip():
            inner = f"[{inner}]({_md_href(href)})"
        parts.append(inner)
        i = j
    return "".join(parts).strip()


def blocks_to_markdown(blocks: list[Block], image_ref: ImageRef) -> str:
    """Render blocks as CommonMark (+ GFM tables and strikethrough)."""
    chunks: list[str] = []
    counters: dict[int, int] = {}
    prev: Block | None = None
    for b in blocks:
        if b.kind != "item":
            counters.clear()
        if b.kind == "heading":
            line = _md_inline(b.runs, image_ref).replace("\\\n", " ")
            text = f"{'#' * b.level} {line}"
        elif b.kind == "para":
            text = _md_inline(b.runs, image_ref)
        elif b.kind == "item":
            counters = {k: v for k, v in counters.items() if k <= b.level}
            if not b.ordered:
                counters.pop(b.level, None)
            else:
                counters[b.level] = counters.get(b.level, 0) + 1
            marker = f"{counters[b.level]}." if b.ordered else "-"
            line = _md_inline(b.runs, image_ref).replace("\\\n", " ")
            text = f"{'    ' * b.level}{marker} {line}"
        elif b.kind == "code":
            longest = max((len(m) for m in re.findall(r"`+", b.text)), default=0)
            fence = "`" * max(3, longest + 1)
            text = f"{fence}\n{b.text}\n{fence}"
        elif b.kind == "quote":
            paras = [_md_inline(r[0], image_ref) for r in b.rows]
            text = "\n>\n".join("\n".join(f"> {ln}" for ln in p.split("\n")) for p in paras)
        elif b.kind == "rule":
            text = "---"
        else:  # table
            text = _md_table(b, image_ref)
        if prev is not None and prev.kind == "item" and b.kind == "item":
            chunks[-1] += "\n" + text
        else:
            chunks.append(text)
        prev = b
    return "\n\n".join(chunks) + "\n" if chunks else ""


def _md_table(b: Block, image_ref: ImageRef) -> str:
    rows = [[_md_inline(c, image_ref, table=True) for c in row] for row in b.rows]
    width = max((len(r) for r in rows), default=0)
    if not width:
        return ""
    rows = [r + [""] * (width - len(r)) for r in rows]
    lines = ["| " + " | ".join(rows[0]) + " |", "| " + " | ".join(["---"] * width) + " |"]
    lines += ["| " + " | ".join(r) + " |" for r in rows[1:]]
    return "\n".join(lines)


# --------------------------------------------------------------------------------------------
# blocks -> HTML
# --------------------------------------------------------------------------------------------


def _h(text: str) -> str:
    return html_lib.escape(text, quote=True).replace("\n", "<br>")


def _html_inline(runs: list[Run], image_ref: ImageRef) -> str:
    parts: list[str] = []
    i = 0
    runs = _merge(runs)
    while i < len(runs):
        href = runs[i].href
        j = i
        while j < len(runs) and runs[j].href == href:
            j += 1
        inner = ""
        for r in runs[i:j]:
            if r.image is not None:
                inner += f'<img src="{html_lib.escape(image_ref(*r.image), quote=True)}" alt="">'
                continue
            s = _h(r.text)
            if r.code:
                s = f"<code>{s}</code>"
            if r.italic:
                s = f"<em>{s}</em>"
            if r.bold:
                s = f"<strong>{s}</strong>"
            if r.strike:
                s = f"<del>{s}</del>"
            inner += s
        if href:
            inner = f'<a href="{html_lib.escape(href, quote=True)}">{inner}</a>'
        parts.append(inner)
        i = j
    return "".join(parts)


def blocks_to_html(blocks: list[Block], image_ref: ImageRef, title: str = "") -> str:
    """Render blocks as a standalone UTF-8 HTML5 document."""
    out: list[str] = []
    stack: list[list[Any]] = []  # [ordered, li_open]

    def close_list() -> None:
        if stack[-1][1]:
            out.append("</li>")
        out.append("</ol>" if stack[-1][0] else "</ul>")
        stack.pop()

    for b in blocks:
        if b.kind != "item":
            while stack:
                close_list()
        if b.kind == "heading":
            out.append(f"<h{b.level}>{_html_inline(b.runs, image_ref)}</h{b.level}>")
        elif b.kind == "para":
            out.append(f"<p>{_html_inline(b.runs, image_ref)}</p>")
        elif b.kind == "item":
            target = b.level + 1
            while len(stack) > target:
                close_list()
            if len(stack) == target:
                if stack[-1][0] != b.ordered:
                    close_list()
                elif stack[-1][1]:
                    out.append("</li>")
                    stack[-1][1] = False
            while len(stack) < target:
                out.append("<ol>" if b.ordered else "<ul>")
                stack.append([b.ordered, False])
            out.append(f"<li>{_html_inline(b.runs, image_ref)}")
            stack[-1][1] = True
        elif b.kind == "code":
            out.append(f"<pre><code>{html_lib.escape(b.text)}</code></pre>")
        elif b.kind == "quote":
            inner = "".join(f"<p>{_html_inline(r[0], image_ref)}</p>" for r in b.rows)
            out.append(f"<blockquote>{inner}</blockquote>")
        elif b.kind == "rule":
            out.append("<hr>")
        else:
            out.append(_html_table(b, image_ref))
    while stack:
        close_list()
    body = ""
    for frag in out:
        glue = "" if frag.startswith(("<li", "</li", "<ul", "<ol", "</ul", "</ol")) else "\n"
        body += (glue if body else "") + frag
    return (
        '<!DOCTYPE html>\n<html>\n<head>\n<meta charset="utf-8">\n'
        f"<title>{html_lib.escape(title)}</title>\n</head>\n<body>\n{body}\n</body>\n</html>\n"
    )


def _html_table(b: Block, image_ref: ImageRef) -> str:
    rows = []
    for i, row in enumerate(b.rows):
        tag = "th" if i == 0 else "td"
        cells = "".join(f"<{tag}>{_html_inline(c, image_ref)}</{tag}>" for c in row)
        rows.append(f"<tr>{cells}</tr>")
    if not rows:
        return "<table></table>"
    head, rest = rows[0], rows[1:]
    body = f"<tbody>{''.join(rest)}</tbody>" if rest else ""
    return f"<table><thead>{head}</thead>{body}</table>"


# --------------------------------------------------------------------------------------------
# Public DOCX -> text conversions
# --------------------------------------------------------------------------------------------


def _load_blocks(src: Path) -> tuple[list[Block], str]:
    doc = DocxHandler().open(Path(src))
    try:
        native = doc.native
        title = (native.core_properties.title or "").strip()
        return docx_blocks(native), title
    except CorruptFileError:
        raise
    except Exception as exc:
        raise CorruptFileError(f"Cannot convert {src}: {exc}") from exc
    finally:
        doc.close()


def docx_to_markdown(
    src: str | Path, dst: str | Path, *, media_dir: str | Path | None = None
) -> Path:
    """Convert a DOCX file to Markdown.

    Embedded images are written to ``media_dir`` (default ``<dst stem>_files`` next to ``dst``) and
    referenced with relative links.
    """
    dst_p = Path(dst)
    blocks, _ = _load_blocks(Path(src))
    media = Path(media_dir) if media_dir is not None else dst_p.with_name(f"{dst_p.stem}_files")
    counter = 0

    def image_ref(data: bytes, ext: str) -> str:
        nonlocal counter
        counter += 1
        media.mkdir(parents=True, exist_ok=True)
        name = f"image{counter}.{ext}"
        (media / name).write_bytes(data)
        try:
            return media.relative_to(dst_p.parent).as_posix() + "/" + name
        except ValueError:
            return (media / name).as_posix()

    dst_p.parent.mkdir(parents=True, exist_ok=True)
    dst_p.write_text(blocks_to_markdown(blocks, image_ref), encoding="utf-8")
    return dst_p


def docx_to_html(src: str | Path, dst: str | Path) -> Path:
    """Convert a DOCX file to a standalone HTML document (images embedded as data URIs)."""
    blocks, title = _load_blocks(Path(src))
    dst_p = Path(dst)

    def image_ref(data: bytes, ext: str) -> str:
        mime = "svg+xml" if ext == "svg" else ("jpeg" if ext == "jpg" else ext)
        return f"data:image/{mime};base64,{base64.b64encode(data).decode('ascii')}"

    dst_p.parent.mkdir(parents=True, exist_ok=True)
    dst_p.write_text(blocks_to_html(blocks, image_ref, title or dst_p.stem), encoding="utf-8")
    return dst_p


# --------------------------------------------------------------------------------------------
# HTML -> DOCX
# --------------------------------------------------------------------------------------------

_HEADINGS = {f"h{i}": i for i in range(1, 7)}
_SKIP = {"script", "style", "head"}
_INLINE_BLOCK_END = {"p", "div", "li", "blockquote", "pre", "tr", "section", "article"}


class _DocxBuilder(HTMLParser):
    """Event-driven HTML reader that writes paragraphs, lists, tables and runs into a DOCX."""

    def __init__(self, base_dir: Path | None) -> None:
        super().__init__(convert_charrefs=True)
        self.doc = new_docx()
        self.base_dir = base_dir
        self.runs: list[Run] = []
        self.bold = self.italic = self.strike = self.code = 0
        self.links: list[str | None] = []
        self.block: tuple[str, int] | None = None  # ("heading", n)
        self.lists: list[bool] = []
        self.pending_list: str | None = None
        self.quote = 0
        self.pre = 0
        self.skip = 0
        self.in_title = False
        self.title = ""
        self.table: list[list[list[list[Run]]]] | None = None
        self.row: list[list[list[Run]]] | None = None
        self.cell: list[list[Run]] | None = None
        self._ensure_code_style()

    # -- styles -----------------------------------------------------------------------------

    def _ensure_code_style(self) -> None:
        styles = self.doc.styles
        if "Code Block" not in [s.name for s in styles]:
            from docx.enum.style import WD_STYLE_TYPE

            style = styles.add_style("Code Block", WD_STYLE_TYPE.PARAGRAPH)
            style.base_style = styles["Normal"]
            style.font.name = _CODE_FONT
            style.font.size = Pt(10)

    # -- emitting ---------------------------------------------------------------------------

    def _list_style(self) -> str:
        ordered = self.lists[-1] if self.lists else False
        depth = min(max(len(self.lists), 1), 3)
        base = "List Number" if ordered else "List Bullet"
        return base if depth == 1 else f"{base} {depth}"

    def _trim(self) -> list[Run]:
        runs = [r for r in self.runs if r.image is not None or r.text]
        self.runs = []
        if runs and runs[0].image is None:
            runs[0].text = runs[0].text.lstrip()
        if runs and runs[-1].image is None:
            runs[-1].text = runs[-1].text.rstrip()
        return [r for r in runs if r.image is not None or r.text]

    def _flush(self) -> None:
        runs = self._trim()
        if self.cell is not None:
            if runs:
                self.cell.append(runs)
            return
        if not runs:
            return
        if self.block and self.block[0] == "heading":
            style = f"Heading {self.block[1]}"
        elif self.pending_list:
            style, self.pending_list = self.pending_list, None
        elif self.lists:
            style = "List Continue"
        elif self.quote:
            style = "Quote"
        else:
            style = "Normal"
        par = self.doc.add_paragraph(style=style)
        self._write_runs(par, runs)

    def _write_runs(self, par: Paragraph, runs: list[Run]) -> None:
        for r in runs:
            if r.image is not None:
                self._add_image(par, r)
                continue
            if r.href:
                self._add_hyperlink(par, r)
            else:
                self._style_run(par.add_run(r.text), r)

    @staticmethod
    def _style_run(run: DocxRun, r: Run) -> None:
        if r.bold:
            run.bold = True
        if r.italic:
            run.italic = True
        if r.strike:
            run.font.strike = True
        if r.code:
            run.font.name = _CODE_FONT
            rpr = run._r.get_or_add_rPr()
            fonts = rpr.find(qn("w:rFonts"))
            if fonts is not None:
                fonts.set(qn("w:eastAsia"), _CODE_FONT)

    def _add_hyperlink(self, par: Paragraph, r: Run) -> None:
        rid = par.part.relate_to(r.href or "", RT.HYPERLINK, is_external=True)
        link = OxmlElement("w:hyperlink")
        link.set(qn("r:id"), rid)
        par._p.append(link)
        run = par.add_run(r.text)
        self._style_run(run, r)
        run.font.color.rgb = RGBColor(0x05, 0x63, 0xC1)
        run.font.underline = True
        link.append(run._r)

    def _add_image(self, par: Paragraph, r: Run) -> None:
        assert r.image is not None
        try:
            pic = par.add_run().add_picture(io.BytesIO(r.image[0]))
        except Exception:  # unsupported image bytes: skip silently
            return
        if pic.width and pic.width > _MAX_PICTURE_WIDTH:
            ratio = _MAX_PICTURE_WIDTH / pic.width
            pic.height = Emu(int(pic.height * ratio))
            pic.width = _MAX_PICTURE_WIDTH

    def _flush_code(self, text: str) -> None:
        for line in text.rstrip("\n").split("\n"):
            par = self.doc.add_paragraph(style="Code Block")
            self._style_run(par.add_run(line), Run(line, code=True))

    def _add_rule(self) -> None:
        par = self.doc.add_paragraph()
        borders = OxmlElement("w:pBdr")
        bottom = OxmlElement("w:bottom")
        for key, value in (("val", "single"), ("sz", "6"), ("space", "1"), ("color", "808080")):
            bottom.set(qn(f"w:{key}"), value)
        borders.append(bottom)
        par._p.get_or_add_pPr().append(borders)

    def _build_table(self, rows: list[list[list[list[Run]]]]) -> None:
        width = max((len(r) for r in rows), default=0)
        if not width:
            return
        table = self.doc.add_table(rows=len(rows), cols=width)
        table.style = "Table Grid"
        for ri, row in enumerate(rows):
            for ci, cell_pars in enumerate(row):
                cell = table.cell(ri, ci)
                for pi, runs in enumerate(cell_pars):
                    par = cell.paragraphs[0] if pi == 0 else cell.add_paragraph()
                    for r in runs:
                        if ri == 0:
                            r.bold = True
                    self._write_runs(par, runs)

    # -- parser events ----------------------------------------------------------------------

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        a = {k: v or "" for k, v in attrs}
        if tag in _SKIP:
            self.skip += 1
            return
        if tag == "title":
            self.in_title = True
            return
        if self.skip:
            return
        if tag in _HEADINGS:
            self._flush()
            self.block = ("heading", _HEADINGS[tag])
        elif tag in ("p", "div", "section", "article"):
            self._flush()
        elif tag in ("ul", "ol"):
            self._flush()
            self.lists.append(tag == "ol")
        elif tag == "li":
            self._flush()
            self.pending_list = self._list_style()
        elif tag == "blockquote":
            self._flush()
            self.quote += 1
        elif tag == "pre":
            self._flush()
            self.pre += 1
            self.runs = []
        elif tag == "table":
            self._flush()
            if self.table is None:
                self.table = []
        elif tag == "tr" and self.table is not None:
            self.row = []
        elif tag in ("td", "th") and self.row is not None:
            self.cell = []
            self.runs = []
            if tag == "th":
                self.bold += 1
        elif tag in ("strong", "b"):
            self.bold += 1
        elif tag in ("em", "i"):
            self.italic += 1
        elif tag in ("del", "s", "strike"):
            self.strike += 1
        elif tag == "code" and not self.pre:
            self.code += 1
        elif tag == "a":
            self.links.append(a.get("href") or None)
        elif tag == "br":
            self.runs.append(self._run("\n"))
        elif tag == "hr":
            self._flush()
            self._add_rule()
        elif tag == "img":
            self._img(a)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)
        if tag not in ("br", "hr", "img"):
            self.handle_endtag(tag)

    def handle_endtag(self, tag: str) -> None:
        if tag in _SKIP:
            self.skip = max(0, self.skip - 1)
            return
        if tag == "title":
            self.in_title = False
            return
        if self.skip:
            return
        if tag in _HEADINGS:
            self._flush()
            self.block = None
        elif tag in ("p", "div", "section", "article"):
            self._flush()
        elif tag in ("ul", "ol"):
            self._flush()
            if self.lists:
                self.lists.pop()
            self.pending_list = None
        elif tag == "li":
            self._flush()
            self.pending_list = None
        elif tag == "blockquote":
            self._flush()
            self.quote = max(0, self.quote - 1)
        elif tag == "pre":
            text = "".join(r.text for r in self.runs)
            self.runs = []
            self.pre = max(0, self.pre - 1)
            self._flush_code(text)
        elif tag in ("td", "th") and self.cell is not None and self.row is not None:
            self._flush()
            if tag == "th":
                self.bold = max(0, self.bold - 1)
            self.row.append(self.cell)
            self.cell = None
        elif tag == "tr" and self.row is not None and self.table is not None:
            self.table.append(self.row)
            self.row = None
        elif tag == "table" and self.table is not None:
            rows, self.table = self.table, None
            self._build_table(rows)
        elif tag in ("strong", "b"):
            self.bold = max(0, self.bold - 1)
        elif tag in ("em", "i"):
            self.italic = max(0, self.italic - 1)
        elif tag in ("del", "s", "strike"):
            self.strike = max(0, self.strike - 1)
        elif tag == "code" and not self.pre:
            self.code = max(0, self.code - 1)
        elif tag == "a" and self.links:
            self.links.pop()

    def handle_data(self, data: str) -> None:
        if self.in_title:
            self.title += data
            return
        if self.skip:
            return
        if not self.pre:
            data = re.sub(r"\s+", " ", data)
            if not self.runs and data.strip() == "":
                return
        if data:
            self.runs.append(self._run(data))

    def _run(self, text: str) -> Run:
        href = next((link for link in reversed(self.links) if link), None)
        return Run(
            text,
            bold=bool(self.bold),
            italic=bool(self.italic),
            strike=bool(self.strike),
            code=bool(self.code or self.pre),
            href=href,
        )

    def _img(self, attrs: dict[str, str]) -> None:
        src = attrs.get("src", "")
        alt = attrs.get("alt", "")
        loaded = self._load_image(src)
        if loaded is not None:
            self.runs.append(Run(image=loaded))
        elif alt:
            self.runs.append(self._run(alt))

    def _load_image(self, src: str) -> tuple[bytes, str] | None:
        if src.startswith("data:"):
            m = re.match(r"data:image/([\w.+-]+);base64,(.*)", src, re.DOTALL)
            if not m:
                return None
            try:
                return base64.b64decode(m.group(2)), m.group(1)
            except (binascii.Error, ValueError):
                return None
        if not src or "://" in src or self.base_dir is None:
            return None
        from urllib.parse import unquote

        try:
            path = (self.base_dir / unquote(src)).resolve()
            if not path.is_relative_to(self.base_dir.resolve()) or not path.is_file():
                return None
            return path.read_bytes(), path.suffix.lstrip(".").lower()
        except OSError:
            return None


def _build_docx(html: str, dst: Path, base_dir: Path | None) -> Path:
    builder = _DocxBuilder(base_dir)
    builder.feed(html)
    builder.close()
    builder._flush()
    title = builder.title.strip()
    if title:
        builder.doc.core_properties.title = title
    dst.parent.mkdir(parents=True, exist_ok=True)
    builder.doc.save(str(dst))
    return dst


def _read_text(src: Path) -> str:
    data = src.read_bytes()
    for enc in ("utf-8-sig", "utf-16"):
        try:
            return data.decode(enc)
        except UnicodeError:
            continue
    return data.decode("cp950", errors="replace")


def html_to_docx(src: str | Path, dst: str | Path) -> Path:
    """Convert an HTML file to DOCX (local images resolve relative to the HTML file)."""
    src_p = Path(src)
    return _build_docx(_read_text(src_p), Path(dst), src_p.parent)


def markdown_to_docx(src: str | Path, dst: str | Path) -> Path:
    """Convert a Markdown file (CommonMark + tables + strikethrough) to DOCX."""
    src_p = Path(src)
    md = MarkdownIt("commonmark").enable("table").enable("strikethrough")
    return _build_docx(md.render(_read_text(src_p)), Path(dst), src_p.parent)


# --------------------------------------------------------------------------------------------
# Graph registration
# --------------------------------------------------------------------------------------------


def register_docx_text(target: ConversionGraph | None = None) -> None:
    """Register DOCX <-> Markdown/HTML converters on ``target`` (default: global graph)."""
    g = target if target is not None else graph

    def wrap(fn: Callable[[Path, Path], Path]) -> Callable[[Path, Path], None]:
        def func(src: Path, dst: Path) -> None:
            fn(src, dst)

        return func

    g.add(Converter("docx", "markdown", wrap(docx_to_markdown), name="docx->markdown"))
    g.add(Converter("docx", "html", wrap(docx_to_html), name="docx->html"))
    g.add(Converter("markdown", "docx", wrap(markdown_to_docx), name="markdown->docx"))
    g.add(Converter("html", "docx", wrap(html_to_docx), name="html->docx"))
