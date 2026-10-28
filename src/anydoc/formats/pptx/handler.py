"""Read-oriented PPTX document and handler built on python-pptx."""

from __future__ import annotations

import io
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, ClassVar

from pptx import Presentation
from pptx.enum.shapes import MSO_SHAPE_TYPE

from anydoc.core.errors import CorruptFileError
from anydoc.core.model import Capability, Metadata, Part
from anydoc.core.registry import FormatHandler

_PPTX_MIME = "application/vnd.openxmlformats-officedocument.presentationml.presentation"


@dataclass(frozen=True)
class ShapeInfo:
    """A node of a slide's shape tree.

    ``kind`` is one of ``text``, ``picture``, ``table``, ``chart``, ``group``, ``shape``.
    ``text`` is the shape's own text (a table is its cells joined by tabs/newlines);
    ``children`` is only populated for groups. Positions are EMU or ``None`` if inherited.
    """

    shape_id: int
    name: str
    kind: str
    text: str = ""
    left: int | None = None
    top: int | None = None
    width: int | None = None
    height: int | None = None
    placeholder: bool = False
    table: list[list[str]] | None = None
    chart_type: str | None = None
    children: list[ShapeInfo] = field(default_factory=list)


@dataclass(frozen=True)
class SlideInfo:
    """A slide with its layout name, title, shape tree and speaker notes."""

    index: int
    layout: str
    title: str | None
    shapes: list[ShapeInfo]
    notes: str = ""


def _kind(shape: Any) -> str:
    if shape.shape_type == MSO_SHAPE_TYPE.GROUP:
        return "group"
    if getattr(shape, "has_table", False) and shape.has_table:
        return "table"
    if getattr(shape, "has_chart", False) and shape.has_chart:
        return "chart"
    if shape.shape_type == MSO_SHAPE_TYPE.PICTURE or type(shape).__name__ == "Picture":
        return "picture"
    if getattr(shape, "has_text_frame", False) and shape.has_text_frame:
        return "text"
    return "shape"


def _shape_info(shape: Any) -> ShapeInfo:
    kind = _kind(shape)
    text = ""
    table: list[list[str]] | None = None
    chart_type: str | None = None
    children: list[ShapeInfo] = []
    if kind == "table":
        table = [[cell.text for cell in row.cells] for row in shape.table.rows]
        text = "\n".join("\t".join(r) for r in table)
    elif kind == "chart":
        chart_type = str(shape.chart.chart_type)
        if shape.chart.has_title:
            text = shape.chart.chart_title.text_frame.text
    elif kind == "group":
        children = [_shape_info(s) for s in shape.shapes]
    elif kind in ("text", "shape") and getattr(shape, "has_text_frame", False):
        text = shape.text_frame.text
    return ShapeInfo(
        shape_id=shape.shape_id,
        name=shape.name,
        kind=kind,
        text=text,
        left=shape.left,
        top=shape.top,
        width=shape.width,
        height=shape.height,
        placeholder=bool(shape.is_placeholder),
        table=table,
        chart_type=chart_type,
        children=children,
    )


def _flatten_text(shapes: list[ShapeInfo]) -> list[str]:
    out: list[str] = []
    for s in shapes:
        if s.kind == "group":
            out.extend(_flatten_text(s.children))
        elif s.text:
            out.append(s.text)
    return out


