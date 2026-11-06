"""Alembic environment for the Chat schema; see primer_service.migrations."""

from __future__ import annotations

from primer_chat.models import CHAT_SCHEMA, Base
from primer_service.migrations import run_migrations

run_migrations(CHAT_SCHEMA, Base.metadata)
