"""Job transitions for ingestion workers.

Cluster-internal: the edge proxy must not route `/internal` from outside.
Authorization here is a service credential, and no route takes a principal,
because a worker acts on a job rather than on behalf of a person. Every
route is safe to call twice - that is the point of the protocol, not a
side effect of it.
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Depends, Request, status
from primer_contracts.errors import ErrorCode
from primer_contracts.ingestion import (
    ClaimResponse,
    StageClaim,
    StageCompletion,
    StageFailure,
    TransitionResult,
)
from primer_service.db import get_session
from primer_service.durable import DurableRoute
from primer_service.errors import ProblemError
from sqlalchemy.ext.asyncio import AsyncSession

from primer_control.config import Settings
from primer_control.dispatch import Dispatcher
from primer_control.repositories.documents import DocumentRepository
from primer_control.repositories.ingestion_jobs import IngestionJobRepository
from primer_control.security import require_service_credential
from primer_control.services.ingestion_pipeline import stage_waiting

router = APIRouter(
    prefix="/internal/v1/ingestion",
    tags=["internal"],
    dependencies=[Depends(require_service_credential)],
    include_in_schema=False,
    # Commit before the response is sent; see primer_*.durable.
    route_class=DurableRoute,
)

Session = Annotated[AsyncSession, Depends(get_session)]


def get_settings(request: Request) -> Settings:
    settings: Settings = request.app.state.settings
    return settings


Config = Annotated[Settings, Depends(get_settings)]


def unknown_job(job_id: UUID) -> ProblemError:
    return ProblemError(
        code=ErrorCode.NOT_FOUND,
        title="Job not found",
        status_code=status.HTTP_404_NOT_FOUND,
        detail=f"No ingestion job {job_id} exists.",
    )


@router.post("/jobs/{job_id}/claim", summary="Take a stage of a job")
async def claim_stage(
    job_id: UUID, payload: StageClaim, session: Session, settings: Config
) -> ClaimResponse:
    """Return work, or the reason there is none.

    A refusal is a 200 with an outcome, not an error status: at-least-once
    delivery makes duplicate messages routine, and a worker that treated
    them as failures would retry work that is already done.
    """
    try:
        return await IngestionJobRepository(session).claim(
            job_id, payload.stage, lease_seconds=settings.job_lease_seconds
        )
    except LookupError as error:
        raise unknown_job(job_id) from error


@router.get("/libraries/{library_id}/generations", summary="Which generations answer for a library")
async def active_generations(library_id: UUID, session: Session) -> list[UUID]:
    """Resolve the scope a search must be filtered to.

    Control owns which build is current, so retrieval never has to hold that
    state or ask for it per query. A deleted document drops out here the
    moment its tombstone is written, before its vectors are removed.
    """
    return await IngestionJobRepository(session).active_generations(library_id)


@router.post("/jobs/{job_id}/purge", summary="Remove a deleted document's rows")
async def purge_document(job_id: UUID, session: Session) -> list[str]:
    """Drop the metadata of a tombstoned document and report freed sources.

    Called by the delete stage once the passages are gone. Returns the
    content hashes no document references any more, which the worker then
    removes from the object store - in that order, so bytes are only removed
    once the database agrees nothing points at them.
    """
    repository = IngestionJobRepository(session)
    document_id = await repository.deleted_document_for(job_id)
    if document_id is None:
        raise unknown_job(job_id)
    return await DocumentRepository(session).purge(document_id)


@router.post("/jobs/{job_id}/heartbeat", summary="Extend a claim")
async def heartbeat(
    job_id: UUID, payload: StageCompletion, session: Session, settings: Config
) -> TransitionResult:
    try:
        return await IngestionJobRepository(session).heartbeat(
            job_id,
            payload.stage,
            payload.generation_id,
            lease_seconds=settings.job_lease_seconds,
        )
    except LookupError as error:
        raise unknown_job(job_id) from error


@router.post("/jobs/{job_id}/complete", summary="Advance a completed stage")
async def complete_stage(
    job_id: UUID,
    payload: StageCompletion,
    session: Session,
    dispatcher: Dispatcher,
    background: BackgroundTasks,
) -> TransitionResult:
    """Advance the job, and send the stage it now waits for.

    Control sends it rather than the worker that finished, because the
    completion and the record that a message is owed commit together here.
    A worker that published after completing left nothing behind when it
    died in between, or when the broker refused it: the job sat in
    `chunking` or `indexing` with no message and no sweep looking for one.
    """
    try:
        result = await IngestionJobRepository(session).complete(
            job_id, payload.stage, payload.generation_id
        )
    except LookupError as error:
        raise unknown_job(job_id) from error
    following = stage_waiting(result.status) if result.applied else None
    if following is not None:
        background.add_task(dispatcher.dispatch, following, job_id)
    return result


@router.post("/jobs/{job_id}/fail", summary="Record a failed stage")
async def fail_stage(
    job_id: UUID, payload: StageFailure, session: Session, settings: Config
) -> TransitionResult:
    try:
        return await IngestionJobRepository(session).fail(
            job_id, payload, max_attempts=settings.max_job_attempts
        )
    except LookupError as error:
        raise unknown_job(job_id) from error
