"""Raster image document and handlers built on Pillow."""

from __future__ import annotations

import io
from datetime import datetime
from pathlib import Path
from typing import Any, ClassVar

from PIL import ExifTags, Image, ImageSequence, UnidentifiedImageError

from anydoc.core.errors import CorruptFileError, OperationNotSupportedError
from anydoc.core.model import Capability, Metadata, Part
from anydoc.core.registry import FormatHandler

_PIL_FORMATS = {
    "png": "PNG",
    "jpeg": "JPEG",
    "gif": "GIF",
    "bmp": "BMP",
    "webp": "WEBP",
    "tiff": "TIFF",
}
_FORMAT_ALIASES = {"jpg": "jpeg", "tif": "tiff"}
_MIMES = {
    "png": "image/png",
    "jpeg": "image/jpeg",
    "gif": "image/gif",
    "bmp": "image/bmp",
    "webp": "image/webp",
    "tiff": "image/tiff",
}
_EXIF_IFDS = {0x8769: "Exif", 0x8825: "GPS"}


def _norm_format(fmt: str) -> str:
    fmt = fmt.lower().lstrip(".")
    return _FORMAT_ALIASES.get(fmt, fmt)


def _jsonable(value: Any) -> Any:
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace").rstrip("\x00")
    if isinstance(value, (tuple, list)):
        return [_jsonable(v) for v in value]
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if (
        hasattr(value, "numerator")
        and hasattr(value, "denominator")
        and not isinstance(value, (int, float))
    ):
        return float(value)
    return value


def _parse_exif_date(value: Any) -> datetime | None:
    try:
        return datetime.strptime(str(value).strip("\x00 "), "%Y:%m:%d %H:%M:%S")
    except ValueError:
        return None


