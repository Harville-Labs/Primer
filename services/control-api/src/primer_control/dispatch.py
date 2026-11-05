"""Sending jobs to the workers, and sending again what did not arrive.

A route commits a job and then publishes it after the response, so the
upload has already returned 200 by the time the broker is asked. If the
broker is down at that moment the job is real but no worker will ever hear
of it. Each job therefore records whether its message was accepted
(`dispatched_at`), and a sweep in Control re-sends the ones that were not.

A sweep rather than a retry button, because the person who would see the
button cannot fix the cause: the broker is the operator's. A button would
leave every document uploaded during an outage stuck until its owner
noticed and pressed it - and a deletion job has no row left on screen to
press it on. The sweep needs nobody, and also covers Control stopping
between commit and publish, which no request would ever learn about.

In the API process rather than a CronJob like Studio's retention, which
deliberately is not: that job deletes, and an operator should be able to
see and suspend it. Re-sending deletes nothing, needs to run under Compose
too, where there is no CronJob, and is safe on every replica at once - see
`IngestionJobRepository.lock_undispatched`.

Re-sending is safe against the worker for the same reason redelivery is:
the claim is a compare-and-set on state and lease, so a second message for
a job that is already claimed or finished is refused, not worked twice.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import timedelta
from typing import Annotated
from uuid import UUID

from fastapi import Depends, Request
from primer_contracts.ingestion import StageName
from primer_service.db import Database

from primer_control.publisher import BrokerUnreachable, JobPublisher
from primer_control.repositories.ingestion_jobs import IngestionJobRepository

logger = logging.getLogger(__name__)

#: What the document's owner reads while the job waits. It says the problem
#: is not their file and that they need not do anything.
UNREACHABLE_DETAIL = (
    "Waiting: the ingestion workers can't be reached. "
    "Primer will send this again automatically once they can."
)

#: Jobs re-sent per transaction, so a backlog from a long outage does not
#: hold every row's lock while each one is published.
BATCH = 100


class JobDispatcher:
    """Publishes jobs and keeps the record of which ones were sent."""

    def __init__(
        self, database: Database, publisher: JobPublisher, *, resend_after: timedelta
    ) -> None:
        self._database = database
        self._publisher = publisher
        self._resend_after = resend_after

    async def dispatch(self, stage: StageName, job_id: UUID) -> None:
        """Send one job just committed by a request. Never raises for the broker.

        Runs as a background task after the response, so an exception here
        would reach nobody but the log - as a traceback, which is how the
        original failure went unnoticed. A broker that is down is recorded on
        the job instead, and the sweep sends it later.
        """
        sent = await self._send(stage, job_id)
        async with self._database.session() as session:
            repository = IngestionJobRepository(session)
            if sent:
                await repository.mark_dispatched(job_id)
            else:
                await repository.mark_undispatched(job_id, UNREACHABLE_DETAIL)

    async def resend_undispatched(self) -> int:
        """Send every job that has waited too long without a message.

        Returns how many were sent. Stops at the first broker failure: the
        rest of the batch would fail the same way, and one log line says so
        as well as a hundred.
        """
        resent = 0
        while True:
            async with self._database.session() as session:
                repository = IngestionJobRepository(session)
                waiting = await repository.lock_undispatched(
                    unchanged_for=self._resend_after, limit=BATCH
                )
                for job_id, stage in waiting:
                    # Published while the row is locked, so another replica
                    # cannot pick the same job until this one has recorded
                    # whether it went.
                    if not await self._send(stage, job_id):
                        await repository.mark_undispatched(job_id, UNREACHABLE_DETAIL)
                        return resent
                    await repository.mark_dispatched(job_id)
                    resent += 1
            if len(waiting) < BATCH:
                return resent

    async def run(self, interval: timedelta) -> None:
        """Sweep forever. Cancelled when the application shuts down."""
        while True:
            try:
                resent = await self.resend_undispatched()
                if resent:
                    logger.info("re-sent %d ingestion jobs that had not reached a worker", resent)
            except Exception:
                # A sweep that dies on one bad pass - the database restarting,
                # say - would silently stop recovering anything until Control
                # restarted. Log it and try again next interval.
                logger.exception("re-sending undispatched ingestion jobs failed")
            await asyncio.sleep(interval.total_seconds())

    async def _send(self, stage: StageName, job_id: UUID) -> bool:
        """Publish off the event loop, and translate the broker being down."""
        try:
            await asyncio.to_thread(self._publisher.publish, stage, job_id)
        except BrokerUnreachable as error:
            logger.warning("job %s for %s was not sent: %s", job_id, stage, error)
            return False
        return True


def get_dispatcher(request: Request) -> JobDispatcher:
    dispatcher: JobDispatcher = request.app.state.dispatcher
    return dispatcher


Dispatcher = Annotated[JobDispatcher, Depends(get_dispatcher)]
