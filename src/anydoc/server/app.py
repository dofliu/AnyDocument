"""FastAPI application of the AnyDocument API service.

The skeleton offers a health check, document upload (size-limited), session storage
with a TTL, listing, download and deletion. Interactive API documentation lives under
``/api/docs`` because ``/docs`` is the document collection.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator, Awaitable, Callable, MutableMapping
from typing import Annotated, Any

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
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

__all__ = ["create_app"]

_Message = MutableMapping[str, Any]
_Receive = Callable[[], Awaitable[_Message]]

# Room for multipart boundaries and part headers on top of the file itself.
_MULTIPART_OVERHEAD = 64 * 1024

_ERROR_STATUS: tuple[tuple[type[AnyDocError], int, str], ...] = (
    (SessionNotFoundError, 404, "not_found"),
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
