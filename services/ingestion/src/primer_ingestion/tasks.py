"""Stage orchestration.

The shape of every task is the same: claim, work, confirm the claim still
holds, complete. Control sends the next stage's message once completion
commits, so a worker that dies after completing cannot strand the job
between stages. None of those steps trusts the
message it arrived on - the message carries only a job id, and Control
decides at each step whether this worker is still the one doing the work.

Handlers are looked up rather than called directly so the Docling and
Haystack pipelines can register their stages without this module importing
either of them.
"""

from __future__ import annotations

import logging
import secrets
from collections.abc import Callable
from dataclasses import dataclass
from uuid import UUID

from celery import Task
from primer_contracts.ingestion import (
    ClaimOutcome,
    FailureDisposition,
    JobClaim,
    StageFailure,
    StageName,
)

from primer_ingestion.celery_app import TASK_NAMES, create_celery
from primer_ingestion.config import Settings
from primer_ingestion.control_client import ControlClient, JobTransitions
from primer_ingestion.errors import PermanentStageError, StageError

logger = logging.getLogger(__name__)

app = create_celery()

StageHandler = Callable[[JobClaim], None]
#: Populated by the parsing and retrieval pipelines as they land. A stage
#: with no handler fails permanently rather than silently succeeding, so a
#: half-deployed cluster cannot mark documents ready without indexing them.
HANDLERS: dict[StageName, StageHandler] = {}


def register_handler(stage: StageName, handler: StageHandler) -> None:
    HANDLERS[stage] = handler


def stage_unavailable(claim: JobClaim) -> None:
    raise PermanentStageError(
        "stage_unavailable",
        "This deployment has no worker able to run that stage.",
    )


@dataclass(frozen=True)
class StageOutcome:
    """What happened, and whether the caller should schedule a retry."""

    outcome: ClaimOutcome
    retry: bool = False
    error: StageError | None = None


def execute_stage(
    stage: StageName,
    job_id: UUID,
    *,
    control: JobTransitions,
    handler: StageHandler,
    retries_left: bool,
) -> StageOutcome:
    """Run one stage of one job.

    Every early return is a normal outcome of at-least-once delivery. The
    caller acknowledges the message in all of them: re-running a stage the
    job has already passed is exactly what this protocol exists to prevent.
    """
    response = control.claim(job_id, stage)
    if response.outcome is not ClaimOutcome.CLAIMED or response.claim is None:
        logger.info("job %s: nothing to do for %s (%s)", job_id, stage, response.outcome)
        return StageOutcome(response.outcome)

    claim = response.claim
    try:
        handler(claim)
    except StageError as error:
        return _report_failure(control, claim, error, retries_left=retries_left)
    except Exception:
        # The trace goes to the worker log, correlated by job id; only a
        # sanitized code reaches the row a user can read.
        logger.exception("job %s: %s stage raised", job_id, stage)
        return _report_failure(
            control,
            claim,
            StageError("stage_error", "The stage failed unexpectedly."),
            retries_left=retries_left,
        )

    # Cancellation and reindexing both happen while a stage is running, and
    # a stage can take minutes. Confirming the claim still holds before
    # completing is the second half of "check before and after the work".
    if not control.heartbeat(job_id, stage, claim.generation_id).applied:
        logger.info("job %s: claim on %s no longer holds; discarding result", job_id, stage)
        return StageOutcome(ClaimOutcome.SUPERSEDED)

    if control.complete(job_id, stage, claim.generation_id).applied:
        _after_activation(handler, claim)
    return StageOutcome(ClaimOutcome.CLAIMED)


def _after_activation(handler: StageHandler, claim: JobClaim) -> None:
    """Run a stage's follow-up work, if it has any.

    Retiring a superseded index can only happen once Control has switched to
    the new one, which is what completing the stage does. A failure here
    leaves storage to reclaim, not a wrong answer, so it is logged rather
    than failing a job that has already succeeded.
    """
    follow_up = getattr(handler, "retire_superseded", None)
    if follow_up is None:
        return
    try:
        follow_up(claim)
    except Exception:
        logger.exception("job %s: could not retire superseded generations", claim.job_id)


def _report_failure(
    control: JobTransitions,
    claim: JobClaim,
    error: StageError,
    *,
    retries_left: bool,
) -> StageOutcome:
    """Tell Control how the stage failed, and whether it is worth trying again."""
    disposition = error.disposition
    if disposition is FailureDisposition.RETRY and not retries_left:
        disposition = FailureDisposition.FAILED

    control.fail(
        claim.job_id,
        StageFailure(
            stage=claim.stage,
            generation_id=claim.generation_id,
            code=error.code,
            detail=error.detail,
            disposition=disposition,
        ),
    )
    return StageOutcome(
        ClaimOutcome.CLAIMED,
        retry=disposition is FailureDisposition.RETRY,
        error=error,
    )


def backoff_seconds(retries: int, settings: Settings) -> float:
    """Exponential backoff with jitter, capped.

    Without jitter, a broker outage retried by every worker at once returns
    as a synchronized thundering herd the moment it recovers.
    """
    base = min(
        settings.retry_backoff_seconds * (2**retries),
        settings.retry_backoff_max_seconds,
    )
    # Not security-sensitive: this only spreads retries out in time.
    return base * (0.5 + secrets.randbelow(1000) / 2000)


def run_task(task: Task, stage: StageName, job_id: str) -> str:
    settings: Settings = task.app.conf.primer_settings
    retries_left = task.request.retries < settings.max_retries

    with ControlClient(settings) as control:
        result = execute_stage(
            stage,
            UUID(job_id),
            control=control,
            handler=HANDLERS.get(stage, stage_unavailable),
            retries_left=retries_left,
        )

    if result.retry:
        raise task.retry(
            exc=result.error,
            countdown=backoff_seconds(task.request.retries, settings),
            max_retries=settings.max_retries,
        )
    return result.outcome.value


@app.task(bind=True, name=TASK_NAMES[StageName.PARSE])
def parse_job(self: Task, job_id: str) -> str:
    return run_task(self, StageName.PARSE, job_id)


@app.task(bind=True, name=TASK_NAMES[StageName.EMBED])
def embed_job(self: Task, job_id: str) -> str:
    return run_task(self, StageName.EMBED, job_id)


@app.task(bind=True, name=TASK_NAMES[StageName.INDEX])
def index_job(self: Task, job_id: str) -> str:
    return run_task(self, StageName.INDEX, job_id)


@app.task(bind=True, name=TASK_NAMES[StageName.DELETE])
def delete_job(self: Task, job_id: str) -> str:
    return run_task(self, StageName.DELETE, job_id)