class PptxDocument:
    """An opened PPTX presentation; ``save`` rewrites it via python-pptx."""

    format = "pptx"

    def __init__(self, prs: Any, *, path: Path | None = None) -> None:
        self._prs = prs
        self.path = path
        self.metadata = self._build_metadata()

    @property
    def native(self) -> Any:
        """The underlying ``pptx.Presentation``."""
        return self._prs

    # -- structure ---------------------------------------------------------

    def _slide_info(self, index: int, slide: Any) -> SlideInfo:
        title_shape = slide.shapes.title
        notes = slide.notes_slide.notes_text_frame.text if slide.has_notes_slide else ""
        return SlideInfo(
            index=index,
            layout=slide.slide_layout.name,
            title=title_shape.text_frame.text if title_shape is not None else None,
            shapes=[_shape_info(s) for s in slide.shapes],
            notes=notes or "",
        )

    def slides(self) -> list[SlideInfo]:
        """Return every slide in presentation order."""
        return [self._slide_info(i, s) for i, s in enumerate(self._prs.slides)]

    def slide(self, index: int) -> SlideInfo:
        """Return one slide by 0-based index."""
        slides = self._prs.slides
        if not 0 <= index < len(slides):
            raise IndexError(f"No such slide: {index}")
        return self._slide_info(index, slides[index])

    def layouts(self) -> list[str]:
        """Return the names of the layouts available in the presentation."""
        return [layout.name for layout in self._prs.slide_layouts]

    def slide_text(self, index: int) -> str:
        """Return a slide's text (shape order, groups flattened), excluding notes."""
        return "\n".join(_flatten_text(self.slide(index).shapes))

    # -- Document protocol -------------------------------------------------

    def _build_metadata(self) -> Metadata:
        props = self._prs.core_properties
        return Metadata(
            format="pptx",
            mime=_PPTX_MIME,
            title=props.title or None,
            author=props.author or None,
            created=props.created,
            modified=props.modified,
            page_count=len(self._prs.slides),
            extra={
                "subject": props.subject or None,
                "keywords": props.keywords or None,
                "last_modified_by": props.last_modified_by or None,
                "slide_count": len(self._prs.slides),
                "slide_width": self._prs.slide_width,
                "slide_height": self._prs.slide_height,
                "layouts": self.layouts(),
            },
        )

    def parts(self) -> list[Part]:
        """Return one part per slide (named by its title when it has one)."""
        return [
            Part(kind="slide", index=s.index, name=s.title or f"Slide {s.index + 1}")
            for s in self.slides()
        ]

    def text(self) -> str:
        """Return all slide text in order, each slide followed by its notes."""
        blocks: list[str] = []
        for s in self.slides():
            lines = _flatten_text(s.shapes)
            if s.notes:
                lines.append(s.notes)
            blocks.append("\n".join(lines))
        return "\n\n".join(blocks)

    def save(self, path: Path | None = None) -> Path:
        """Write the presentation to ``path`` (default: the source path) and return it."""
        target = path or self.path
        if target is None:
            raise ValueError("No path given and document has no source path")
        self._prs.save(str(target))
        return target

    def close(self) -> None:
        """Release resources (nothing to close for python-pptx)."""


class PptxHandler(FormatHandler):
    """Handler for PowerPoint ``.pptx`` presentations."""

    format_id: ClassVar[str] = "pptx"
    extensions: ClassVar[tuple[str, ...]] = ("pptx",)
    mime_types: ClassVar[tuple[str, ...]] = (_PPTX_MIME,)
    capabilities: ClassVar[frozenset[Capability]] = frozenset(
        {Capability.READ, Capability.EXTRACT_TEXT}
    )

    @classmethod
    def sniff(cls, head: bytes, path: Path | None) -> float:
        """Return confidence from the extension (content detection lives in ``detect``)."""
        if path is not None and path.suffix.lower() == ".pptx":
            return 0.9
        return 0.0

    def open(self, source: Path | bytes) -> PptxDocument:
        """Open a PPTX from a path or bytes; invalid packages raise ``CorruptFileError``."""
        path = source if isinstance(source, Path) else None
        if isinstance(source, Path):
            try:
                data = source.read_bytes()
            except OSError as exc:
                raise CorruptFileError(f"Cannot read {source}: {exc}") from exc
        else:
            data = source
        try:
            return PptxDocument(Presentation(io.BytesIO(data)), path=path)
        except Exception as exc:  # python-pptx raises many types for bad packages
            raise CorruptFileError(f"Invalid PPTX: {exc}") from exc
