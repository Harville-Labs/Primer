"""Lab's schema, and its separation from the other services'."""

from __future__ import annotations

import sqlalchemy as sa
from primer_lab.migrations import check_for_drift, downgrade_to_base, upgrade_to_head
from primer_service.db import as_sync_url
from sqlalchemy import create_engine


def test_the_lab_schema_holds_its_own_tables(migrated_url: str) -> None:
    """Its migration history too, so it never meets Chat's or Control's."""
    engine = create_engine(as_sync_url(migrated_url))
    try:
        assert set(sa.inspect(engine).get_table_names(schema="lab")) == {
            "alembic_version",
            "experiments",
        }
    finally:
        engine.dispose()


def test_lab_does_not_reach_into_another_schema(migrated_url: str) -> None:
    """No foreign keys across the service boundary: an owner is an id, not a join."""
    engine = create_engine(as_sync_url(migrated_url))
    try:
        inspector = sa.inspect(engine)
        tables = inspector.get_table_names(schema="lab")
        assert tables
        for table in tables:
            for key in inspector.get_foreign_keys(table, schema="lab"):
                assert key["referred_schema"] in (None, "lab")
    finally:
        engine.dispose()


def test_migrations_match_the_tables(migrated_url: str) -> None:
    check_for_drift(migrated_url)


def test_upgrade_and_downgrade_round_trip(scratch_db_url: str) -> None:
    """A migration that cannot be rolled back cannot be safely deployed."""
    upgrade_to_head(scratch_db_url)
    downgrade_to_base(scratch_db_url)

    engine = create_engine(as_sync_url(scratch_db_url))
    try:
        assert "experiments" not in sa.inspect(engine).get_table_names(schema="lab")
    finally:
        engine.dispose()
