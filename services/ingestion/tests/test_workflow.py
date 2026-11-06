"""Stage orchestration under at-least-once delivery.

These tests use a fake Control rather than a server: what is under test is
how a worker reacts to each transition outcome, and the outcomes themselves
are proven against a real database in the Control API's integration tests.
"""

from __future__ import annotations

import uuid
from uuid import UUID

import pytest
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
from primer_ingestion.celery_app import QUEUE_NAMES, TASK_NAMES
from primer_ingestion.config import Settings
from primer_ingestion.errors import PermanentStageError, StageError, UnsupportedDocument
from primer_ingestion.tasks import (
    backoff_seconds,
    execute_stage,
    stage_unavailable,
)

JOB_ID = UUID("11111111-1111-5111-8111-111111111111")
GENERATION_ID = UUID("22222222-2222-5222-8222-222222222222")


def make_claim(stage: StageName, *, attempt: int = 1) -> JobClaim:
    return JobClaim(
        job_id=JOB_ID,
        stage=stage,
        generation_id=GENERATION_ID,
        attempt=attempt,
        document_id=uuid.uuid5(uuid.NAMESPACE_URL, "document"),
        document_version_id=uuid.uuid5(uuid.NAMESPACE_URL, "version"),
        library_id=uuid.uuid5(uuid.NAMESPACE_URL, "library"),
        owner_user_id=uuid.uuid5(uuid.NAMESPACE_URL, "owner"),
        source_sha256="a" * 64,
        filename="paper.pdf",
        media_type="application/pdf",
        byte_size=1024,
    )


class FakeControl:
    """A scripted Control, recording what the worker asked it to do."""

    def __init__(self, outcomes: list[ClaimOutcome] | None = None) -> None:
        self.outcomes = outcomes or [ClaimOutcome.CLAIMED]
        self.claim_calls: list[StageName] = []
        self.completed: list[StageName] = []
        self.failures: list[StageFailure] = []
        self.heartbeat_applies = True
        self.complete_applies = True

    def claim(self, job_id: UUID, stage: StageName) -> ClaimResponse:
        self.claim_calls.append(stage)
        index = min(len(self.claim_calls) - 1, len(self.outcomes) - 1)
        outcome = self.outcomes[index]
        if outcome is not ClaimOutcome.CLAIMED:
            return ClaimResponse(outcome=outcome)
        return ClaimResponse(outcome=outcome, claim=make_claim(stage))

    def heartbeat(self, job_id: UUID, stage: StageName, generation_id: UUID) -> TransitionResult:
        return TransitionResult(applied=self.heartbeat_applies, status=IngestionStatus.PARSING)

    def complete(self, job_id: UUID, stage: StageName, generation_id: UUID) -> TransitionResult:
        if self.complete_applies:
            self.completed.append(stage)
        return TransitionResult(applied=self.complete_applies, status=IngestionStatus.CHUNKING)

    def fail(self, job_id: UUID, failure: StageFailure) -> TransitionResult:
        self.failures.append(failure)
        return TransitionResult(applied=True, status=IngestionStatus.FAILED)

    def purge(self, job_id: UUID) -> list[str]:
        return []


class RecordingStage:
    def __init__(self, error: Exception | None = None) -> None:
        self.call_count = 0
        self.error = error

    def __call__(self, claim: JobClaim) -> None:
        self.call_count += 1
        if self.error is not None:
            raise self.error


def run(
    stage: StageName,
    control: FakeControl,
    handler: RecordingStage,
    *,
    retries_left: bool = True,
):
    return execute_stage(
        stage,
        JOB_ID,
        control=control,
        handler=handler,
        retries_left=retries_left,
    )


def test_second_delivery_does_not_repeat_a_completed_stage() -> None:
    """The core guarantee: at-least-once delivery, exactly-once work."""
    control = FakeControl([ClaimOutcome.CLAIMED, ClaimOutcome.ALREADY_COMPLETED])
    handler = RecordingStage()

    run(StageName.PARSE, control, handler)
    second = run(StageName.PARSE, control, handler)

    assert handler.call_count == 1
    assert second.outcome is ClaimOutcome.ALREADY_COMPLETED
    assert control.completed == [StageName.PARSE]