class ImageDocument:
    """An opened raster image (possibly multi-frame) with simple transformations."""

    def __init__(self, data: bytes, *, path: Path | None = None) -> None:
        self.path = path
        try:
            opened = Image.open(io.BytesIO(data))
            opened.load()
        except (UnidentifiedImageError, OSError, ValueError, SyntaxError) as exc:
            raise CorruptFileError(f"Invalid image: {exc}") from exc
        self._image: Image.Image = opened
        self.format = _norm_format(opened.format or "")
        self._frames: list[Image.Image] = [f.copy() for f in ImageSequence.Iterator(opened)]
        self.metadata = self._build_metadata()

    # -- properties --------------------------------------------------------

    @property
    def size(self) -> tuple[int, int]:
        """Pixel size ``(width, height)`` of the first frame."""
        return self._frames[0].size

    @property
    def mode(self) -> str:
        """Pillow mode of the first frame (``RGB``, ``RGBA``, ``L``, ...)."""
        return self._frames[0].mode

    @property
    def frame_count(self) -> int:
        """Number of frames (pages of a TIFF, frames of a GIF/WebP)."""
        return len(self._frames)

    def exif(self) -> dict[str, Any]:
        """Return EXIF tags by name (Exif/GPS sub-IFDs merged); empty if none."""
        raw = self._image.getexif()
        out: dict[str, Any] = {}
        for tag, value in raw.items():
            if tag in _EXIF_IFDS:
                continue
            out[ExifTags.TAGS.get(tag, str(tag))] = _jsonable(value)
        for ifd_id, label in _EXIF_IFDS.items():
            try:
                sub = raw.get_ifd(ifd_id)
            except Exception:  # malformed sub-IFD
                continue
            tags = ExifTags.GPSTAGS if label == "GPS" else ExifTags.TAGS
            for tag, value in sub.items():
                out[tags.get(tag, str(tag))] = _jsonable(value)
        return out

    def frame(self, index: int = 0) -> Image.Image:
        """Return a copy of one frame as a Pillow image."""
        if not 0 <= index < len(self._frames):
            raise IndexError(f"No such frame: {index}")
        return self._frames[index].copy()

    # -- transformations ---------------------------------------------------

    def _derive(self, frames: list[Image.Image]) -> ImageDocument:
        new = object.__new__(ImageDocument)
        new.path = None
        new._image = frames[0]
        new.format = self.format
        new._frames = frames
        new.metadata = new._build_metadata()
        return new

    def resize(
        self,
        size: tuple[int, int] | None = None,
        *,
        scale: float | None = None,
        keep_aspect: bool = False,
    ) -> ImageDocument:
        """Return a resized copy; give ``size`` (w, h) or ``scale``.

        With ``keep_aspect`` the image is fitted inside ``size`` preserving proportions.
        """
        w, h = self.size
        if scale is not None:
            if scale <= 0:
                raise ValueError("scale must be positive")
            target = (max(1, round(w * scale)), max(1, round(h * scale)))
        elif size is not None:
            if size[0] <= 0 or size[1] <= 0:
                raise ValueError("size must be positive")
            if keep_aspect:
                ratio = min(size[0] / w, size[1] / h)
                target = (max(1, round(w * ratio)), max(1, round(h * ratio)))
            else:
                target = (int(size[0]), int(size[1]))
        else:
            raise ValueError("resize needs size or scale")
        return self._derive([f.resize(target, Image.Resampling.LANCZOS) for f in self._frames])

    def rotate(self, degrees: float, *, expand: bool = True) -> ImageDocument:
        """Return a copy rotated counter-clockwise by ``degrees``."""
        return self._derive([f.rotate(degrees, expand=expand) for f in self._frames])

    def to_bytes(self, fmt: str | None = None, *, quality: int = 90) -> bytes:
        """Encode all frames to ``fmt`` (default: own format) and return the bytes.

        Multi-frame images keep their frames when the target is TIFF, GIF or WebP;
        otherwise only the first frame is written. Alpha is flattened for JPEG.
        """
        target = _norm_format(fmt or self.format)
        pil_fmt = _PIL_FORMATS.get(target)
        if pil_fmt is None:
            raise OperationNotSupportedError(f"Cannot write image format: {target}")
        frames = [self._prepare(f, target) for f in self._frames]
        buf = io.BytesIO()
        kwargs: dict[str, Any] = {}
        if target in ("jpeg", "webp"):
            kwargs["quality"] = quality
        if len(frames) > 1 and target in ("tiff", "gif", "webp"):
            kwargs.update(save_all=True, append_images=frames[1:])
        frames[0].save(buf, format=pil_fmt, **kwargs)
        return buf.getvalue()

    @staticmethod
    def _prepare(frame: Image.Image, target: str) -> Image.Image:
        if target == "jpeg" and frame.mode not in ("RGB", "L", "CMYK"):
            if frame.mode in ("RGBA", "LA", "P"):
                rgba = frame.convert("RGBA")
                background = Image.new("RGB", rgba.size, "white")
                background.paste(rgba, mask=rgba.getchannel("A"))
                return background
            return frame.convert("RGB")
        if target == "bmp" and frame.mode not in ("RGB", "L", "P", "1"):
            return frame.convert("RGB")
        return frame

    def convert(self, fmt: str, path: Path | None = None, *, quality: int = 90) -> bytes:
        """Convert to ``fmt`` and return the bytes; also write them to ``path`` if given."""
        data = self.to_bytes(fmt, quality=quality)
        if path is not None:
            path.write_bytes(data)
        return data

    # -- Document protocol -------------------------------------------------

    def _build_metadata(self) -> Metadata:
        width, height = self._frames[0].size
        exif: dict[str, Any] = {}
        try:
            exif = self.exif()
        except Exception:  # damaged EXIF must not prevent opening
            exif = {}
        extra: dict[str, Any] = {
            "width": width,
            "height": height,
            "mode": self._frames[0].mode,
            "frames": len(self._frames),
            "exif": exif,
        }
        dpi = self._image.info.get("dpi")
        if dpi:
            extra["dpi"] = [round(float(dpi[0]), 1), round(float(dpi[1]), 1)]
        return Metadata(
            format=self.format,
            mime=_MIMES.get(self.format, "application/octet-stream"),
            title=exif.get("ImageDescription") or None,
            author=exif.get("Artist") or None,
            created=_parse_exif_date(exif.get("DateTimeOriginal") or exif.get("DateTime")),
            page_count=len(self._frames),
            extra=extra,
        )

    def parts(self) -> list[Part]:
        """Return one ``frame`` part per frame with its pixel size."""
        return [
            Part(
                kind="frame", index=i, name=f"Frame {i + 1}", size=(float(f.width), float(f.height))
            )
            for i, f in enumerate(self._frames)
        ]

    def text(self) -> str:
        """Images carry no text; returns an empty string."""
        return ""

    def save(self, path: Path | None = None) -> Path:
        """Write the image to ``path`` (default: source path); format follows the suffix."""
        target = path or self.path
        if target is None:
            raise ValueError("No path given and document has no source path")
        suffix = target.suffix.lstrip(".")
        fmt = _norm_format(suffix) if _norm_format(suffix) in _PIL_FORMATS else self.format
        target.write_bytes(self.to_bytes(fmt))
        return target

    def close(self) -> None:
        """Release the underlying Pillow image."""
        self._image.close()


