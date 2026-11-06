"""Alembic environment for the Control schema; see primer_service.migrations."""

from __future__ import annotations

from primer_control.models import CONTROL_SCHEMA, Base
from primer_service.migrations import run_migrations

run_migrations(CONTROL_SCHEMA, Base.metadata)
