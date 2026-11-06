"""Jobs whose message never reached the broker, and sending them again.

Seen on a live deployment: the broker URL named a host that no longer
resolved, every upload returned 200, and each job sat in `queued` with no
message while the document said it was waiting for a worker - for good.
"""

from __future__ import annotations

import asyncio
import time
from datetime import timedelta
from typing import Any
from uuid import UUID

import pytest
import pytest_asyncio
from control_credentials import FORWARDED, WORKER_TOKEN
from control_support import ServiceClient, UserClient
from httpx2 import ASGITransport, AsyncClient
from primer_contracts.ingestion import StageName
from primer_control.app import create_app
from primer_control.config import Settings
from primer_control.dispatch import UNREACHABLE_DETAIL, JobDispatcher
from primer_control.models import IngestionJob
from primer_control.publisher import BrokerUnreachable
from primer_service.db import Database
from primer_storage import SourceStore
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncEngine


class FlakyPublisher:
    """A broker that is down until told otherwise, and records what reached it."""

    def __init__(self) -> None:
        self.reachable = True
        self.delay = 0.0
        self.attempts = 0
        self.sent: list[tuple[StageName, UUID]] = []

    def publish(self, stage: StageName, job_id: UUID) -> None:
        self.attempts += 1
        time.sleep(self.delay)
        if not self.reachable:
            raise BrokerUnreachable("rabbitmq.test:5672", "Name or service not known")
        self.sent.append((stage, job_id))


@pytest.fixture
def publisher() -> FlakyPublisher:
    return FlakyPublisher()


@pytest_asyncio.fixture
async def client(
    database: Database,
    clean_tables: AsyncEngine,
    source_store: SourceStore,
    publisher: FlakyPublisher,
) -> Any:
    app = create_app(
        Settings(auth_mode="oidc", max_upload_bytes=4096, worker_token=WORKER_TOKEN),
        database=database,
        source_store=source_store,
        publisher=publisher,
    )
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://control", headers=FORWARDED
    ) as http:
        yield http


@pytest.fixture
def owner(client: AsyncClient) -> UserClient:
    return UserClient(client, "oidc-owner")


@pytest.fixture
def worker(client: AsyncClient) -> ServiceClient:
    return ServiceClient(client, WORKER_TOKEN)


@pytest_asyncio.fixture
async def library_id(owner: UserClient) -> str:
    response = await owner.post("/api/v1/libraries", {"name": "Sources"})
    return str(response.json()["id"])


def sweeper(database: Database, publisher: FlakyPublisher) -> JobDispatcher:
    """A dispatcher that considers any unsent job overdue, as a sweep long after would."""
    return JobDispatcher(database, publisher, resend_after=timedelta(0))


async def upload(owner: UserClient, library_id: str) -> str:
    response = await owner.upload(library_id, "paper.txt", b"evidence")
    assert response.status_code == 201
    return str(response.json()["id"])


async def only_job(database: Database) -> IngestionJob:
    async with database.session() as session:
        return (await session.execute(select(IngestionJob))).scalar_one()


async def test_a_failed_publish_tells_the_owner_the_workers_are_unreachable(
    owner: UserClient, library_id: str, publisher: FlakyPublisher
) -> None:
    """The generic "waiting for a worker" is what hid the outage."""
    publisher.reachable = False
    document_id = await upload(owner, library_id)

    document = (await owner.get(f"/api/v1/libraries/{library_id}/documents/{document_id}")).json()

    assert publisher.attempts == 1
    assert document["status"] == "queued"
    assert document["status_detail"] == UNREACHABLE_DETAIL


async def test_a_job_whose_publish_failed_is_sent_exactly_once_when_the_broker_returns(
    owner: UserClient, library_id: str, publisher: FlakyPublisher, database: Database
) -> None:
    publisher.reachable = False
    document_id = await upload(owner, library_id)
    job = await only_job(database)
    publisher.reachable = True

    dispatcher = sweeper(database, publisher)
    assert await dispatcher.resend_undispatched() == 1
    # A second pass is the guard: a sweep that forgot it had sent the job
    # would send it again on every interval, forever.
    assert await dispatcher.resend_undispatched() == 0

    assert publisher.sent == [(StageName.PARSE, job.id)]
    document = (await owner.get(f"/api/v1/libraries/{library_id}/documents/{document_id}")).json()
    assert document["status_detail"] is None


async def test_a_sweep_while_the_broker_is_still_down_sends_nothing_and_keeps_waiting(
    owner: UserClient, library_id: str, publisher: FlakyPublisher, database: Database
) -> None:
    publisher.reachable = False
    await upload(owner, library_id)

    assert await sweeper(database, publisher).resend_undispatched() == 0

    job = await only_job(database)
    assert publisher.sent == []
    assert job.dispatched_at is None
    assert job.error_detail == UNREACHABLE_DETAIL


async def test_a_job_that_was_published_is_never_sent_again(
    owner: UserClient, library_id: str, publisher: FlakyPublisher, database: Database
) -> None:
    """A message waiting in a queue with no worker running is not lost."""
    await upload(owner, library_id)

    assert await sweeper(database, publisher).resend_undispatched() == 0
    assert len(publisher.sent) == 1


