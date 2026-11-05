"""Handing a job to the workers.

Publishing is deliberately not part of the upload transaction. A message
sent before the commit can outlive a rollback, and a worker would then claim
a job that does not exist. Routes publish through a background task, which
runs after the response - and therefore after the session dependency has
committed - so a message only ever names a job that is really there.

The reverse failure is possible and is the acceptable one: a broker that is
down, or a crash between commit and publish, leaves a job queued in the
database with no message. `primer_control.dispatch` is what makes that
recoverable: it records which jobs were actually sent and re-sends the rest.
"""

from __future__ import annotations

import logging
from typing import Protocol
from urllib.parse import urlsplit
from uuid import UUID

from primer_contracts.ingestion import StageName

logger = logging.getLogger(__name__)

#: Must match the worker's task and queue names, which are the wire protocol
#: between the two services.
TASK_NAMES: dict[StageName, str] = {
    StageName.PARSE: "ingestion.parse",
    StageName.EMBED: "ingestion.embed",
    StageName.INDEX: "ingestion.index",
    StageName.DELETE: "ingestion.delete",
}

#: Also part of that wire protocol, and not optional: RabbitMQ refuses to
#: redeclare an existing queue with arguments that disagree with the ones it
#: was created with (406 PRECONDITION_FAILED) rather than ignoring the
#: mismatch. A publisher that declares these queues with the worker's
#: defaults - no dead-letter exchange, a classic rather than a quorum queue -
#: does not get a different queue; it gets every publish refused, silently,
#: after the upload it belongs to has already returned 200. Keep this equal
#: to `primer_ingestion.celery_app.build_queues()`; the two are duplicated
#: because Control does not depend on the worker package, not because the
#: topology is allowed to differ.
QUEUE_ARGUMENTS: dict[str, object] = {
    "x-queue-type": "quorum",
    "x-dead-letter-exchange": "ingestion.dlx",
    "x-dead-letter-routing-key": "dead",
}


class BrokerUnreachable(Exception):
    """The broker refused or never answered, so the job was not sent.

    Raised in place of kombu's own error, whose traceback leads with an
    unrelated `AttributeError` from its lazy channel and names neither the
    broker nor the fact that nothing was published.
    """

    def __init__(self, host: str, reason: str) -> None:
        super().__init__(f"the ingestion broker at {host} is unreachable: {reason}")
        self.host = host


class JobPublisher(Protocol):
    def publish(self, stage: StageName, job_id: UUID) -> None:
        """Send the job's stage to the workers, or raise `BrokerUnreachable`."""
        ...


class NullPublisher:
    """Records the intent and does nothing.

    Used when no broker is configured, which is the default for a local
    checkout. Uploads still work and their jobs still exist; they simply stay
    queued until a worker deployment gives them somewhere to go.
    """

    def publish(self, stage: StageName, job_id: UUID) -> None:
        logger.info("no broker configured; job %s stays queued for %s", job_id, stage)


class CeleryJobPublisher:
    """Publishes onto the same broker topology the workers consume."""

    def __init__(self, broker_url: str) -> None:
        from celery import Celery
        from kombu import Exchange, Queue

        self._host = broker_host(broker_url)
        exchange = Exchange("ingestion", type="direct")
        self._app = Celery("primer_control_publisher", broker=broker_url)
        self._app.conf.update(
            task_serializer="json",
            # Without confirms, a broker that drops the message says nothing,
            # and the job sits queued forever with no sign it was never sent.
            broker_transport_options={"confirm_publish": True},
            # Declared with the worker's own queue arguments, so a publish
            # against a queue the worker already created is a no-op rather
            # than a rejected redeclaration - see QUEUE_ARGUMENTS above.
            task_queues=tuple(
                Queue(name, exchange, routing_key=name, queue_arguments=QUEUE_ARGUMENTS)
                for name in TASK_NAMES.values()
            ),
            task_default_exchange="ingestion",
        )

    def publish(self, stage: StageName, job_id: UUID) -> None:
        from kombu.exceptions import OperationalError

        try:
            self._app.send_task(TASK_NAMES[stage], args=[str(job_id)], queue=TASK_NAMES[stage])
        except OperationalError as error:
            # `from None`: the chained context is kombu's lazy-channel
            # AttributeError, which is what made the original log unreadable.
            raise BrokerUnreachable(self._host, str(error)) from None


def broker_host(broker_url: str) -> str:
    """Where the broker is, for a log line, with any credentials left out.

    Built up from the parts rather than by masking the password, so a
    username, or a password containing `@`, cannot leak through a pattern
    that missed it.
    """
    parts = urlsplit(broker_url)
    host = parts.hostname or "an unnamed host"
    return f"{host}:{parts.port}" if parts.port else host


def build_publisher(broker_url: str | None) -> JobPublisher:
    return CeleryJobPublisher(broker_url) if broker_url else NullPublisher()
