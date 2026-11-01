"""Alembic environment for the Lab schema.

Runs synchronously, and against its own schema only: Lab shares a
PostgreSQL instance with Control and Chat but not a migration history, so
its migration run touches none of their tables, nor theirs Lab's.
"""

from __future__ import annotations

import os

from alembic import context
from primer_lab.tables import LAB_SCHEMA, Base
from primer_service.db import as_sync_url
from sqlalchemy import engine_from_config, pool, text

config = context.config
target_metadata = Base.metadata


def resolve_database_url() -> str:
    """Where migrations run; never from a file in the repository."""
    configured = config.get_main_option("sqlalchemy.url", None)
    if configured:
        return configured
    from_env = os.environ.get("PRIMER_DATABASE_URL")
    if not from_env:
        raise RuntimeError("Set PRIMER_DATABASE_URL to run migrations from the alembic CLI")
    return as_sync_url(from_env)


DATABASE_URL = resolve_database_url()
config.set_main_option("sqlalchemy.url", DATABASE_URL)


def include_object(obj, name, type_, reflected, compare_to) -> bool:
    """Ignore other services' tables sharing this database."""
    if type_ == "table":
        return obj.schema == LAB_SCHEMA
    return True


def run_migrations_offline() -> None:
    context.configure(
        url=DATABASE_URL,
        target_metadata=target_metadata,
        literal_binds=True,
        include_schemas=True,
        include_object=include_object,
        version_table_schema=LAB_SCHEMA,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    section = dict(config.get_section(config.config_ini_section, {}))
    section["sqlalchemy.url"] = DATABASE_URL
    connectable = engine_from_config(section, prefix="sqlalchemy.", poolclass=pool.NullPool)
    with connectable.connect() as connection:
        connection.execute(text(f"CREATE SCHEMA IF NOT EXISTS {LAB_SCHEMA}"))
        connection.commit()
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            include_schemas=True,
            include_object=include_object,
            version_table_schema=LAB_SCHEMA,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
