"""Document upload, status, download, and deletion.

Documents inherit their library's authorization: an unreachable library and
an unreachable document return the same 404, so probing document ids reveals
nothing about libraries the caller cannot see.
"""

from __future__ import annotations

from pathlib import PurePosixPath
from typing import Annotated
from urllib.parse import quote
from uuid import UUID

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    Request,
    Response,
    status,
)
from fastapi.responses import StreamingResponse
from primer_contracts.documents import DocumentSummary, IngestionStatus, ReindexSummary
from primer_contracts.errors import ErrorCode
from primer_contracts.ingestion import StageName
from primer_service.db import get_session
from primer_service.durable import DurableRoute
from primer_service.errors import ProblemError
from primer_storage import QuotaExceeded, SourceStore, SourceStoreError
from sqlalchemy.ext.asyncio import AsyncSession

from primer_control.identity import CurrentPrincipal
from primer_control.models import Document, Library
from primer_control.publisher import JobPublisher
from primer_control.repositories.documents import DocumentRecord, DocumentRepository
from primer_control.repositories.libraries import LibraryRepository
from primer_control.services.library_access import LibraryAccess
from primer_control.upload_stream import REQUEST_BODY, open_upload

router = APIRouter(
    prefix="/api/v1/libraries/{library_id}/documents", tags=["documents"], route_class=DurableRoute
)

Session = Annotated[AsyncSession, Depends(get_session)]
access = LibraryAccess()

MAX_FILENAME_LENGTH = 255


def get_source_store(request: Request) -> SourceStore:
    store: SourceStore = request.app.state.source_store
    return store


def get_publisher(request: Request) -> JobPublisher:
    publisher: JobPublisher = request.app.state.publisher
    return publisher


Store = Annotated[SourceStore, Depends(get_source_store)]
Publisher = Annotated[JobPublisher, Depends(get_publisher)]


def not_found() -> ProblemError:
    return ProblemError(
        code=ErrorCode.NOT_FOUND,
        title="Document not found",
        status_code=status.HTTP_404_NOT_FOUND,
        detail="No document with that identifier is available to you.",
    )


def as_problem(error: SourceStoreError) -> ProblemError:
    """Translate a rejected upload into its stable HTTP shape."""
    if isinstance(error, QuotaExceeded):
        return ProblemError(
            code=ErrorCode.QUOTA_EXCEEDED,
            title="Upload too large",
            status_code=status.HTTP_413_CONTENT_TOO_LARGE,
            detail=error.message,
        )
    return ProblemError(
        code=ErrorCode.UNSUPPORTED_CONTENT,
        title="Unsupported file",
        status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
        detail=error.message,
    )


def safe_filename(raw: str | None) -> str:
    """Reduce a client-supplied name to a bare, storable filename.

    The name is echoed back in listings and download headers, so directory
    components and control characters are stripped here rather than trusted
    downstream.
    """
    candidate = PurePosixPath((raw or "").replace("\\", "/")).name
    candidate = "".join(character for character in candidate if character.isprintable()).strip()
    if not candidate or candidate in {".", ".."}:
        raise ProblemError(
            code=ErrorCode.VALIDATION_FAILED,
            title="Filename missing",
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="The upload must carry a filename.",
        )
    return candidate[:MAX_FILENAME_LENGTH]


def summarize(record: DocumentRecord) -> DocumentSummary:
    job = record.job
    return DocumentSummary(
        id=record.document.id,
        library_id=record.document.library_id,
        current_version_id=record.version.id,
        filename=record.version.filename,
        media_type=record.version.media_type,
        byte_size=record.version.byte_size,
        status=IngestionStatus(job.state) if job else IngestionStatus.QUEUED,
        status_detail=job.error_detail if job else None,
        created_at=record.document.created_at,
        updated_at=record.document.updated_at,
    )


def enqueue(background: BackgroundTasks, publisher: JobPublisher, record: DocumentRecord) -> None:
    """Queue the new version's ingestion, after the upload has committed.

    A background task runs once the response is on its way, which is after
    the session dependency committed. Publishing inline would risk a message
    outliving a rolled-back upload, and a worker claiming a job that never
    existed.
    """
    if record.job is None:  # pragma: no cover - every version is created with a job
        return
    background.add_task(publisher.publish, StageName.PARSE, record.job.id)


