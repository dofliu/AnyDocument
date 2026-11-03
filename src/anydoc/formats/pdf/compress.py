"""PDF compression / optimisation: image resampling, duplicate-object removal, stream deflate.

Functions take and return PDF ``bytes`` like :mod:`anydoc.formats.pdf.pages`.
"""

from __future__ import annotations

import io
from dataclasses import asdict, dataclass
from typing import Any

from PIL import Image
from pypdf import PdfWriter

from anydoc.core.errors import CorruptFileError
from anydoc.formats.pdf.pages import _dump, _reader

__all__ = ["CompressResult", "compress_pdf", "compress_pdf_with_stats"]


@dataclass(frozen=True)
class CompressResult:
    """Outcome of a compression run."""

    data: bytes
    original_size: int
    new_size: int
    images_recompressed: int
    images_downscaled: int

    @property
    def ratio(self) -> float:
        """``new_size / original_size`` (1.0 means no saving)."""
        return self.new_size / self.original_size if self.original_size else 1.0

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serialisable summary (without the PDF bytes)."""
        d = asdict(self)
        d.pop("data")
        d["ratio"] = round(self.ratio, 4)
        return d


def _optimise_image(img: Any, quality: int, max_side: int | None) -> tuple[bool, bool]:
    """Re-encode one image XObject as JPEG; return ``(recompressed, downscaled)``."""
    try:
        pil = img.image
    except Exception:
        return False, False  # unsupported filter / mask-only image: leave untouched
    if pil is None or pil.mode in ("1", "P", "LA", "RGBA", "PA"):
        return False, False  # keep palette / alpha images lossless
    if pil.mode not in ("RGB", "L"):
        try:
            pil = pil.convert("RGB")
        except Exception:
            return False, False
    downscaled = False
    if max_side and max(pil.size) > max_side:
        factor = max_side / max(pil.size)
        pil = pil.resize(
            (max(1, round(pil.width * factor)), max(1, round(pil.height * factor))),
            Image.Resampling.LANCZOS,
        )
        downscaled = True
    try:
        before = len(img.indirect_reference.get_object().get_data())
    except Exception:
        before = 0
    probe = io.BytesIO()
    pil.save(probe, format="JPEG", quality=quality, optimize=True)
    if not downscaled and before and probe.tell() >= before:
        return False, False  # re-encoding would not help
    try:
        img.replace(pil, quality=quality)
    except Exception:
        return False, False
    return True, downscaled


def compress_pdf_with_stats(
    data: bytes,
    *,
    image_quality: int = 70,
    max_image_side: int | None = 1600,
    recompress_images: bool = True,
    password: str = "",
) -> CompressResult:
    """Optimise a PDF and report what was done.

    Steps: JPEG re-encoding (and downscaling to ``max_image_side`` pixels on the longest side)
    of RGB/greyscale images, merging of identical objects, dropping of orphan objects and
    deflating of content streams. If the result is not smaller than the input, the original
    bytes are returned unchanged.
    """
    if not 1 <= image_quality <= 100:
        raise ValueError("image_quality must be between 1 and 100")
    if max_image_side is not None and max_image_side < 1:
        raise ValueError("max_image_side must be positive")
    reader = _reader(data, password)
    try:
        writer = PdfWriter(clone_from=reader)
        recompressed = downscaled = 0
        for page in writer.pages:
            if recompress_images:
                seen: set[int] = set()
                for img in page.images:
                    ref = img.indirect_reference
                    key = ref.idnum if ref is not None else id(img)
                    if key in seen:
                        continue
                    seen.add(key)
                    done, down = _optimise_image(img, image_quality, max_image_side)
                    recompressed += done
                    downscaled += down
            page.compress_content_streams()
        try:
            writer.compress_identical_objects(remove_duplicates=True, remove_unreferenced=True)
        except TypeError:  # pypdf < 6.x used the older keyword names
            writer.compress_identical_objects(remove_identicals=True, remove_orphans=True)
        out = _dump(writer)
    except CorruptFileError:
        raise
    except Exception as exc:
        raise CorruptFileError(f"Cannot compress PDF: {exc}") from exc
    if len(out) >= len(data):
        return CompressResult(data, len(data), len(data), 0, 0)
    return CompressResult(out, len(data), len(out), recompressed, downscaled)


def compress_pdf(
    data: bytes,
    *,
    image_quality: int = 70,
    max_image_side: int | None = 1600,
    recompress_images: bool = True,
    password: str = "",
) -> bytes:
    """Return an optimised copy of the PDF; see :func:`compress_pdf_with_stats`."""
    return compress_pdf_with_stats(
        data,
        image_quality=image_quality,
        max_image_side=max_image_side,
        recompress_images=recompress_images,
        password=password,
    ).data
