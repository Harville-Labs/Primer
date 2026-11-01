"""Async database access, shared by the services that own a schema.

Control and Chat had a copy of this each, identical but for the docstrings,
and Retrieval had a third copy of the URL rewriting alone. Three copies of
the rule for turning one connection string into the driver a given process
needs is three places for it to drift, and the failure it drifts into -
SQLAlchemy quietly resolving a bare `postgresql://` to a driver that is not
installed - reads as a deployment fault rather than as a bug.

The engine and session factory are held on an injected `Database` rather than
in module globals, so an application, a migration run, and a test can each
own their own connections without fighting over process state.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import Request
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from primer_service.durable import SESSION_STATE


def _with_driver(url: str, driver: str) -> str:
    """Rewrite only the scheme.

    A password may contain anything, a driver name included, so replacing
    across the whole string would corrupt it into an authentication failure
    with nothing on the surface to explain why.
    """
    scheme, separator, rest = url.partition("://")
    if not separator or not scheme.startswith("postgresql"):
        return url
    return f"postgresql+{driver}{separator}{rest}"


def as_async_url(url: str) -> str:
    """Normalize a PostgreSQL URL onto the asyncpg driver."""
    return _with_driver(url, "asyncpg")


#: The driver migrations run under, and the one Retrieval's vector
#: integration speaks - so a deployment needs one Postgres driver installed
#: rather than two.
SYNC_DRIVER = "psycopg"


def as_sync_url(url: str) -> str:
    """Normalize a PostgreSQL URL onto psycopg.

    Alembic runs synchronously, so migrations use psycopg while the
    application serves requests over asyncpg from the same URL.
    """
    return _with_driver(url, SYNC_DRIVER)


class Database:
    """Owns the async engine and hands out transactional sessions."""

    def __init__(self, url: str, *, echo: bool = False) -> None:
        self.engine: AsyncEngine = create_async_engine(as_async_url(url), echo=echo, future=True)
        self._sessions = async_sessionmaker(self.engine, expire_on_commit=False)

    @asynccontextmanager
    async def session(self) -> AsyncGenerator[AsyncSession]:
        """A session whose transaction commits on success and rolls back on error."""
        async with self._sessions() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    async def check(self) -> bool:
        """Readiness probe: can this service actually reach PostgreSQL?"""
        from sqlalchemy import text

        try:
            async with self.engine.connect() as connection:
                await connection.execute(text("SELECT 1"))
        except Exception:  # noqa: BLE001 - any failure to reach PostgreSQL means unready
            return False
        return True

    async def dispose(self) -> None:
        await self.engine.dispose()


async def get_session(request: Request) -> AsyncGenerator[AsyncSession]:
    """FastAPI dependency yielding a per-request transactional session."""
    database: Database = request.app.state.database
    async with database.session() as session:
        # Left where `DurableRoute` can find it, so the write is
        # committed before the response is sent rather than in this
        # dependency's teardown - which FastAPI runs afterwards, so a
        # client that read its own write back could miss it.
        setattr(request.state, SESSION_STATE, session)
        yield session