async def test_a_fresh_job_is_left_to_the_publish_its_own_request_makes(
    owner: UserClient, library_id: str, publisher: FlakyPublisher, database: Database
) -> None:
    publisher.reachable = False
    await upload(owner, library_id)
    publisher.reachable = True

    patient = JobDispatcher(database, publisher, resend_after=timedelta(hours=1))

    assert await patient.resend_undispatched() == 0
    assert publisher.sent == []


async def test_a_deletion_whose_publish_failed_is_sent_as_a_deletion(
    owner: UserClient, library_id: str, publisher: FlakyPublisher, database: Database
) -> None:
    """The case a retry button could never cover: the document is already gone."""
    document_id = await upload(owner, library_id)
    publisher.reachable = False
    response = await owner.delete(f"/api/v1/libraries/{library_id}/documents/{document_id}")
    assert response.status_code == 204
    publisher.reachable = True

    assert await sweeper(database, publisher).resend_undispatched() == 1

    job = await only_job(database)
    assert publisher.sent[-1] == (StageName.DELETE, job.id)


async def test_a_reindex_whose_publish_failed_is_sent_again(
    owner: UserClient, library_id: str, publisher: FlakyPublisher, database: Database
) -> None:
    """A reindex reuses the job row, which was marked sent for its first build."""
    document_id = await upload(owner, library_id)
    async with database.session() as session:
        await session.execute(update(IngestionJob).values(state="ready"))
    publisher.reachable = False
    path = f"/api/v1/libraries/{library_id}/documents/{document_id}/reindex"
    assert (await owner.post(path, {})).status_code == 202
    publisher.reachable = True

    assert await sweeper(database, publisher).resend_undispatched() == 1
    assert len(publisher.sent) == 2


async def test_replicas_sweeping_at_once_send_a_job_once(
    owner: UserClient, library_id: str, publisher: FlakyPublisher, database: Database
) -> None:
    """Every Control replica runs the sweep; the row lock is what keeps them apart."""
    publisher.reachable = False
    await upload(owner, library_id)
    publisher.reachable = True
    # Slow enough that the second replica looks while the first holds the row.
    publisher.delay = 0.3

    first, second = sweeper(database, publisher), sweeper(database, publisher)
    sent = await asyncio.gather(first.resend_undispatched(), second.resend_undispatched())

    assert sorted(sent) == [0, 1]
    assert len(publisher.sent) == 1


async def test_a_workers_own_error_is_not_erased_by_a_send(
    owner: UserClient, library_id: str, publisher: FlakyPublisher, database: Database
) -> None:
    """Only the dispatcher's own note is the dispatcher's to clear."""
    await upload(owner, library_id)
    async with database.session() as session:
        await session.execute(
            update(IngestionJob).values(
                dispatched_at=None, error_code="parse_timeout", error_detail="Took too long."
            )
        )

    assert await sweeper(database, publisher).resend_undispatched() == 1

    job = await only_job(database)
    assert job.error_code == "parse_timeout"
    assert job.error_detail == "Took too long."


async def parse(worker: ServiceClient, database: Database) -> IngestionJob:
    """Claim and complete the parse stage, as a worker would."""
    job = await only_job(database)
    claim = (await worker.claim(str(job.id))).json()["claim"]
    response = await worker.complete(str(job.id), claim["generation_id"])
    assert response.json()["status"] == "chunking"
    return job


async def test_completing_a_stage_sends_the_next_one(
    owner: UserClient,
    worker: ServiceClient,
    library_id: str,
    publisher: FlakyPublisher,
    database: Database,
) -> None:
    """Control, not the worker that finished, queues the following stage."""
    await upload(owner, library_id)

    job = await parse(worker, database)

    assert publisher.sent == [(StageName.PARSE, job.id), (StageName.EMBED, job.id)]
    assert (await only_job(database)).dispatched_at is not None


async def test_a_job_between_stages_is_sent_again_when_its_message_was_lost(
    owner: UserClient,
    worker: ServiceClient,
    library_id: str,
    publisher: FlakyPublisher,
    database: Database,
) -> None:
    """Seen as documents stuck in `chunking`: the worker that finished parsing
    published the next stage itself, and when that publish failed nothing
    knew a message was owed."""
    await upload(owner, library_id)
    publisher.reachable = False
    job = await parse(worker, database)
    publisher.reachable = True

    assert await sweeper(database, publisher).resend_undispatched() == 1
    assert publisher.sent[-1] == (StageName.EMBED, job.id)


async def test_completing_the_last_stage_sends_nothing(
    owner: UserClient,
    worker: ServiceClient,
    library_id: str,
    publisher: FlakyPublisher,
    database: Database,
) -> None:
    await upload(owner, library_id)
    job = await parse(worker, database)
    for stage in ("embed", "index"):
        claim = (await worker.claim(str(job.id), stage)).json()["claim"]
        await worker.complete(str(job.id), claim["generation_id"], stage)

    assert [stage for stage, _ in publisher.sent] == [
        StageName.PARSE,
        StageName.EMBED,
        StageName.INDEX,
    ]
    assert await sweeper(database, publisher).resend_undispatched() == 0