async def require_library(
    library_id: UUID,
    principal_id: UUID,
    session: AsyncSession,
    *,
    to_manage: bool,
) -> Library:
    """The library gate, at the same strength as the thing behind it.

    This asked for `manageable` whichever endpoint called it, so a library
    shared with someone refused them its documents: reading the list, a
    single status, or the bytes all failed at the gate before the
    per-document `readable` check below them could allow anything. A shared
    library therefore opened to "no document with that identifier is
    available to you" - the reader could see the library named in their
    sidebar and nothing inside it.

    Which check to apply is the caller's to say, because it is the caller
    that knows whether it is about to read or to change. It is passed by
    keyword so neither can be chosen by accident.
    """
    policy = access.manageable(principal_id) if to_manage else access.readable(principal_id)
    library = await LibraryRepository(session).get(library_id, where=policy)
    if library is None:
        raise not_found()
    return library


async def store_version(
    *,
    document: Document | None,
    library_id: UUID,
    request: Request,
    store: SourceStore,
    session: AsyncSession,
) -> DocumentRecord:
    """Persist an upload as a new document, or as a replacement version.

    The bytes are made durable before any metadata is written, so a failed
    upload leaves no document row promising content that was never stored.
    """
    upload = open_upload(request, chunk_bytes=store.chunk_bytes)
    filename = safe_filename(await upload.start())
    try:
        stored = await store.put(upload.chunks(), filename=filename)
    except SourceStoreError as error:
        raise as_problem(error) from error

    repository = DocumentRepository(session)
    await repository.ensure_source(stored)
    if document is None:
        document = await repository.create_document(library_id=library_id)
    version = await repository.add_version(document, stored, filename=filename)
    job = await repository.enqueue_job(version)
    return DocumentRecord(document, version, job)


@router.get("", summary="List a library's documents")
async def list_documents(
    library_id: UUID, principal: CurrentPrincipal, session: Session
) -> list[DocumentSummary]:
    await require_library(library_id, principal.user_id, session, to_manage=False)
    records = await DocumentRepository(session).find_all(
        library_id=library_id, where=access.readable(principal.user_id)
    )
    return [summarize(record) for record in records]


@router.post(
    "",
    status_code=status.HTTP_201_CREATED,
    summary="Upload a document",
    openapi_extra=REQUEST_BODY,
)
async def upload_document(
    library_id: UUID,
    principal: CurrentPrincipal,
    session: Session,
    store: Store,
    publisher: Publisher,
    background: BackgroundTasks,
    request: Request,
) -> DocumentSummary:
    await require_library(library_id, principal.user_id, session, to_manage=True)
    record = await store_version(
        document=None, library_id=library_id, request=request, store=store, session=session
    )
    enqueue(background, publisher, record)
    return summarize(record)


@router.post(
    "/{document_id}/versions",
    status_code=status.HTTP_201_CREATED,
    summary="Replace a document with a new version",
    openapi_extra=REQUEST_BODY,
)
async def replace_document(
    library_id: UUID,
    document_id: UUID,
    principal: CurrentPrincipal,
    session: Session,
    store: Store,
    publisher: Publisher,
    background: BackgroundTasks,
    request: Request,
) -> DocumentSummary:
    """Add a version rather than overwriting one.

    Earlier versions stay readable so a citation pinned to a version keeps
    resolving to the text that was actually quoted.
    """
    await require_library(library_id, principal.user_id, session, to_manage=True)
    repository = DocumentRepository(session)
    existing = await repository.get(
        document_id, library_id=library_id, where=access.manageable(principal.user_id)
    )
    if existing is None:
        raise not_found()
    record = await store_version(
        document=existing.document,
        library_id=library_id,
        request=request,
        store=store,
        session=session,
    )
    enqueue(background, publisher, record)
    return summarize(record)


@router.get("/{document_id}", summary="Read one document's status")
async def read_document(
    library_id: UUID, document_id: UUID, principal: CurrentPrincipal, session: Session
) -> DocumentSummary:
    await require_library(library_id, principal.user_id, session, to_manage=False)
    record = await DocumentRepository(session).get(
        document_id, library_id=library_id, where=access.readable(principal.user_id)
    )
    if record is None:
        raise not_found()
    return summarize(record)


