"""Studio against a real PostgreSQL and a Chat faked over real sockets.

Studio images are private to one person, administrators included, so most
of these tests are about who cannot see something. The fake Chat records
what it was asked, so a refusal can be shown to have happened before Chat
was called at all.
"""

from __future__ import annotations

import base64
import io
from collections.abc import AsyncIterator, Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import quote
from uuid import UUID

import pytest
import pytest_asyncio
from control_credentials import FORWARDED
from httpx2 import ASGITransport, AsyncClient, Response
from PIL import Image
from primer_control.app import create_app
from primer_control.config import Settings
from primer_control.models import StudioGeneration, StudioImage
from primer_control.studio.retention import purge
from primer_service.db import Database
from primer_storage import SourceStore, StudioStore
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncEngine
from studio_support import FakeChat, Running, events_of, picture

PROVIDER = "6c1f7b0e-4b7a-4a51-9a3e-1b2c3d4e5f60"
ADMINS = "primer-admins"


class Person:
    """One subject at the edge, with the Studio calls a test makes."""

    def __init__(self, http: AsyncClient, subject: str, *, groups: str | None = None) -> None:
        self.http = http
        self.headers = {"X-Forwarded-User": subject, "X-Forwarded-Email": f"{subject}@x.edu"}
        if groups:
            self.headers["X-Forwarded-Groups"] = groups

    async def upload(self, data: bytes, name: str = "photo.jpg") -> Response:
        return await self.http.post(
            "/api/v1/studio/images",
            files={"file": (name, data, "application/octet-stream")},
            headers=self.headers,
        )

    async def get(self, path: str) -> Response:
        return await self.http.get(path, headers=self.headers)

    async def delete(self, path: str) -> Response:
        return await self.http.delete(path, headers=self.headers)

    async def generate(self, **fields: Any) -> Response:
        body = {
            "provider_id": PROVIDER,
            "model": "flux",
            "operation": "generate",
            "prompt": "A heron in fog",
            "size": "1024x1024",
            **fields,
        }
        return await self.http.post("/api/v1/studio/generations", json=body, headers=self.headers)


@pytest.fixture
def chat() -> Iterator[Running]:
    running = Running()
    yield running
    running.stop()


@pytest.fixture
def fake(chat: Running) -> FakeChat:
    return chat.fake


@pytest.fixture
def studio_store(tmp_path: Path) -> StudioStore:
    return StudioStore(f"file://{tmp_path / 'sources'}")


def studio_settings(chat_url: str | None, **overrides: Any) -> Settings:
    fields: dict[str, Any] = {
        "auth_mode": "oidc",
        "admin_group": ADMINS,
        "chat_service_url": chat_url,
        "studio_enabled": True,
        **overrides,
    }
    return Settings.model_validate(fields)


@pytest_asyncio.fixture
async def make_client(
    database: Database,
    clean_tables: AsyncEngine,
    source_store: SourceStore,
    studio_store: StudioStore,
    chat: Running,
) -> AsyncIterator[Any]:
    clients: list[AsyncClient] = []

    def build(**overrides: Any) -> AsyncClient:
        app = create_app(
            studio_settings(chat.url, **overrides),
            database=database,
            source_store=source_store,
            studio_store=studio_store,
        )
        http = AsyncClient(
            transport=ASGITransport(app=app), base_url="http://control", headers=FORWARDED
        )
        clients.append(http)
        return http

    yield build
    for http in clients:
        await http.aclose()


@pytest.fixture
def http(make_client: Any) -> AsyncClient:
    return make_client()


@pytest.fixture
def owner(http: AsyncClient) -> Person:
    return Person(http, "studio-owner")


@pytest.fixture
def stranger(http: AsyncClient) -> Person:
    return Person(http, "studio-stranger")


@pytest.fixture
def admin(http: AsyncClient) -> Person:
    return Person(http, "studio-admin", groups=ADMINS)


