"""Read-oriented PPTX document and handler built on python-pptx."""

from __future__ import annotations

import io
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, ClassVar

from pptx import Presentation
from pptx.enum.shapes import MSO_SHAPE_TYPE
from pptx.util import Inches

from anydoc.core.errors import CorruptFileError
from anydoc.core.model import Capability, Metadata, Part
from anydoc.core.registry import FormatHandler
from anydoc.formats.pptx import shapes as _shapes
from anydoc.formats.pptx import slides as _slides
from anydoc.formats.pptx.shapes import Length, ShapeFormat

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

    # -- slide management (T070) -------------------------------------------

    def _touch(self) -> None:
        self.metadata = self._build_metadata()

    def add_slide(
        self,
        layout: int | str = 1,
        *,
        index: int | None = None,
        title: str | None = None,
        body: str | list[str] | None = None,
        notes: str | None = None,
    ) -> int:
        """Add a slide from a layout (index or name); return its 0-based index."""
        result = _slides.add_slide(
            self._prs, layout, index=index, title=title, body=body, notes=notes
        )
        self._touch()
        return result

    def delete_slide(self, index: int) -> None:
        """Delete the slide at 0-based ``index``."""
        _slides.delete_slide(self._prs, index)
        self._touch()

    def duplicate_slide(self, index: int, *, to: int | None = None) -> int:
        """Copy a slide (default: right after the source); return the copy's index."""
        result = _slides.duplicate_slide(self._prs, index, to=to)
        self._touch()
        return result

    def move_slide(self, index: int, to: int) -> None:
        """Move a slide to a new 0-based position."""
        _slides.move_slide(self._prs, index, to)
        self._touch()

    def reorder_slides(self, order: list[int]) -> None:
        """Reorder all slides; ``order`` is a permutation of the current indexes."""
        _slides.reorder_slides(self._prs, order)
        self._touch()

    def set_notes(self, index: int, text: str) -> None:
        """Set the speaker notes of a slide (empty string clears them)."""
        _slides.set_notes(self._prs, index, text)

    def get_notes(self, index: int) -> str:
        """Return the speaker notes of a slide."""
        return _slides.get_notes(self._prs, index)

    # -- text, pictures and shapes (T071) ----------------------------------

    def add_textbox(
        self,
        slide: int,
        text: str | Sequence[str],
        *,
        left: Length,
        top: Length,
        width: Length,
        height: Length,
        **options: Any,
    ) -> int:
        """Add a text box; return its shape id (options: see ``shapes.add_textbox``)."""
        return _shapes.add_textbox(
            self._prs, slide, text, left=left, top=top, width=width, height=height, **options
        )

    def set_shape_text(self, slide: int, shape_id: int, text: str | Sequence[str]) -> None:
        """Replace the text of a shape (string or list of paragraphs)."""
        _shapes.set_text(self._prs, slide, shape_id, text)
        self._touch()

    def add_shape_paragraph(self, slide: int, shape_id: int, text: str, **options: Any) -> int:
        """Append a paragraph to a shape; return its index (see ``shapes.add_paragraph``)."""
        return _shapes.add_paragraph(self._prs, slide, shape_id, text, **options)

    def format_text(self, slide: int, shape_id: int, **options: Any) -> None:
        """Format a shape's text (font, colour, alignment…; see ``shapes.format_text``)."""
        _shapes.format_text(self._prs, slide, shape_id, **options)

    def add_picture(
        self,
        slide: int,
        image: Path | str | bytes,
        *,
        left: Length,
        top: Length,
        width: Length | None = None,
        height: Length | None = None,
    ) -> int:
        """Insert a picture (aspect ratio kept when one dimension is given); return its id."""
        return _shapes.add_picture(
            self._prs, slide, image, left=left, top=top, width=width, height=height
        )

    def add_shape(
        self,
        slide: int,
        kind: str,
        *,
        left: Length,
        top: Length,
        width: Length,
        height: Length,
        **options: Any,
    ) -> int:
        """Add a basic auto shape (``shapes.SHAPE_KINDS``); return its shape id."""
        return _shapes.add_shape(
            self._prs, slide, kind, left=left, top=top, width=width, height=height, **options
        )

    def add_line(
        self,
        slide: int,
        *,
        x1: Length,
        y1: Length,
        x2: Length,
        y2: Length,
        color: str | None = None,
        width: float | None = None,
    ) -> int:
        """Add a straight line; return its shape id."""
        return _shapes.add_line(
            self._prs, slide, x1=x1, y1=y1, x2=x2, y2=y2, color=color, width=width
        )

    def style_shape(
        self,
        slide: int,
        shape_id: int,
        *,
        fill: str | None = None,
        line_color: str | None = None,
        line_width: float | None = None,
    ) -> None:
        """Set fill colour, outline colour (``"none"`` removes) and outline width (pt)."""
        _shapes.style_shape(
            self._prs, slide, shape_id, fill=fill, line_color=line_color, line_width=line_width
        )

    def set_geometry(
        self,
        slide: int,
        shape_id: int,
        *,
        left: Length | None = None,
        top: Length | None = None,
        width: Length | None = None,
        height: Length | None = None,
        rotation: float | None = None,
    ) -> None:
        """Move, resize and/or rotate a shape; omitted values are kept."""
        _shapes.set_geometry(
            self._prs,
            slide,
            shape_id,
            left=left,
            top=top,
            width=width,
            height=height,
            rotation=rotation,
        )

    def delete_shape(self, slide: int, shape_id: int) -> None:
        """Remove a shape from a slide."""
        _shapes.delete_shape(self._prs, slide, shape_id)

    def set_z_order(self, slide: int, shape_id: int, position: int | str) -> None:
        """Restack a shape: ``"front"``, ``"back"`` or a 0-based position (0 = bottom)."""
        _shapes.set_z_order(self._prs, slide, shape_id, position)

    def shape_format(self, slide: int, shape_id: int) -> ShapeFormat:
        """Return geometry, fill/outline and text formatting of a shape."""
        return _shapes.shape_format(self._prs, slide, shape_id)

    def to_bytes(self) -> bytes:
        """Serialize the presentation to PPTX bytes."""
        buf = io.BytesIO()
        self._prs.save(buf)
        return buf.getvalue()


class PptxHandler(FormatHandler):
    """Handler for PowerPoint ``.pptx`` presentations."""

    format_id: ClassVar[str] = "pptx"
    extensions: ClassVar[tuple[str, ...]] = ("pptx",)
    mime_types: ClassVar[tuple[str, ...]] = (_PPTX_MIME,)
    capabilities: ClassVar[frozenset[Capability]] = frozenset(
        {Capability.READ, Capability.WRITE, Capability.EDIT, Capability.EXTRACT_TEXT}
    )

    @classmethod
    def sniff(cls, head: bytes, path: Path | None) -> float:
        """Return confidence from the extension (content detection lives in ``detect``)."""
        if path is not None and path.suffix.lower() == ".pptx":
            return 0.9
        return 0.0

    def create(self) -> PptxDocument:
        """Create an empty 16:9 presentation using the default template."""
        prs = Presentation()
        prs.slide_width = Inches(13.333)
        prs.slide_height = Inches(7.5)
        return PptxDocument(prs)

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
