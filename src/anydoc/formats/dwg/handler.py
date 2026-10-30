"""DWG documents: header metadata always, full drawing access when a converter exists."""

from __future__ import annotations

from pathlib import Path
from typing import Any, ClassVar

from anydoc.core.errors import CorruptFileError
from anydoc.core.model import Capability, Metadata, Part
from anydoc.core.registry import FormatHandler
from anydoc.external.dwg import available_dwg_converter, convert_dwg_to_dxf
from anydoc.external.tools import ExternalToolError, temp_workdir
from anydoc.formats.dxf import DxfDocument, DxfHandler

_DWG_MIME = "image/vnd.dwg"

_RELEASES = {
    "AC1.40": "R1.4",
    "AC1.50": "R2.0",
    "AC1002": "R2.5",
    "AC1003": "R2.6",
    "AC1004": "R9",
    "AC1006": "R10",
    "AC1009": "R11/R12",
    "AC1012": "R13",
    "AC1014": "R14",
    "AC1015": "R2000",
    "AC1018": "R2004",
    "AC1021": "R2007",
    "AC1024": "R2010",
    "AC1027": "R2013",
    "AC1032": "R2018",
}


def dwg_version(head: bytes) -> str | None:
    """Return the version code (``AC1027``...) from a DWG header, or ``None`` if invalid."""
    code = head[:6].decode("ascii", errors="replace")
    return code if code in _RELEASES else None


def dwg_release(version: str) -> str:
    """Return the AutoCAD release name for a DWG version code (code itself if unknown)."""
    return _RELEASES.get(version, version)


class DwgDocument:
    """An opened DWG file.

    Header metadata (``version``, ``release``) is always available. When a converter
    (ODA File Converter or LibreDWG) is installed the drawing is converted to DXF and
    the structure/text/render API of :class:`~anydoc.formats.dxf.DxfDocument` is exposed
    through :attr:`dxf`; otherwise ``dxf`` is ``None`` and the document degrades to
    metadata only (``parts()`` empty, ``text()`` empty).
    """

    format = "dwg"

    def __init__(
        self,
        version: str,
        *,
        path: Path | None = None,
        raw: bytes = b"",
        dxf: DxfDocument | None = None,
        converter: str | None = None,
    ) -> None:
        self.version = version
        self.release = dwg_release(version)
        self.path = path
        self._raw = raw
        self.dxf = dxf
        self.converter = converter
        self.metadata = self._build_metadata()

    @property
    def converted(self) -> bool:
        """Whether the full drawing was converted to DXF."""
        return self.dxf is not None

    def _build_metadata(self) -> Metadata:
        extra: dict[str, Any] = {
            "version": self.version,
            "release": self.release,
            "converted": self.converted,
            "converter": self.converter,
        }
        title = None
        page_count = None
        if self.dxf is not None:
            extra.update(
                {
                    k: v
                    for k, v in self.dxf.metadata.extra.items()
                    if k not in ("version", "release")
                }
            )
            title = self.dxf.metadata.title
            page_count = self.dxf.metadata.page_count
        return Metadata(
            format="dwg", mime=_DWG_MIME, title=title, page_count=page_count, extra=extra
        )

    def parts(self) -> list[Part]:
        """Return one part per layout (empty without a converter)."""
        return self.dxf.parts() if self.dxf else []

    def text(self) -> str:
        """Return drawing text (empty without a converter)."""
        return self.dxf.text() if self.dxf else ""

    def save(self, path: Path | None = None) -> Path:
        """Write the original DWG bytes to ``path`` (default: the source path)."""
        target = path or self.path
        if target is None:
            raise ValueError("No path given and document has no source path")
        target.write_bytes(self._raw)
        return target

    def close(self) -> None:
        """Release resources."""
        if self.dxf is not None:
            self.dxf.close()


class DwgHandler(FormatHandler):
    """Handler for AutoCAD ``.dwg`` drawings (read-only, via DXF conversion)."""

    format_id: ClassVar[str] = "dwg"
    extensions: ClassVar[tuple[str, ...]] = ("dwg",)
    mime_types: ClassVar[tuple[str, ...]] = (_DWG_MIME, "application/acad", "application/x-dwg")
    capabilities: ClassVar[frozenset[Capability]] = frozenset(
        {Capability.READ, Capability.EXTRACT_TEXT, Capability.RENDER, Capability.CONVERT}
    )

    @classmethod
    def sniff(cls, head: bytes, path: Path | None) -> float:
        """Return confidence from the ``ACxxxx`` magic or the extension."""
        if dwg_version(head) is not None:
            return 1.0
        if path is not None and path.suffix.lower() == ".dwg":
            return 0.5
        return 0.0

    def open(self, source: Path | bytes) -> DwgDocument:
        """Open a DWG; converts to DXF when a converter exists, else metadata only.

        Raises:
            CorruptFileError: if the header is not a DWG header or conversion output is invalid.
        """
        path = source if isinstance(source, Path) else None
        if path is not None:
            try:
                raw = path.read_bytes()
            except OSError as exc:
                raise CorruptFileError(f"Cannot read {path}: {exc}") from exc
        else:
            raw = source  # type: ignore[assignment]
        version = dwg_version(raw)
        if version is None:
            raise CorruptFileError("Not a DWG file: missing AC10xx header")
        converter = available_dwg_converter()
        if converter is None:
            return DwgDocument(version, path=path, raw=raw)
        with temp_workdir("anydoc-dwg-") as work:
            src = work / "input.dwg"
            src.write_bytes(raw)
            outdir = work / "out"
            outdir.mkdir()
            try:
                dxf_path = convert_dwg_to_dxf(src, outdir)
            except ExternalToolError as exc:
                raise CorruptFileError(f"Cannot convert DWG: {exc}") from exc
            dxf = DxfHandler().open(dxf_path.read_bytes())
        return DwgDocument(version, path=path, raw=raw, dxf=dxf, converter=converter)