async def uploaded(person: Person, data: bytes | None = None) -> dict[str, Any]:
    response = await person.upload(data or picture("JPEG"))
    assert response.status_code == 201, response.text
    return response.json()


async def row_count(database: Database, model: type[StudioImage | StudioGeneration]) -> int:
    async with database.session() as session:
        return int(await session.scalar(select(func.count()).select_from(model)) or 0)


class TestUploads:
    async def test_gps_exif_is_stripped_and_the_image_stored_as_png(self, owner: Person) -> None:
        """A phone photo carries where it was taken; the stored copy must not."""
        original = picture("JPEG", gps=True)
        with Image.open(io.BytesIO(original)) as source:
            assert source.getexif().get_ifd(0x8825), "the fixture must carry GPS to test this"

        image = await uploaded(owner, original)
        content = await owner.get(f"/api/v1/studio/images/{image['id']}/content")

        assert content.status_code == 200
        assert content.headers["content-type"] == "image/png"
        with Image.open(io.BytesIO(content.content)) as stored:
            assert stored.format == "PNG"
            assert not stored.getexif()
            assert "exif" not in stored.info
            assert stored.size == (8, 6)
        assert (image["width"], image["height"], image["origin"]) == (8, 6, "upload")
        assert image["byte_size"] == len(content.content)

    async def test_a_file_that_is_not_an_image_is_refused(self, owner: Person) -> None:
        response = await owner.upload(b"%PDF-1.7 not a picture", "paper.pdf")

        assert response.status_code == 415

    async def test_an_image_wider_than_the_limit_is_refused_before_decoding(
        self, make_client: Any
    ) -> None:
        person = Person(make_client(studio_max_side=64), "studio-owner")

        response = await person.upload(picture("PNG", (65, 10)))

        assert response.status_code == 415
        assert "64 pixels" in response.json()["detail"]

    async def test_an_upload_over_the_byte_limit_is_refused(self, make_client: Any) -> None:
        person = Person(make_client(studio_max_upload_bytes=100), "studio-owner")

        response = await person.upload(picture("PNG", (64, 64)) + b"\0" * 200)

        assert response.status_code == 413

    async def test_studio_switched_off_has_no_routes(self, make_client: Any) -> None:
        person = Person(make_client(studio_enabled=False), "studio-owner")

        assert (await person.upload(picture())).status_code == 404
        assert (await person.get("/api/v1/studio/images")).status_code == 404


class TestPrivacy:
    async def test_nobody_else_can_list_read_download_or_delete_an_image(
        self, owner: Person, stranger: Person, admin: Person
    ) -> None:
        """Administrators included: they see usage, never pictures."""
        image = await uploaded(owner)
        path = f"/api/v1/studio/images/{image['id']}"

        for other in (stranger, admin):
            assert (await other.get("/api/v1/studio/images")).json() == []
            assert (await other.get(path)).status_code == 404
            assert (await other.get(f"{path}/content")).status_code == 404
            assert (await other.delete(path)).status_code == 404
        assert (await owner.get(path)).status_code == 200

    async def test_an_administrator_sees_counts_and_sizes_only(
        self, owner: Person, admin: Person, stranger: Person
    ) -> None:
        image = await uploaded(owner)

        usage = await admin.get("/api/v1/admin/studio/usage")

        assert usage.status_code == 200
        [row] = usage.json()
        assert row == {
            "user_id": row["user_id"],
            "email": "studio-owner@x.edu",
            "image_count": 1,
            "byte_size": image["byte_size"],
        }
        assert (await stranger.get("/api/v1/admin/studio/usage")).status_code == 403

    async def test_deleting_removes_the_row_and_the_bytes(
        self, owner: Person, studio_store: StudioStore
    ) -> None:
        image = await uploaded(owner)
        image_id = UUID(image["id"])
        assert await studio_store.exists(image_id)

        response = await owner.delete(f"/api/v1/studio/images/{image_id}")

        assert response.status_code == 204
        assert not await studio_store.exists(image_id)
        assert (await owner.get(f"/api/v1/studio/images/{image_id}")).status_code == 404

    async def test_listing_pages_back_by_time(self, owner: Person) -> None:
        first = await uploaded(owner)
        second = await uploaded(owner)

        page = (await owner.get("/api/v1/studio/images?limit=1")).json()
        before = quote(page[0]["created_at"])
        older = (await owner.get(f"/api/v1/studio/images?limit=1&before={before}")).json()

        assert [image["id"] for image in page] == [second["id"]]
        assert [image["id"] for image in older] == [first["id"]]


