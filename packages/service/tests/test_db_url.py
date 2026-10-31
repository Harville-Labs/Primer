"""Which driver a migration connects with.

Alembic runs synchronously, and the services that serve requests are given
an async URL. SQLAlchemy reads a bare `postgresql://` as psycopg 2, so a
migration run against an unmodified URL fails on a driver that is not
installed and should not be.
"""

from __future__ import annotations

import pytest
from primer_service.db import as_sync_url


@pytest.mark.parametrize(
    ("given", "expected"),
    [
        ("postgresql://u:p@h:5432/d", "postgresql+psycopg://u:p@h:5432/d"),
        # One deployment may give every service the same connection string,
        # and the services that serve requests do want an async driver.
        ("postgresql+asyncpg://u:p@h:5432/d", "postgresql+psycopg://u:p@h:5432/d"),
        # Already named, and left alone: an operator who chose a driver on
        # purpose should keep it.
        ("postgresql+psycopg://u:p@h:5432/d", "postgresql+psycopg://u:p@h:5432/d"),
    ],
)
def test_the_migration_url_names_a_synchronous_driver(given: str, expected: str) -> None:
    assert as_sync_url(given) == expected


def test_a_password_containing_a_driver_name_survives() -> None:
    """Only the scheme is rewritten.

    A password may contain anything, `+asyncpg` included. Rewriting the whole
    string would corrupt it into an authentication failure with nothing on
    the surface to explain why.
    """
    assert (
        as_sync_url("postgresql://u:pa+asyncpgss@h:5432/d")
        == "postgresql+psycopg://u:pa+asyncpgss@h:5432/d"
    )


def test_a_url_that_is_not_postgres_is_left_alone() -> None:
    assert as_sync_url("sqlite:///tmp/x.db") == "sqlite:///tmp/x.db"
