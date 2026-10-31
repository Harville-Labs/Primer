"""Integration fixtures backed by a real PostgreSQL.

Persistence and authorization bugs hide behind mocks, so these tests run
against the same image the deployment uses. The container is session-scoped
because starting PostgreSQL costs far more than isolating a test inside it.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from pathlib import Path

import pytest
import pytest_asyncio
from control_support import ServiceClient, UserClient
from httpx2 import ASGITransport, AsyncClient
from primer_control.app import create_app
from primer_control.config import Settings
from primer_control.migrations import upgrade_to_head
from primer_service.db import Database, as_sync_url
from primer_storage import SourceStore
from sqlalchemy.ext.asyncio import AsyncEngine
from testcontainers.community.postgres import PostgresContainer

POSTGRES_IMAGE = "postgres:17"


@pytest.fixture(scope="session")
def postgres_url() -> Iterator[str]:
    with PostgresContainer(POSTGRES_IMAGE, driver="asyncpg") as container:
        yield container.get_connection_url()


@pytest.fixture(scope="session")
def migrated_url(postgres_url: str) -> str:
    """A database with the Control schema at head.

    Migrations run once here, never from application startup, matching how
    Compose and Helm apply them as an explicit step.
    """
    upgrade_to_head(postgres_url)
    return postgres_url


@pytest.fixture
def scratch_db_url(postgres_url: str) -> Iterator[str]:
    """A throwaway database in the same container, for destructive tests."""
    from sqlalchemy import create_engine, text

    admin = create_engine(as_sync_url(postgres_url), isolation_level="AUTOCOMMIT")
    name = "primer_scratch"
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
    """Truncate between tests so each one starts from a known empty schema."""
    from sqlalchemy import text

    async with database.engine.begin() as connection:
        await connection.execute(
            text(
                "TRUNCATE control.libraries, control.users, control.source_objects "
                "RESTART IDENTITY CASCADE"
            )
        )
    yield database.engine


#: Small enough that a test can exceed it with a readable literal.
MAX_UPLOAD_BYTES = 4096

SERVICE_TOKEN = "test-service-token"  # noqa: S105 - a fixture value, not a real credential

#: Two, so the retry budget can be exhausted in a readable number of steps.
MAX_JOB_ATTEMPTS = 2


@pytest.fixture
def source_root(tmp_path: Path) -> Path:
    """A per-test source store, so stored objects can be counted directly."""
    return tmp_path / "sources"


@pytest.fixture
def source_store(source_root: Path) -> SourceStore:
    return SourceStore(f"file://{source_root}", max_bytes=MAX_UPLOAD_BYTES)


@pytest_asyncio.fixture
async def client(
    database: Database, clean_tables: AsyncEngine, source_store: SourceStore
) -> AsyncIterator[AsyncClient]:
    """An OIDC-mode client; each request names its user through edge headers."""
    app = create_app(
        Settings(
            auth_mode="oidc",
            max_upload_bytes=MAX_UPLOAD_BYTES,
            internal_api_token=SERVICE_TOKEN,
            max_job_attempts=MAX_JOB_ATTEMPTS,
        ),
        database=database,
        source_store=source_store,
    )
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://control") as http:
        yield http


@pytest.fixture
def owner(client: AsyncClient) -> UserClient:
    # With an address, because the proxy sends one and sharing needs it to
    # name people by. A fixture without one would make every sharing test
    # set up its own identity.
    return UserClient(client, "oidc-owner", email="owner@example.edu")


@pytest.fixture
def stranger(client: AsyncClient) -> UserClient:
    return UserClient(client, "oidc-stranger")


@pytest.fixture
def colleague(client: AsyncClient) -> UserClient:
    """Someone a library can be shared with, with an address to name them by."""
    return UserClient(client, "oidc-colleague", email="colleague@example.edu")


@pytest_asyncio.fixture
async def library_id(owner: UserClient) -> str:
    """A library the owner can manage, for document tests to fill."""
    response = await owner.post("/api/v1/libraries", {"name": "Sources"})
    assert response.status_code == 201
    return str(response.json()["id"])


@pytest.fixture
def service(client: AsyncClient) -> ServiceClient:
    """A worker, presenting the cluster-internal service credential."""
    return ServiceClient(client, SERVICE_TOKEN)