class TestGenerating:
    async def test_results_are_stored_with_their_provenance_and_seed(
        self, owner: Person, fake: FakeChat
    ) -> None:
        response = await owner.generate(count=2, steps=8)

        assert response.status_code == 200, response.text
        events = events_of(response.text)
        assert [event["type"] for event in events] == ["progress", "done"]
        images = events[-1]["images"]
        assert len(images) == 2
        assert images[0]["origin"] == "generated"
        assert images[0]["provenance"] == {
            "provider_id": PROVIDER,
            "model": "flux",
            "operation": "generate",
            "prompt": "A heron in fog",
            "negative_prompt": None,
            "size": "1024x1024",
            "seed": fake.seed,
            "steps": 8,
        }
        listed = (await owner.get("/api/v1/studio/images")).json()
        assert {image["id"] for image in listed} == {image["id"] for image in images}

    async def test_chat_is_asked_as_the_person_not_as_control(
        self, owner: Person, fake: FakeChat
    ) -> None:
        """Chat decides which providers someone may use, so it must know who."""
        await owner.generate()

        [headers] = fake.headers
        assert headers["x-forwarded-user"] == "studio-owner"
        assert headers["x-forwarded-email"] == "studio-owner@x.edu"

    async def test_an_edit_sends_the_source_and_records_it_as_the_parent(
        self, owner: Person, fake: FakeChat
    ) -> None:
        source = await uploaded(owner)
        stored = (await owner.get(f"/api/v1/studio/images/{source['id']}/content")).content

        response = await owner.generate(operation="edit", source_id=source["id"])

        [done] = [event for event in events_of(response.text) if event["type"] == "done"]
        assert done["images"][0]["parent_id"] == source["id"]
        [sent] = fake.requests
        assert base64.b64decode(sent["image"]) == stored
        assert "source_id" not in sent

    async def test_someone_elses_image_cannot_be_edited(
        self, owner: Person, stranger: Person, fake: FakeChat
    ) -> None:
        source = await uploaded(owner)

        response = await stranger.generate(operation="edit", source_id=source["id"])

        assert response.status_code == 404
        assert fake.requests == []

    async def test_a_provider_failure_arrives_as_an_event_and_is_recorded(
        self, owner: Person, fake: FakeChat, database: Database
    ) -> None:
        fake.mode = "failed"

        response = await owner.generate()

        assert events_of(response.text)[-1] == {
            "id": 1,
            "type": "failed",
            "code": "dependency_unavailable",
            "title": "Image provider timed out",
            "detail": "Try again.",
            "status": 504,
        }
        assert await row_count(database, StudioImage) == 0
        async with database.session() as session:
            generation = await session.scalar(select(StudioGeneration))
        assert generation is not None and generation.error_code == "dependency_unavailable"

    async def test_chats_own_refusal_is_passed_on_in_its_words(
        self, owner: Person, fake: FakeChat
    ) -> None:
        fake.mode = "refuse"

        response = await owner.generate()

        failed = events_of(response.text)[-1]
        assert (failed["type"], failed["status"]) == ("failed", 422)
        assert failed["detail"] == "That provider does not inpaint."

    async def test_output_that_is_not_an_image_is_not_stored(
        self, owner: Person, fake: FakeChat, database: Database
    ) -> None:
        fake.output = b"<html>a login page</html>"

        response = await owner.generate()

        failed = events_of(response.text)[-1]
        assert (failed["type"], failed["status"]) == ("failed", 502)
        assert await row_count(database, StudioImage) == 0