class _RasterHandler(FormatHandler):
    """Shared open logic for raster image handlers."""

    capabilities: ClassVar[frozenset[Capability]] = frozenset(
        {Capability.READ, Capability.WRITE, Capability.CONVERT, Capability.EDIT}
    )
    _magic: ClassVar[tuple[bytes, ...]] = ()

    @classmethod
    def sniff(cls, head: bytes, path: Path | None) -> float:
        """Return confidence from magic bytes, else from the extension."""
        if head.startswith(cls._magic):
            return 1.0
        if path is not None and path.suffix.lower().lstrip(".") in cls.extensions:
            return 0.4
        return 0.0

    def open(self, source: Path | bytes) -> ImageDocument:
        """Open an image from a path or bytes; undecodable input raises ``CorruptFileError``."""
        path = source if isinstance(source, Path) else None
        if isinstance(source, Path):
            try:
                data = source.read_bytes()
            except OSError as exc:
                raise CorruptFileError(f"Cannot read {source}: {exc}") from exc
        else:
            data = source
        return ImageDocument(data, path=path)


class PngHandler(_RasterHandler):
    """Handler for PNG."""

    format_id: ClassVar[str] = "png"
    extensions: ClassVar[tuple[str, ...]] = ("png",)
    mime_types: ClassVar[tuple[str, ...]] = ("image/png",)
    _magic: ClassVar[tuple[bytes, ...]] = (b"\x89PNG\r\n\x1a\n",)


class JpegHandler(_RasterHandler):
    """Handler for JPEG."""

    format_id: ClassVar[str] = "jpeg"
    extensions: ClassVar[tuple[str, ...]] = ("jpg", "jpeg")
    mime_types: ClassVar[tuple[str, ...]] = ("image/jpeg",)
    _magic: ClassVar[tuple[bytes, ...]] = (b"\xff\xd8\xff",)


class GifHandler(_RasterHandler):
    """Handler for GIF (animated frames exposed as parts)."""

    format_id: ClassVar[str] = "gif"
    extensions: ClassVar[tuple[str, ...]] = ("gif",)
    mime_types: ClassVar[tuple[str, ...]] = ("image/gif",)
    _magic: ClassVar[tuple[bytes, ...]] = (b"GIF87a", b"GIF89a")


class BmpHandler(_RasterHandler):
    """Handler for BMP."""

    format_id: ClassVar[str] = "bmp"
    extensions: ClassVar[tuple[str, ...]] = ("bmp",)
    mime_types: ClassVar[tuple[str, ...]] = ("image/bmp",)
    _magic: ClassVar[tuple[bytes, ...]] = (b"BM",)


class WebpHandler(_RasterHandler):
    """Handler for WebP."""

    format_id: ClassVar[str] = "webp"
    extensions: ClassVar[tuple[str, ...]] = ("webp",)
    mime_types: ClassVar[tuple[str, ...]] = ("image/webp",)

    @classmethod
    def sniff(cls, head: bytes, path: Path | None) -> float:
        """Return confidence from the RIFF/WEBP header, else from the extension."""
        if head.startswith(b"RIFF") and head[8:12] == b"WEBP":
            return 1.0
        return super().sniff(head, path)


class TiffHandler(_RasterHandler):
    """Handler for TIFF (multi-page files expose one part per page)."""

    format_id: ClassVar[str] = "tiff"
    extensions: ClassVar[tuple[str, ...]] = ("tif", "tiff")
    mime_types: ClassVar[tuple[str, ...]] = ("image/tiff",)
    _magic: ClassVar[tuple[bytes, ...]] = (b"II*\x00", b"MM\x00*")
