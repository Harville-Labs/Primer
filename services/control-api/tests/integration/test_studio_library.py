"""Adding a Studio image to a library, against a real PostgreSQL.

The image becomes a document like any upload, so what matters here is the
two gates it passes through - the image must be the caller's and the library
theirs to change - and that the copy leaves with its recipe written into it.
"""

from __future__ import annotations

import io
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any
from uuid import UUID

import pytest
import pytest_asyncio
from control_support import UserClient
from httpx2 import ASGITransport, AsyncClient, Response
from PIL import Image
from primer_contracts.ingestion import StageName
from primer_contracts.studio import PNG_MODEL_KEYWORD, PNG_PROMPT_KEYWORD
from primer_control.app import create_app
from primer_control.config import Settings
from primer_control.models import StudioImage
from primer_service.db import Database
from primer_storage import SourceStore, StudioStore
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncEngine
from studio_support import picture

BROKER = "amqp://broker.invalid"


class RecordingPublisher:
    def __init__(self) -> None:
        self.published: list[tuple[StageName, Any]] = []

    def publish(self, stage: StageName, job_id: Any) -> None:
        self.published.append((stage, job_id))


@pytest.fixture
def publisher() -> RecordingPublisher:
    return RecordingPublisher()


@pytest_asyncio.fixture
async def make_client(
    database: Database,
    clean_tables: AsyncEngine,
    source_store: SourceStore,
    publisher: RecordingPublisher,
    tmp_path: Path,
) -> AsyncIterator[Any]:
    clients: list[AsyncClient] = []

    def build(*, vision: bool = True, broker_url: str | None = BROKER) -> AsyncClient:
        settings = Settings(
            auth_mode="oidc", studio_enabled=True, vision_enabled=vision, broker_url=broker_url
        )
        app = create_app(
            settings,
            database=database,
            source_store=source_store,
            studio_store=StudioStore(f"file://{tmp_path / 'studio'}"),
            publisher=publisher,
        )
        http = AsyncClient(transport=ASGITransport(app=app), base_url="http://control")
        clients.append(http)
        return http

    yield build
    for http in clients:
        await http.aclose()


@pytest_asyncio.fixture
async def client(make_client: Any) -> AsyncClient:
    http: AsyncClient = make_client()
    return http


@pytest.fixture
def owner(client: AsyncClient) -> UserClient:
    return UserClient(client, "oidc-owner")


@pytest.fixture
def stranger(client: AsyncClient) -> UserClient:
    return UserClient(client, "oidc-stranger")


async def studio_image(client: AsyncClient, subject: str) -> str:
    response = await client.post(
        "/api/v1/studio/images",
        files={"file": ("photo.png", picture(), "application/octet-stream")},
        headers={"X-Forwarded-User": subject},
    )
    assert response.status_code == 201
    return str(response.json()["id"])


async def add(user: UserClient, image_id: str, library_id: str) -> Response:
    return await user.post(f"/api/v1/studio/images/{image_id}/library", {"library_id": library_id})


async def test_an_added_image_is_a_png_document_queued_for_parsing(
    owner: UserClient, client: AsyncClient, library_id: str, publisher: RecordingPublisher
) -> None:
    image_id = await studio_image(client, "oidc-owner")

    response = await add(owner, image_id, library_id)

    assert response.status_code == 201
    document = response.json()
    assert document["media_type"] == "image/png"
    assert document["filename"] == f"primer-{image_id}.png"
    assert [stage for stage, _ in publisher.published] == [StageName.PARSE]


async def test_the_copy_carries_the_prompt_and_model_that_made_it(
    owner: UserClient, client: AsyncClient, library_id: str, clean_tables: AsyncEngine
) -> None:
    """The library keeps no link back, so the recipe has to travel in the file."""
    image_id = await studio_image(client, "oidc-owner")
    async with clean_tables.begin() as connection:
        await connection.execute(
            update(StudioImage)
            .where(StudioImage.id == UUID(image_id))
            .values(prompt="A heron in fog", model="flux")
        )

    document = (await add(owner, image_id, library_id)).json()
    content = await owner.get(f"/api/v1/libraries/{library_id}/documents/{document['id']}/content")

    with Image.open(io.BytesIO(content.content)) as stored:
        text = dict(getattr(stored, "text", {}))
    assert text == {PNG_PROMPT_KEYWORD: "A heron in fog", PNG_MODEL_KEYWORD: "flux"}


async def test_a_library_the_caller_cannot_change_refuses_the_image(
    owner: UserClient,
    stranger: UserClient,
    client: AsyncClient,
    library_id: str,
    publisher: RecordingPublisher,
) -> None:
    image_id = await studio_image(client, "oidc-stranger")

    response = await add(stranger, image_id, library_id)

    assert response.status_code == 404
    assert publisher.published == []


async def test_someone_elses_image_cannot_be_added(
    owner: UserClient, stranger: UserClient, client: AsyncClient
) -> None:
    image_id = await studio_image(client, "oidc-owner")
    theirs = (await stranger.post("/api/v1/libraries", {"name": "Mine"})).json()["id"]

    response = await add(stranger, image_id, theirs)

    assert response.status_code == 404
    assert response.json()["title"] == "Image not found"


async def test_without_a_vision_model_there_is_no_such_route(
    make_client: Any, library_id: str, owner: UserClient, client: AsyncClient
) -> None:
    image_id = await studio_image(client, "oidc-owner")
    blind = UserClient(make_client(vision=False), "oidc-owner")

    response = await add(blind, image_id, library_id)

    assert response.status_code == 404
    assert response.json()["title"] == "Images cannot be added to libraries"


async def test_an_ordinary_upload_still_refuses_a_png(owner: UserClient, library_id: str) -> None:
    """Only Studio may put a picture in a library; the upload route is unchanged."""
    response = await owner.upload(library_id, "photo.png", picture())

    assert response.status_code == 415


@pytest.mark.parametrize(
    ("vision", "broker_url", "offered"),
    [(True, BROKER, True), (False, BROKER, False), (True, None, False)],
)
async def test_the_action_is_offered_only_where_something_will_read_the_image(
    make_client: Any, vision: bool, broker_url: str | None, offered: bool
) -> None:
    """No vision model, or no queue to reach ingestion, and the image would never be read."""
    user = UserClient(make_client(vision=vision, broker_url=broker_url), "oidc-owner")

    response = await user.get("/api/v1/capabilities")

    assert response.json()["studio_library_available"] is offered