class TestLimits:
    async def test_a_full_studio_is_refused_before_chat_is_asked(
        self, make_client: Any, fake: FakeChat
    ) -> None:
        size = (await uploaded(Person(make_client(), "sizer"), picture()))["byte_size"]
        person = Person(make_client(studio_quota_bytes=size), "studio-owner")
        await uploaded(person, picture())

        upload = await person.upload(picture())
        generation = await person.generate()

        assert (upload.status_code, upload.json()["code"]) == (413, "quota_exceeded")
        assert (generation.status_code, generation.json()["code"]) == (413, "quota_exceeded")
        assert fake.requests == []

    async def test_results_that_would_overflow_the_quota_are_not_stored(
        self, make_client: Any, fake: FakeChat, database: Database, studio_store: StudioStore
    ) -> None:
        person = Person(make_client(studio_quota_bytes=120), "studio-owner")

        response = await person.generate(count=2)

        failed = events_of(response.text)[-1]
        assert (failed["type"], failed["code"]) == ("failed", "quota_exceeded")
        assert await row_count(database, StudioImage) == 0
        assert not list((studio_store_root(studio_store)).glob("*.png"))

    async def test_the_hourly_limit_counts_failures_too(
        self, make_client: Any, fake: FakeChat
    ) -> None:
        person = Person(make_client(studio_generations_per_hour=1), "studio-owner")
        fake.mode = "failed"
        await person.generate()

        response = await person.generate()

        assert response.status_code == 429
        assert len(fake.requests) == 1

    async def test_more_images_than_the_deployment_allows_is_refused(
        self, make_client: Any, fake: FakeChat
    ) -> None:
        person = Person(make_client(studio_max_images_per_request=2), "studio-owner")

        response = await person.generate(count=3)

        assert response.status_code == 422
        assert fake.requests == []


class TestRetention:
    async def test_only_images_past_retention_are_deleted(
        self, owner: Person, database: Database, studio_store: StudioStore, chat: Running
    ) -> None:
        old, new = await uploaded(owner), await uploaded(owner)
        async with database.session() as session:
            await session.execute(
                update(StudioImage)
                .where(StudioImage.id == UUID(old["id"]))
                .values(created_at=datetime.now(UTC) - timedelta(days=40))
            )

        deleted = await purge(
            studio_settings(chat.url, studio_retention_days=30),
            database,
            studio_store,
            datetime.now(UTC),
        )

        assert deleted == 1
        assert not await studio_store.exists(UUID(old["id"]))
        assert await studio_store.exists(UUID(new["id"]))
        assert (await owner.get(f"/api/v1/studio/images/{new['id']}")).status_code == 200

    async def test_nothing_is_deleted_when_no_retention_is_set(
        self, owner: Person, database: Database, studio_store: StudioStore, chat: Running
    ) -> None:
        await uploaded(owner)
        far_future = datetime.now(UTC) + timedelta(days=10_000)

        deleted = await purge(studio_settings(chat.url), database, studio_store, far_future)

        assert deleted == 0
        assert await row_count(database, StudioImage) == 1


class TestCapability:
    @pytest.mark.parametrize(
        ("enabled", "with_chat", "available"),
        [(True, True, True), (True, False, False), (False, True, False)],
    )
    async def test_studio_is_offered_only_when_on_and_chat_is_there(
        self, make_client: Any, enabled: bool, with_chat: bool, available: bool
    ) -> None:
        overrides: dict[str, Any] = {"studio_enabled": enabled}
        if not with_chat:
            overrides["chat_service_url"] = None
        person = Person(make_client(**overrides), "studio-owner")

        response = await person.get("/api/v1/capabilities")

        assert response.json()["studio_available"] is available


def studio_store_root(store: StudioStore) -> Path:
    return Path(store._root) / "studio"
