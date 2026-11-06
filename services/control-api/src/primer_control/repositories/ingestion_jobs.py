"""Job transitions, written as compare-and-set statements.

Every transition here is a single conditional UPDATE. Read-then-write would
let two workers both observe a claimable job and both proceed; the condition
travels with the write instead, so exactly one of them changes a row and the
other is told what actually happened.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID

from primer_contracts.documents import IngestionStatus
from primer_contracts.ingestion import (
    ClaimOutcome,
    ClaimResponse,
    FailureDisposition,
    JobClaim,
    StageFailure,
    StageName,
    TransitionResult,
)
from sqlalchemy import Row, case, func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from primer_control.models import (
    Document,
    DocumentIndex,
    DocumentVersion,
    IngestionJob,
    Library,
)
from primer_control.services.ingestion_pipeline import (
    ABANDONED_STATES,
    TERMINAL_STATES,
    WAITING_FOR,
    stage_for,
    stage_waiting,
)

#: The error code a job carries while its message could not be sent. Only
#: this code is ever cleared by a successful send: a worker's own failure
#: on a job waiting to be retried is not the dispatcher's to erase.
UNDISPATCHED_CODE = "workers_unreachable"


class IngestionJobRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def _job(self, job_id: UUID) -> IngestionJob | None:
        result = await self._session.execute(select(IngestionJob).where(IngestionJob.id == job_id))
        return result.scalar_one_or_none()

    async def _context(self, job: IngestionJob) -> Row[DocumentVersion, Document, Library]:
        """The version, document, and library a claim needs to describe itself."""
        result = await self._session.execute(
            select(DocumentVersion, Document, Library)
            .join(Document, DocumentVersion.document_id == Document.id)
            .join(Library, Document.library_id == Library.id)
            .where(DocumentVersion.id == job.document_version_id)
        )
        return result.one()

    async def claim(self, job_id: UUID, stage: StageName, *, lease_seconds: int) -> ClaimResponse:
        """Take a stage, or explain why this message has no work to do.

        The lease is what excludes a second live worker. State alone cannot:
        a worker that crashed mid-stage leaves the job marked active, and a
        rule that refused to re-enter an active stage would strand it.
        """
        definition = stage_for(stage)
        now = datetime.now(UTC)
        claimed = await self._session.execute(
            update(IngestionJob)
            .where(
                IngestionJob.id == job_id,
                IngestionJob.state.in_([s.value for s in definition.claimable_from]),
                (IngestionJob.lease_expires_at.is_(None))
                | (IngestionJob.lease_expires_at < func.now()),
            )
            .values(
                state=definition.active.value,
                claimed_at=now,
                lease_expires_at=now + timedelta(seconds=lease_seconds),
                attempt=IngestionJob.attempt + 1,
            )
            .returning(IngestionJob.id)
        )
        if claimed.scalar_one_or_none() is None:
            return ClaimResponse(outcome=await self._refusal(job_id, stage))

        job = await self._job(job_id)
        assert job is not None  # noqa: S101 - the update above proved the row exists
        version, document, library = await self._context(job)
        return ClaimResponse(
            outcome=ClaimOutcome.CLAIMED,
            claim=JobClaim(
                job_id=job.id,
                stage=stage,
                generation_id=job.generation_id,
                attempt=job.attempt,
                document_id=document.id,
                document_version_id=version.id,
                library_id=library.id,
                owner_user_id=library.owner_user_id,
                source_sha256=version.source_sha256,
                filename=version.filename,
                media_type=version.media_type,
                byte_size=version.byte_size,
            ),
        )

    async def _refusal(self, job_id: UUID, stage: StageName) -> ClaimOutcome:
        """Why the conditional claim matched no row.

        A refusal is the normal result of at-least-once delivery, so it is
        reported as an outcome rather than raised. Only a job that does not
        exist at all is an error, and that is the caller's to raise.
        """
        job = await self._job(job_id)
        if job is None:
            raise LookupError(job_id)

        state = IngestionStatus(job.state)
        if state in ABANDONED_STATES:
            return ClaimOutcome.CANCELLED
        if state in TERMINAL_STATES:
            return ClaimOutcome.ALREADY_COMPLETED
        if state in stage_for(stage).claimable_from:
            # Right state, live lease: someone else is working on it.
            return ClaimOutcome.IN_PROGRESS
        return ClaimOutcome.ALREADY_COMPLETED

    async def heartbeat(
        self, job_id: UUID, stage: StageName, generation_id: UUID, *, lease_seconds: int
    ) -> TransitionResult:
        """Extend a live lease, so long work is not mistaken for a dead worker."""
        definition = stage_for(stage)
        result = await self._session.execute(
            update(IngestionJob)
            .where(
                IngestionJob.id == job_id,
                IngestionJob.state == definition.active.value,
                IngestionJob.generation_id == generation_id,
            )
            .values(lease_expires_at=datetime.now(UTC) + timedelta(seconds=lease_seconds))
            .returning(IngestionJob.state)
        )
        return await self._result(job_id, result.scalar_one_or_none())

    async def complete(
        self, job_id: UUID, stage: StageName, generation_id: UUID
    ) -> TransitionResult:
        """Advance a claimed stage, once, for the generation that did the work.

        Matching on the generation is what stops a slow worker from marking a
        stage complete after a reindex has already moved the job on.

        A job that moves on to another stage is marked as not yet sent, in
        the same statement. Control sends that stage's message after the
        commit, and the sweep sends it again if that fails - so a worker or
        a broker failing between the two cannot leave the job waiting for a
        message nobody will send.

        Completing the index stage is also what activates the generation.
        They are the same event - "this build is now the answer" - and doing
        them in one transaction means there is no instant where a job is
        ready but nothing points at what it built, or the reverse.
        """
        definition = stage_for(stage)
        result = await self._session.execute(
            update(IngestionJob)
            .where(
                IngestionJob.id == job_id,
                IngestionJob.state == definition.active.value,
                IngestionJob.generation_id == generation_id,
            )
            .values(
                state=definition.done.value,
                dispatched_at=(
                    None
                    if stage_waiting(definition.done) is not None
                    else IngestionJob.dispatched_at
                ),
                claimed_at=None,
                lease_expires_at=None,
                error_code=None,
                error_detail=None,
            )
            .returning(IngestionJob.id, IngestionJob.state)
        )
        row = result.one_or_none()
        if row is not None and stage is StageName.INDEX:
            await self._activate(job_id, generation_id)
        return await self._result(job_id, row.state if row is not None else None)

    async def _activate(self, job_id: UUID, generation_id: UUID) -> None:
        """Point the version's index at the generation that just finished.

        An upsert, because a reindex activates a version that already has a
        pointer. The previous generation's chunks are left in the store: they
        are still being read by searches that started before this moment, and
        retiring them is the deletion lifecycle's job.
        """
        job = await self._job(job_id)
        if job is None:  # pragma: no cover - the update above proved it exists
            return
        version, _document, library = await self._context(job)
        statement = (
            insert(DocumentIndex)
            .values(
                document_version_id=version.id,
                library_id=library.id,
                active_generation_id=generation_id,
            )
            .on_conflict_do_update(
                index_elements=[DocumentIndex.document_version_id],
                set_={"active_generation_id": generation_id, "updated_at": func.now()},
            )
        )
        await self._session.execute(statement)

    async def deleted_document_for(self, job_id: UUID) -> UUID | None:
        """The tombstoned document this cleanup job belongs to.

        Requiring the tombstone is the guard: a purge request that named a
        live document would delete rows a user still expects to exist, so it
        finds nothing instead.
        """
        result = await self._session.execute(
            select(Document.id)
            .join(DocumentVersion, DocumentVersion.document_id == Document.id)
            .join(IngestionJob, IngestionJob.document_version_id == DocumentVersion.id)
            .where(IngestionJob.id == job_id, Document.deleted_at.is_not(None))
        )
        return result.scalar_one_or_none()

    async def active_generations(self, library_id: UUID) -> list[UUID]:
        """The generations a search of this library may read.

        Only live documents in a live library, and only versions whose index
        finished. A deleted document stops being searchable the moment its
        tombstone is written, without waiting for its vectors to be removed.
        """
        result = await self._session.execute(
            select(DocumentIndex.active_generation_id)
            .join(DocumentVersion, DocumentIndex.document_version_id == DocumentVersion.id)
            .join(Document, DocumentVersion.document_id == Document.id)
            .join(Library, Document.library_id == Library.id)
            .where(
                DocumentIndex.library_id == library_id,
                DocumentIndex.active_generation_id.is_not(None),
                Document.deleted_at.is_(None),
                Library.deleted_at.is_(None),
            )
        )
        return [generation for generation in result.scalars() if generation is not None]

    async def fail(
        self, job_id: UUID, failure: StageFailure, *, max_attempts: int
    ) -> TransitionResult:
        """Record a failure, retrying only while the budget allows.

        Control holds the hard attempt bound rather than trusting the
        worker's own retry counter, which a broker redelivery resets.
        """
        definition = stage_for(failure.stage)
        job = await self._job(job_id)
        if job is None:
            raise LookupError(job_id)

        exhausted = job.attempt >= max_attempts
        if failure.disposition is FailureDisposition.UNSUPPORTED:
            state = IngestionStatus.UNSUPPORTED
        elif failure.disposition is FailureDisposition.FAILED or exhausted:
            state = IngestionStatus.FAILED
        else:
            # Back to the stage's entry state, lease released, so the next
            # delivery can claim it again.
            state = definition.entry

        result = await self._session.execute(
            update(IngestionJob)
            .where(
                IngestionJob.id == job_id,
                IngestionJob.state == definition.active.value,
                IngestionJob.generation_id == failure.generation_id,
            )
            .values(
                state=state.value,
                claimed_at=None,
                lease_expires_at=None,
                error_code=failure.code,
                error_detail=failure.detail,
            )
            .returning(IngestionJob.state)
        )
        return await self._result(job_id, result.scalar_one_or_none())

    async def mark_dispatched(self, job_id: UUID) -> None:
        """Record that the broker accepted this job's message."""
        await self._session.execute(
            update(IngestionJob)
            .where(IngestionJob.id == job_id)
            .values(
                dispatched_at=func.now(),
                error_code=case(
                    (IngestionJob.error_code == UNDISPATCHED_CODE, None),
                    else_=IngestionJob.error_code,
                ),
                error_detail=case(
                    (IngestionJob.error_code == UNDISPATCHED_CODE, None),
                    else_=IngestionJob.error_detail,
                ),
            )
        )

    async def mark_undispatched(self, job_id: UUID, detail: str) -> None:
        """Say on the job, where its owner will read it, that it was not sent.

        Leaves an error a worker recorded alone, for the reason given on
        `UNDISPATCHED_CODE`. Writing the row also moves `updated_at`, which
        is what spaces the dispatcher's next attempt from this one.
        """
        await self._session.execute(
            update(IngestionJob)
            .where(
                IngestionJob.id == job_id,
                IngestionJob.dispatched_at.is_(None),
                IngestionJob.error_code.is_(None) | (IngestionJob.error_code == UNDISPATCHED_CODE),
            )
            .values(error_code=UNDISPATCHED_CODE, error_detail=detail)
        )

    async def lock_undispatched(
        self, *, unchanged_for: timedelta, limit: int
    ) -> list[tuple[UUID, StageName]]:
        """Jobs waiting on a message nobody sent, locked for this transaction.

        `unchanged_for` leaves a job just committed to the publish its own
        request is about to make. `SKIP LOCKED` is what lets every Control
        replica run this at once: a row one replica is re-sending is not
        visible to another, so a job gets one message per pass, not one per
        replica. A live lease excludes a job a worker holds; one that has
        expired does not, since that worker is gone and sent nothing.
        """
        result = await self._session.execute(
            select(IngestionJob.id, IngestionJob.state)
            .where(
                IngestionJob.state.in_([state.value for state in WAITING_FOR]),
                IngestionJob.dispatched_at.is_(None),
                IngestionJob.updated_at < func.now() - unchanged_for,
                (IngestionJob.lease_expires_at.is_(None))
                | (IngestionJob.lease_expires_at < func.now()),
            )
            .order_by(IngestionJob.updated_at)
            .limit(limit)
            .with_for_update(skip_locked=True)
        )
        return [(job_id, WAITING_FOR[IngestionStatus(state)]) for job_id, state in result]

    async def _result(self, job_id: UUID, updated: str | None) -> TransitionResult:
        """Report what the row holds now, applied or not.

        A transition that matched nothing is not an error: it is how a
        redelivered completion, or one for a superseded generation, is
        distinguished from the first.
        """
        if updated is not None:
            return TransitionResult(applied=True, status=IngestionStatus(updated))
        job = await self._job(job_id)
        if job is None:
            raise LookupError(job_id)
        return TransitionResult(applied=False, status=IngestionStatus(job.state))