@router.get("/{document_id}/content", summary="Download the current version's bytes")
async def download_document(
    library_id: UUID,
    document_id: UUID,
    principal: CurrentPrincipal,
    session: Session,
    store: Store,
) -> StreamingResponse:
    await require_library(library_id, principal.user_id, session, to_manage=False)
    record = await DocumentRepository(session).get(
        document_id, library_id=library_id, where=access.readable(principal.user_id)
    )
    if record is None:
        raise not_found()

    # RFC 5987 encoding, and never the storage key: how bytes are addressed
    # on the backend is not the caller's business.
    disposition = f"attachment; filename*=UTF-8''{quote(record.version.filename)}"
    return StreamingResponse(
        store.open_stream(record.version.source_sha256),
        media_type=record.version.media_type,
        headers={"Content-Disposition": disposition},
    )


@router.post(
    "/reindex",
    status_code=status.HTTP_202_ACCEPTED,
    summary="Rebuild every document in a library",
)
async def reindex_library(
    library_id: UUID,
    principal: CurrentPrincipal,
    session: Session,
    publisher: Publisher,
    background: BackgroundTasks,
) -> ReindexSummary:
    """Rebuild the whole library, one job per document.

    This exists because the reason to reindex is almost never one document.
    Chunking, the embedding model or the tokenizer changes and everything
    indexed under the old settings is stale together; doing that a document
    at a time means clicking once per row and having no idea which rows
    were missed.

    A literal path segment beside `/{document_id}`, which is unambiguous
    only because nothing else answers POST on a single segment here. A
    future `POST /{document_id}` would have to be declared after this one
    or it would swallow it.

    Per-document refusals are not errors. `start_reindex` declines a
    document already being rebuilt, and on a library-wide press that is the
    ordinary case rather than a fault, so it is counted and reported rather
    than raised.
    """
    await require_library(library_id, principal.user_id, session, to_manage=True)
    repository = DocumentRepository(session)
    records = await repository.find_all(
        library_id=library_id, where=access.manageable(principal.user_id)
    )

    queued = 0
    for record in records:
        job = await repository.start_reindex(record)
        if job is None:
            continue
        queued += 1
        # After the response, like every other publish here: the session
        # commits with the response, and a message sent before that could
        # hand a worker a generation the database rolled back.
        background.add_task(publisher.publish, StageName.PARSE, job.id)
    return ReindexSummary(queued=queued, skipped=len(records) - queued)


@router.post(
    "/{document_id}/reindex",
    status_code=status.HTTP_202_ACCEPTED,
    summary="Rebuild a document's index",
)
async def reindex_document(
    library_id: UUID,
    document_id: UUID,
    principal: CurrentPrincipal,
    session: Session,
    publisher: Publisher,
    background: BackgroundTasks,
) -> DocumentSummary:
    """Build a new generation, leaving the current one answering.

    Pressing this twice does not start two builds. A second build of the
    same version would have two workers writing different generations with
    only one ever activated, so a rebuild already in flight is reported as
    it stands rather than restarted.
    """
    await require_library(library_id, principal.user_id, session, to_manage=True)
    repository = DocumentRepository(session)
    record = await repository.get(
        document_id, library_id=library_id, where=access.manageable(principal.user_id)
    )
    if record is None:
        raise not_found()

    job = await repository.start_reindex(record)
    if job is not None:
        background.add_task(publisher.publish, StageName.PARSE, job.id)
        record = DocumentRecord(record.document, record.version, job)
    return summarize(record)


@router.delete(
    "/{document_id}", status_code=status.HTTP_204_NO_CONTENT, summary="Delete a document"
)
async def delete_document(
    library_id: UUID,
    document_id: UUID,
    principal: CurrentPrincipal,
    session: Session,
    publisher: Publisher,
    background: BackgroundTasks,
) -> Response:
    """Tombstone first; the passages and bytes go afterwards.

    The tombstone is the deletion the user asked for, and it takes effect
    immediately. Everything after it is cleanup, and scheduling it separately
    means a slow or failing cleanup never leaves a deleted document
    answering questions in the meantime.
    """
    await require_library(library_id, principal.user_id, session, to_manage=True)
    repository = DocumentRepository(session)
    record = await repository.get(
        document_id, library_id=library_id, where=access.manageable(principal.user_id)
    )
    if record is None:
        raise not_found()

    await repository.soft_delete(record.document)
    job = await repository.start_deletion(record)
    if job is not None:
        background.add_task(publisher.publish, StageName.DELETE, job.id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
