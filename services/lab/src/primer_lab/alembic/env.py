"""Alembic environment for the Lab schema; see primer_service.migrations."""

from __future__ import annotations

from primer_lab.tables import LAB_SCHEMA, Base
from primer_service.migrations import run_migrations

run_migrations(LAB_SCHEMA, Base.metadata)
