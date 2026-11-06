"""Handing uploads to the workers, and when.

A message that outlives a rolled-back upload points at a job that does not
exist. These assert the ordering that prevents it.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import pytest_asyncio
from control_credentials import FORWARDED
from control_support import UserClient
from httpx2 import ASGITransport, AsyncClient
from primer_contracts.ingestion import StageName
from primer_control.app import create_app
from primer_control.config import Settings
from primer_control.models import IngestionJob
from primer_service.db import Database
from primer_storage import SourceStore
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncEngine


class RecordingPublisher:
    """Records what was published, and what the database held at that moment."""

    def __init__(self, database: Database) -> None:
        self.database = database
        self.published: list[tuple[StageName, Any]] = []

    def publish(self, stage: StageName, job_id: Any) -> None:
        self.published.append((stage, job_id))


@pytest_asyncio.fixture
async def publisher(database: Database) -> RecordingPublisher:
    return RecordingPublisher(database)


@pytest_asyncio.fixture
async def client(
    database: Database,
    clean_tables: AsyncEngine,
    source_store: SourceStore,
    publisher: RecordingPublisher,
) -> Any:
    app = create_app(
        Settings(auth_mode="oidc", max_upload_bytes=4096),
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


@pytest_asyncio.fixture
async def library_id(owner: UserClient) -> str:
    response = await owner.post("/api/v1/libraries", {"name": "Sources"})
    return str(response.json()["id"])


async def test_a_successful_upload_is_queued_for_parsing(
    owner: UserClient, library_id: str, publisher: RecordingPublisher, database: Database
) -> None:
    await owner.upload(library_id, "paper.txt", b"evidence")

    assert len(publisher.published) == 1
    stage, job_id = publisher.published[0]
    assert stage is StageName.PARSE

    # The published id names a job that is committed and readable, which is
    # the whole point of publishing after the response.
    async with database.session() as session:
        job = (await session.execute(select(IngestionJob))).scalar_one()
    assert str(job.id) == str(job_id)


async def test_a_rejected_upload_queues_nothing(
    owner: UserClient, library_id: str, publisher: RecordingPublisher
) -> None:
    """No job exists, so no message may name one."""
    await owner.upload(library_id, "fake.pdf", b"not a pdf")
    await owner.upload(library_id, "huge.txt", b"x" * 8192)

    assert publisher.published == []


async def test_an_upload_to_a_forbidden_library_queues_nothing(
    client: AsyncClient, library_id: str, publisher: RecordingPublisher
) -> None:
    stranger = UserClient(client, "oidc-stranger")

    assert (await stranger.upload(library_id, "intrusion.txt", b"nope")).status_code == 404
    assert publisher.published == []


async def test_a_replacement_queues_its_own_version(
    owner: UserClient, library_id: str, publisher: RecordingPublisher, database: Database
) -> None:
    original = (await owner.upload(library_id, "draft.txt", b"first")).json()
    await owner.upload(library_id, "final.txt", b"second", document_id=original["id"])

    async with database.session() as session:
        count = (await session.execute(select(func.count()).select_from(IngestionJob))).scalar_one()

    assert count == 2
    assert len(publisher.published) == 2
    published = [job_id for _stage, job_id in publisher.published]
    assert len(set(published)) == 2


async def test_without_a_broker_uploads_still_work(
    database: Database, clean_tables: AsyncEngine, source_store: SourceStore, tmp_path: Path
) -> None:
    """A local checkout has no RabbitMQ; the job simply stays queued."""
    app = create_app(
        Settings(auth_mode="oidc", max_upload_bytes=4096, broker_url=None),
        database=database,
        source_store=source_store,
    )
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://control", headers=FORWARDED
    ) as http:
        user = UserClient(http, "oidc-owner")
        library = (await user.post("/api/v1/libraries", {"name": "Local"})).json()
        response = await user.upload(str(library["id"]), "notes.md", b"# Notes")

    assert response.status_code == 201
    assert response.json()["status"] == "queued"
