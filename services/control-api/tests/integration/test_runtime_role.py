"""The role Control runs as reaches Control's rows and nothing else.

Control, Chat and Lab share one PostgreSQL. With one login between them, a
flaw in any service that ran arbitrary SQL could read every library and
every conversation. These run the real migrations against a real server and
then connect as the role they made.
"""

from __future__ import annotations

import pytest
from primer_control.migrations import upgrade_to_head
from primer_service.db import as_sync_url
from primer_service.migrations import RUNTIME_PASSWORD_ENV, RUNTIME_ROLE_ENV
from sqlalchemy import create_engine, text
from sqlalchemy.exc import ProgrammingError

ROLE = "primer_control_test"
PASSWORD = 'it\'s a "password"; DROP ROLE x'  # noqa: S105 - a test value, quoting included


def _as_role(url: str, password: str = PASSWORD):
    owner = as_sync_url(url)
    scheme, rest = owner.split("://", 1)
    return create_engine(
        f"{scheme}://{ROLE}@{rest.split('@', 1)[1]}", connect_args={"password": password}
    )


@pytest.fixture
def migrated_with_role(scratch_db_url: str, monkeypatch: pytest.MonkeyPatch) -> str:
    owner = create_engine(as_sync_url(scratch_db_url))
    with owner.begin() as connection:
        # Another service's schema, made the way its own migrations would.
        connection.execute(text("CREATE SCHEMA chat"))
        connection.execute(text("CREATE TABLE chat.messages (body text)"))
        connection.execute(text("INSERT INTO chat.messages VALUES ('private')"))
    owner.dispose()
    monkeypatch.setenv(RUNTIME_ROLE_ENV, ROLE)
    monkeypatch.setenv(RUNTIME_PASSWORD_ENV, PASSWORD)
    upgrade_to_head(scratch_db_url)
    return scratch_db_url


def test_runtime_role_reads_and_writes_its_own_schema(migrated_with_role: str) -> None:
    engine = _as_role(migrated_with_role)
    with engine.begin() as connection:
        connection.execute(
            text("INSERT INTO control.users (id, subject) VALUES (gen_random_uuid(), 's')")
        )
        assert connection.execute(text("SELECT count(*) FROM control.users")).scalar_one() == 1
    engine.dispose()


@pytest.mark.parametrize(
    "statement",
    [
        "SELECT * FROM chat.messages",
        "CREATE TABLE control.extra (id int)",
        "DROP TABLE control.users",
        "UPDATE control.alembic_version SET version_num = 'x'",
    ],
)
def test_runtime_role_is_refused_everything_else(migrated_with_role: str, statement: str) -> None:
    """Another schema's rows, DDL, and the migration record are all out of reach.

    The last guards the record of which migrations ran: a service that could
    rewrite it could make the next upgrade skip one.
    """
    engine = _as_role(migrated_with_role)
    with (
        engine.connect() as connection,
        pytest.raises(ProgrammingError, match=r"permission denied|must be owner"),
    ):
        connection.execute(text(statement))
    engine.dispose()


def test_rerunning_migrations_sets_the_password_again(
    migrated_with_role: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Rotating the password is changing the Secret and upgrading."""
    monkeypatch.setenv(RUNTIME_PASSWORD_ENV, "rotated")
    upgrade_to_head(migrated_with_role)
    engine = _as_role(migrated_with_role, "rotated")
    with engine.connect() as connection:
        assert connection.execute(text("SELECT current_user")).scalar_one() == ROLE
    engine.dispose()