@pytest.mark.parametrize(
    "outcome",
    [ClaimOutcome.IN_PROGRESS, ClaimOutcome.ALREADY_COMPLETED, ClaimOutcome.CANCELLED],
)
def test_a_refused_claim_does_no_work(outcome: ClaimOutcome) -> None:
    control = FakeControl([outcome])
    handler = RecordingStage()

    result = run(StageName.PARSE, control, handler)

    assert result.outcome is outcome
    assert result.retry is False
    assert handler.call_count == 0
    assert control.completed == []
    assert control.failures == []


def test_work_finished_after_supersession_is_discarded() -> None:
    """A reindex during a slow stage must not have its result overwritten."""
    control = FakeControl()
    control.heartbeat_applies = False
    handler = RecordingStage()

    result = run(StageName.PARSE, control, handler)

    assert handler.call_count == 1
    assert result.outcome is ClaimOutcome.SUPERSEDED
    assert control.completed == []


def test_an_unsupported_document_is_not_retried() -> None:
    control = FakeControl()
    handler = RecordingStage(UnsupportedDocument("ocr_required", "This PDF holds no text."))

    result = run(StageName.PARSE, control, handler)

    assert result.retry is False
    assert control.failures[0].disposition is FailureDisposition.UNSUPPORTED
    assert control.failures[0].code == "ocr_required"
    assert control.completed == []


def test_a_transient_failure_is_retried_while_the_budget_allows() -> None:
    control = FakeControl()
    handler = RecordingStage(StageError("embedding_unavailable", "The endpoint timed out."))

    result = run(StageName.PARSE, control, handler, retries_left=True)

    assert result.retry is True
    assert control.failures[0].disposition is FailureDisposition.RETRY


def test_an_exhausted_retry_budget_fails_terminally() -> None:
    """The last attempt reports failure rather than asking for another turn."""
    control = FakeControl()
    handler = RecordingStage(StageError("embedding_unavailable", "The endpoint timed out."))

    result = run(StageName.PARSE, control, handler, retries_left=False)

    assert result.retry is False
    assert control.failures[0].disposition is FailureDisposition.FAILED


def test_an_unexpected_exception_reports_a_sanitized_code() -> None:
    """A stack trace belongs in the log, not in a field a user can read."""
    control = FakeControl()
    handler = RecordingStage(RuntimeError("connection string postgres://user:hunter2@db"))

    result = run(StageName.PARSE, control, handler)

    failure = control.failures[0]
    assert failure.code == "stage_error"
    assert failure.detail is not None
    assert "hunter2" not in failure.detail
    assert result.retry is True


def test_a_stage_with_no_handler_fails_permanently() -> None:
    """A half-deployed cluster must not mark documents ready."""
    with pytest.raises(PermanentStageError) as raised:
        stage_unavailable(make_claim(StageName.EMBED))
    assert raised.value.code == "stage_unavailable"
    assert raised.value.disposition is FailureDisposition.FAILED


def test_backoff_grows_and_stays_within_its_cap() -> None:
    settings = Settings(retry_backoff_seconds=10, retry_backoff_max_seconds=100)
    early = [backoff_seconds(0, settings) for _ in range(50)]
    late = [backoff_seconds(20, settings) for _ in range(50)]

    assert all(5 <= value <= 10 for value in early)
    assert all(50 <= value <= 100 for value in late)
    # Jittered, so a broker outage does not return as a synchronized herd.
    assert len(set(early)) > 1


def test_every_stage_has_a_task_and_a_queue() -> None:
    """Task names are wire protocol, so a missing one is a silent dead letter."""
    assert set(TASK_NAMES) == set(StageName)
    assert set(QUEUE_NAMES) == set(StageName)
