"""FastAPI application of the AnyDocument API service.

The skeleton offers a health check, document upload (size-limited), session storage
with a TTL, listing, download and deletion, plus the viewing endpoints (document
information, parts, text and rendered images). Interactive API documentation lives under
``/api/docs`` because ``/docs`` is the document collection.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator, Awaitable, Callable, MutableMapping
from typing import Annotated, Any, Literal

from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool
from starlette.datastructures import UploadFile
from starlette.exceptions import HTTPException as StarletteHTTPException

from anydoc import __version__
from anydoc.core.errors import (
    AnyDocError,
    CorruptFileError,
    ExternalToolMissingError,
    OperationNotSupportedError,
    SecurityLimitError,
    UnsupportedFormatError,
)
from anydoc.core.registry import registry
from anydoc.server.settings import ServerSettings
from anydoc.server.store import DocumentRecord, SessionNotFoundError, SessionStore
from anydoc.server.views import (
    PartNotFoundError,
    document_info,
    document_parts,
    document_text,
    render_etag,
    render_info,
    render_part,
)

__all__ = ["create_app"]

_Message = MutableMapping[str, Any]
_Receive = Callable[[], Awaitable[_Message]]

# Room for multipart boundaries and part headers on top of the file itself.
_MULTIPART_OVERHEAD = 64 * 1024

_ERROR_STATUS: tuple[tuple[type[AnyDocError], int, str], ...] = (
    (SessionNotFoundError, 404, "not_found"),
    (PartNotFoundError, 404, "part_not_found"),
    (UnsupportedFormatError, 415, "unsupported_format"),
    (CorruptFileError, 422, "corrupt_file"),
    (ExternalToolMissingError, 501, "external_tool_missing"),
    (SecurityLimitError, 413, "too_large"),
    (OperationNotSupportedError, 400, "operation_not_supported"),
)
_HTTP_CODES = {
    400: "bad_request",
    404: "not_found",
    405: "method_not_allowed",
    413: "too_large",
    415: "unsupported_format",
    422: "validation_error",
}


class HealthResponse(BaseModel):
    """Liveness information of the service."""

    status: str = Field(examples=["ok"])
    version: str
    documents: int = Field(description="Number of live document sessions.")
    max_upload_bytes: int
    session_ttl_seconds: float


class FormatInfo(BaseModel):
    """A supported document format."""

    format: str
    extensions: list[str]
    mime_types: list[str]
    capabilities: list[str]


class DocumentInfo(BaseModel):
    """An uploaded document session."""

    id: str
    filename: str
    format: str
    mime: str
    size: int = Field(description="Size in bytes.")
    created: str = Field(description="Upload time (ISO 8601, UTC).")
    accessed: str = Field(description="Last access time (ISO 8601, UTC).")
    expires: str = Field(description="Expiry time; renewed on each access.")
    download_url: str


class MetadataInfo(BaseModel):
    """Format-independent metadata read from the document itself."""

    format: str
    mime: str
    title: str | None = None
    author: str | None = None
    created: str | None = Field(default=None, description="ISO 8601 creation time.")
    modified: str | None = Field(default=None, description="ISO 8601 modification time.")
    page_count: int | None = None
    extra: dict[str, Any] = Field(
        default_factory=dict, description="Format-specific details (sheets, layers, ...)."
    )


class RenderOptions(BaseModel):
    """How a document can be rendered through ``/docs/{id}/render/{part}``."""

    unit: str = Field(description="What a render index addresses: page, layout, frame, ...")
    formats: list[str] = Field(description="Available image formats.")


class DocumentDetails(BaseModel):
    """Session data of a document together with what its handler reads from it."""

    id: str
    filename: str
    format: str
    mime: str
    size: int = Field(description="Size in bytes.")
    metadata: MetadataInfo
    part_count: int = Field(description="Number of structural parts (see ``/parts``).")
    capabilities: list[str]
    render: RenderOptions
    parts_url: str
    text_url: str
    render_url: str


class PartInfo(BaseModel):
    """A page, sheet, slide, layout, frame, ... of a document."""

    kind: str
    index: int
    name: str | None = None
    size: list[float] | None = Field(
        default=None, description="Width and height in the part's native unit."
    )


class PartList(BaseModel):
    """The structural parts of a document."""

    id: str
    format: str
    count: int
    parts: list[PartInfo]


class TextResponse(BaseModel):
    """A window of the document text (offsets and lengths count characters)."""

    id: str
    format: str
    text: str
    offset: int
    length: int
    total: int = Field(description="Length of the whole text.")
    truncated: bool = Field(description="Whether text remains after this window.")


class RenderList(BaseModel):
    """The renderable units of a document."""

    id: str
    format: str
    unit: str
    count: int
    formats: list[str]
    urls: list[str] = Field(description="Render URL of every unit, in order.")


class ErrorDetail(BaseModel):
    """Machine-readable error code and human-readable message."""

    code: str
    message: str


class ErrorResponse(BaseModel):
    """Envelope of every error response."""

    error: ErrorDetail


class _BodyTooLargeError(Exception):
    """The request body grew beyond the allowed size while being received."""


def _limited_receive(receive: _Receive, limit: int) -> _Receive:
    """Wrap an ASGI ``receive`` so that reading more than ``limit`` body bytes fails."""
    received = 0

    async def wrapper() -> _Message:
        nonlocal received
        message = await receive()
        if message["type"] == "http.request":
            received += len(message.get("body", b""))
            if received > limit:
                raise _BodyTooLargeError
        return message

    return wrapper


def _get_record(request: Request, doc_id: str) -> DocumentRecord:
    store: SessionStore = request.app.state.store
    return store.get(doc_id)


_RecordDep = Annotated[DocumentRecord, Depends(_get_record)]


def _error(status_code: int, code: str, message: str) -> JSONResponse:
    return JSONResponse({"error": {"code": code, "message": message}}, status_code=status_code)


def _document_info(record: DocumentRecord) -> DocumentInfo:
    return DocumentInfo(**record.to_dict(), download_url=f"/docs/{record.id}/download")


def _errors(*codes: int) -> dict[int | str, dict[str, Any]]:
    return {code: {"model": ErrorResponse} for code in codes}


_UPLOAD_SCHEMA: dict[str, Any] = {
    "requestBody": {
        "required": True,
        "content": {
            "multipart/form-data": {
                "schema": {
                    "type": "object",
                    "required": ["file"],
                    "properties": {
                        "file": {
                            "type": "string",
                            "format": "binary",
                            "description": "The document to upload.",
                        }
                    },
                }
            }
        },
    }
}


def create_app(
    settings: ServerSettings | None = None, *, store: SessionStore | None = None
) -> FastAPI:
    """Create the AnyDocument API application.

    Args:
        settings: Server settings; defaults to :meth:`ServerSettings.from_env`.
        store: Session store to use instead of one built from ``settings`` (the
            caller keeps ownership: it is not closed when the application stops).

    The store is available as ``app.state.store`` and the settings as
    ``app.state.settings``.
    """
    cfg = settings or ServerSettings.from_env()
    owns_store = store is None
    sessions = (
        SessionStore(cfg.storage_dir, ttl=cfg.session_ttl, max_bytes=cfg.max_upload_bytes)
        if store is None
        else store
    )
    max_bytes = min(cfg.max_upload_bytes, sessions.max_bytes)

    async def _sweep() -> None:
        while True:
            await asyncio.sleep(cfg.cleanup_interval)
            await run_in_threadpool(sessions.purge_expired)

    @contextlib.asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        registry.load_entry_points()
        await run_in_threadpool(sessions.purge_expired)
        task = asyncio.create_task(_sweep()) if cfg.cleanup_interval > 0 else None
        try:
            yield
        finally:
            if task is not None:
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task
            if owns_store:
                sessions.close()

    app = FastAPI(
        title="AnyDocument API",
        version=__version__,
        description="Universal document viewer, converter and editor service.",
        docs_url="/api/docs",
        redoc_url="/api/redoc",
        openapi_url="/openapi.json",
        lifespan=lifespan,
    )
    app.state.settings = cfg
    app.state.store = sessions

    @app.exception_handler(AnyDocError)
    async def _anydoc_error(_: Request, exc: AnyDocError) -> JSONResponse:
        for kind, status_code, code in _ERROR_STATUS:
            if isinstance(exc, kind):
                return _error(status_code, code, str(exc))
        return _error(500, "internal_error", str(exc))

    @app.exception_handler(StarletteHTTPException)
    async def _http_error(_: Request, exc: StarletteHTTPException) -> JSONResponse:
        code = _HTTP_CODES.get(exc.status_code, "http_error")
        response = _error(exc.status_code, code, str(exc.detail))
        if exc.headers:
            response.headers.update(exc.headers)
        return response

    @app.exception_handler(RequestValidationError)
    async def _validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
        problems = "; ".join(
            f"{'.'.join(str(p) for p in err.get('loc', ()))}: {err.get('msg', '')}"
            for err in exc.errors()
        )
        return _error(422, "validation_error", problems)

    @app.get("/health", response_model=HealthResponse, tags=["service"])
    def health() -> HealthResponse:
        """Report that the service is alive, with its version and limits."""
        return HealthResponse(
            status="ok",
            version=__version__,
            documents=len(sessions),
            max_upload_bytes=max_bytes,
            session_ttl_seconds=sessions.ttl,
        )

    @app.get("/formats", response_model=list[FormatInfo], tags=["service"])
    def formats() -> list[FormatInfo]:
        """List the supported formats and their capabilities."""
        return [
            FormatInfo(
                format=h.format_id,
                extensions=list(h.extensions),
                mime_types=list(h.mime_types),
                capabilities=sorted(str(c) for c in h.capabilities),
            )
            for h in registry.all()
        ]

    @app.post(
        "/docs",
        response_model=DocumentInfo,
        status_code=201,
        tags=["documents"],
        responses=_errors(400, 413, 415),
        openapi_extra=_UPLOAD_SCHEMA,
    )
    async def upload(request: Request, response: Response) -> DocumentInfo:
        """Upload a document (multipart field ``file``) and open a session for it.

        The format is detected from the content. Uploads larger than the configured
        limit are rejected with 413, empty or unrecognised files with 415.
        """
        too_large = HTTPException(
            413,
            f"Upload exceeds the size limit of {max_bytes} bytes",
        )
        body_limit = max_bytes + _MULTIPART_OVERHEAD
        declared = request.headers.get("content-length", "")
        if declared.isdigit() and int(declared) > body_limit:
            raise too_large
        if not request.headers.get("content-type", "").lower().startswith("multipart/form-data"):
            raise HTTPException(400, "Expected a multipart/form-data body with a 'file'")
        limited = Request(request.scope, _limited_receive(request.receive, body_limit))
        try:
            form = await limited.form(max_files=1, max_fields=16)
        except _BodyTooLargeError:
            raise too_large from None
        try:
            upload_file = form.get("file")
            if not isinstance(upload_file, UploadFile):
                raise HTTPException(400, "Missing multipart field 'file'")
            record = await run_in_threadpool(
                sessions.create, upload_file.filename, upload_file.file
            )
        finally:
            await form.close()
        response.headers["Location"] = f"/docs/{record.id}"
        return _document_info(record)

    @app.get("/docs", response_model=list[DocumentInfo], tags=["documents"])
    def list_documents() -> list[DocumentInfo]:
        """List the live document sessions, oldest first (does not renew them)."""
        return [_document_info(record) for record in sessions.all()]

    @app.get(
        "/docs/{doc_id}", response_model=DocumentInfo, tags=["documents"], responses=_errors(404)
    )
    def get_document(record: _RecordDep) -> DocumentInfo:
        """Return a document session and renew its expiry."""
        return _document_info(record)

    @app.get(
        "/docs/{doc_id}/download",
        response_class=FileResponse,
        tags=["documents"],
        responses={
            200: {"content": {"application/octet-stream": {}}, "description": "The document."},
            **_errors(404),
        },
    )
    def download(record: _RecordDep) -> FileResponse:
        """Download the document under its original file name."""
        return FileResponse(
            record.path,
            media_type=record.mime or "application/octet-stream",
            filename=record.filename,
        )

    @app.get(
        "/docs/{doc_id}/info",
        response_model=DocumentDetails,
        tags=["viewing"],
        responses=_errors(404, 415, 422),
    )
    def info(record: _RecordDep) -> DocumentDetails:
        """Return the document's metadata, part count, capabilities and render options."""
        base = f"/docs/{record.id}"
        return DocumentDetails(
            id=record.id,
            filename=record.filename,
            format=record.format,
            mime=record.mime,
            size=record.size,
            parts_url=f"{base}/parts",
            text_url=f"{base}/text",
            render_url=f"{base}/render",
            **document_info(record.path, record.format),
        )

    @app.get(
        "/docs/{doc_id}/parts",
        response_model=PartList,
        tags=["viewing"],
        responses=_errors(404, 415, 422),
    )
    def parts(record: _RecordDep) -> PartList:
        """List the structural parts: pages, sheets, slides, layouts, frames, ..."""
        items = [PartInfo(**part) for part in document_parts(record.path)]
        return PartList(id=record.id, format=record.format, count=len(items), parts=items)

    @app.get(
        "/docs/{doc_id}/text",
        response_model=TextResponse,
        tags=["viewing"],
        responses={
            200: {"content": {"text/plain": {}}, "description": "The text (JSON or plain)."},
            **_errors(404, 415, 422),
        },
    )
    def text(
        record: _RecordDep,
        offset: Annotated[int, Query(ge=0, description="First character to return.")] = 0,
        limit: Annotated[
            int | None, Query(ge=1, description="Largest number of characters to return.")
        ] = None,
        output: Annotated[
            Literal["json", "plain"],
            Query(alias="format", description="`json` (default) or `plain` (text/plain)."),
        ] = "json",
    ) -> Response:
        """Return the document text in reading order, optionally a character window."""
        window = document_text(record.path, offset=offset, limit=limit)
        if output == "plain":
            return PlainTextResponse(
                window.text,
                headers={
                    "X-Text-Offset": str(window.offset),
                    "X-Text-Total": str(window.total),
                    "X-Text-Truncated": str(window.truncated).lower(),
                },
            )
        payload = TextResponse(
            id=record.id,
            format=record.format,
            text=window.text,
            offset=window.offset,
            length=window.length,
            total=window.total,
            truncated=window.truncated,
        )
        return JSONResponse(payload.model_dump())

    @app.get(
        "/docs/{doc_id}/render",
        response_model=RenderList,
        tags=["viewing"],
        responses=_errors(404, 415, 422, 501),
    )
    def render_index(record: _RecordDep) -> RenderList:
        """List what can be rendered: unit kind, count, formats and one URL per unit.

        Office/ODF/RTF documents are converted to PDF on first use (needs LibreOffice;
        the rendition is cached for the session), so the first call may take a while.
        """
        details = render_info(record.path, record.format)
        return RenderList(
            id=record.id,
            format=record.format,
            unit=details.unit,
            count=details.count,
            formats=list(details.formats),
            urls=[f"/docs/{record.id}/render/{n}" for n in range(details.count)],
        )

    @app.get(
        "/docs/{doc_id}/render/{part}",
        response_class=Response,
        tags=["viewing"],
        responses={
            200: {
                "content": {
                    "image/png": {},
                    "image/jpeg": {},
                    "image/webp": {},
                    "image/svg+xml": {},
                },
                "description": "The rendered image.",
            },
            304: {"description": "The cached image is still current (`If-None-Match`)."},
            **_errors(400, 404, 415, 422, 501),
        },
    )
    def render(
        request: Request,
        record: _RecordDep,
        part: int,
        output: Annotated[
            Literal["png", "jpeg", "jpg", "webp", "svg"],
            Query(alias="format", description="Image format; `svg` only for SVG/DXF/DWG."),
        ] = "png",
        size: Annotated[
            int, Query(ge=16, le=4096, description="Longest side of a raster image (px).")
        ] = 1024,
    ) -> Response:
        """Render one page, slide, layout or frame (0-based) as PNG/JPEG/WebP or SVG."""
        etag = render_etag(record.path, part, output, size)
        headers = {
            "ETag": etag,
            "Cache-Control": "private, no-cache",
            "X-Content-Type-Options": "nosniff",
            # Uploaded SVG is untrusted: never let it run scripts when opened directly.
            "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; sandbox",
        }
        candidates = {
            tag.strip().removeprefix("W/")
            for tag in request.headers.get("if-none-match", "").split(",")
        }
        if etag in candidates:
            return Response(status_code=304, headers=headers)
        image = render_part(record.path, record.format, part, fmt=output, size=size)
        return Response(image.data, media_type=image.media_type, headers=headers)

    @app.delete(
        "/docs/{doc_id}",
        status_code=204,
        tags=["documents"],
        responses=_errors(404),
    )
    def delete_document(doc_id: str) -> Response:
        """Close a document session and remove its file."""
        sessions.delete(doc_id)
        return Response(status_code=204)

    return app
