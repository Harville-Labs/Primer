"""Chat against a real PostgreSQL, with Control, Retrieval, and the model faked.

The three are faked deliberately. What needs testing here is the ordering -
authorize, then retrieve, then generate - and the persistence of what came
out. None of that depends on those being real, and all of it is easier to
assert when the fakes can record exactly what they were asked.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator

import pytest
import pytest_asyncio
from chat_support import FakeControl, FakeGenerator, FakeRetrieval
from httpx2 import ASGITransport, AsyncClient
from primer_chat.app import create_app
from primer_chat.config import Settings
from primer_chat.migrations import upgrade_to_head
from primer_service.db import Database
from sqlalchemy.ext.asyncio import AsyncEngine
from testcontainers.community.postgres import PostgresContainer

POSTGRES_IMAGE = "postgres:17"


@pytest.fixture(scope="session")
def postgres_url() -> Iterator[str]:
    with PostgresContainer(POSTGRES_IMAGE, driver="asyncpg") as container:
        yield container.get_connection_url()


@pytest.fixture(scope="session")
def migrated_url(postgres_url: str) -> str:
    upgrade_to_head(postgres_url)
    return postgres_url


@pytest.fixture
def scratch_db_url(postgres_url: str) -> Iterator[str]:
    """A throwaway database in the same container, for destructive tests."""
    from primer_service.db import as_sync_url
    from sqlalchemy import create_engine, text

    admin = create_engine(as_sync_url(postgres_url), isolation_level="AUTOCOMMIT")
    name = "primer_chat_scratch"
    try:
        with admin.connect() as connection:
            connection.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
            connection.execute(text(f'CREATE DATABASE "{name}"'))
        yield postgres_url.rsplit("/", 1)[0] + f"/{name}"
        with admin.connect() as connection:
            connection.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
    finally:
        admin.dispose()


@pytest_asyncio.fixture
async def database(migrated_url: str) -> AsyncIterator[Database]:
    db = Database(migrated_url)
    yield db
    await db.dispose()


@pytest_asyncio.fixture
async def clean_tables(database: Database) -> AsyncIterator[AsyncEngine]:
    """Empty every table in the schema between tests.

    Taken from the metadata rather than listed by hand. A hand-written list
    goes stale the moment a table is added, and the failure it produces is
    the worst kind: rows left behind by one test make another fail somewhere
    unrelated, so the symptom points away from the cause. That is exactly
    what happened when providers arrived.
    """
    from primer_chat.models import Base
    from sqlalchemy import text

    tables = ", ".join(f"chat.{table.name}" for table in Base.metadata.sorted_tables)
    async with database.engine.begin() as connection:
        await connection.execute(text(f"TRUNCATE {tables} RESTART IDENTITY CASCADE"))
    yield database.engine


@pytest.fixture
def control() -> FakeControl:
    return FakeControl()


@pytest.fixture
def retrieval() -> FakeRetrieval:
    return FakeRetrieval()


@pytest.fixture
def generator() -> FakeGenerator:
    return FakeGenerator()


@pytest.fixture
def settings() -> Settings:
    """The deployment under test. Overridden by tests that need a different one.

    An endpoint and a model are named because a real deployment names them,
    and because neither is defaulted any more. Chat used to fall back to a
    hosted model's name, which meant these tests passed while representing a
    deployment that had been configured with nothing - and the routing that
    now refuses to send a question nowhere had no way to tell that apart
    from a genuine misconfiguration.

    The generator is a fake, so nothing is actually sent here; what these
    values do is make the deployment under test a plausible one.
    """
    return Settings(
        auth_mode="oidc",
        chat_base_url="http://model.invalid/v1",
        chat_model="test-model",
    )


@pytest.fixture
def make_client(database: Database, clean_tables: AsyncEngine, settings: Settings):
    """Build a client over chosen fakes, for the cases that need their own.

    Returned as a factory rather than more fixtures because the variations
    are one-offs: a generator that fails partway, a Control that forbids.
    """

    def build(
        control: FakeControl | None = None,
        retrieval: FakeRetrieval | None = None,
        generator: FakeGenerator | None = None,
    ) -> AsyncClient:
        app = create_app(
            settings,
            database=database,
            control=control or FakeControl(),
            retrieval=retrieval or FakeRetrieval(),
            generator=generator or FakeGenerator(),
        )
        return AsyncClient(transport=ASGITransport(app=app), base_url="http://chat")

    return build


@pytest_asyncio.fixture
async def client(
    database: Database,
    clean_tables: AsyncEngine,
    control: FakeControl,
    retrieval: FakeRetrieval,
    generator: FakeGenerator,
    settings: Settings,
) -> AsyncIterator[AsyncClient]:
    app = create_app(
        settings,
        database=database,
        control=control,
        retrieval=retrieval,
        generator=generator,
    )
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://chat") as http:
        yield http
