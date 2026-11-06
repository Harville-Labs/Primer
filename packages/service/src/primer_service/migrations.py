"""The Alembic environment every service that owns a schema runs.

Control, Chat and Lab each had a copy of this, identical but for the schema
name. Each service's `env.py` now names its schema and metadata and calls
`run_migrations`.

Migrations run as the database's owner. A service at runtime need not: when
the deployment names a runtime role, the migration run creates it and grants
it the rows of this one schema and nothing else, so a compromised Chat can
read conversations but not Control's libraries, and no service can alter a
table. The grants are made again on every run rather than through default
privileges, because a run is what adds tables and it is then already there to
grant them.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from alembic import context
from psycopg import sql
from sqlalchemy import Connection, MetaData, engine_from_config, pool, text

from primer_service.db import as_sync_url

DATABASE_URL_ENV = "PRIMER_DATABASE_URL"
RUNTIME_ROLE_ENV = "PRIMER_DATABASE_RUNTIME_ROLE"
RUNTIME_PASSWORD_ENV = "PRIMER_DATABASE_RUNTIME_PASSWORD"  # noqa: S105 - a variable name


@dataclass(frozen=True)
class RuntimeRole:
    """The login a service uses at runtime, confined to its own schema."""

    name: str
    password: str

    def __repr__(self) -> str:
        return f"RuntimeRole(name={self.name!r})"


def runtime_role_from_env() -> RuntimeRole | None:
    """The runtime role a deployment named, if it named one.

    Both variables or neither: a role with no password could not log in, and
    a password with no role would be silently ignored, so either half alone
    stops the run and says which is missing.
    """
    name = os.environ.get(RUNTIME_ROLE_ENV, "")
    password = os.environ.get(RUNTIME_PASSWORD_ENV, "")
    if not name and not password:
        return None
    if not name or not password:
        missing = RUNTIME_ROLE_ENV if not name else RUNTIME_PASSWORD_ENV
        raise RuntimeError(
            f"{missing} is unset. Set both {RUNTIME_ROLE_ENV} and {RUNTIME_PASSWORD_ENV} "
            "for services to connect as their own role, or neither to keep one role."
        )
    return RuntimeRole(name=name, password=password)


def grant_runtime_role(connection: Connection, schema: str, role: RuntimeRole) -> None:
    """Create `role` if it is missing, and give it the rows of `schema` alone.

    The password is set on every run, so rotating it is a matter of changing
    the Secret and upgrading. Quoted by psycopg rather than formatted in,
    since a password may hold anything a statement would need escaped.
    """
    ident = sql.Identifier(role.name)
    target = sql.Identifier(schema)
    exists = connection.execute(
        text("SELECT 1 FROM pg_roles WHERE rolname = :name"), {"name": role.name}
    ).scalar()
    verb = sql.SQL("ALTER ROLE") if exists else sql.SQL("CREATE ROLE")
    database = connection.execute(text("SELECT current_database()")).scalar_one()
    statements = [
        sql.SQL("{} {} WITH LOGIN PASSWORD {}").format(verb, ident, sql.Literal(role.password)),
        sql.SQL("GRANT CONNECT ON DATABASE {} TO {}").format(sql.Identifier(database), ident),
        sql.SQL("GRANT USAGE ON SCHEMA {} TO {}").format(target, ident),
        sql.SQL("GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA {} TO {}").format(
            target, ident
        ),
        sql.SQL("GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA {} TO {}").format(target, ident),
        # Which migrations have run is the migration run's to record. A
        # service that could rewrite it could make the next upgrade skip one.
        sql.SQL("REVOKE INSERT, UPDATE, DELETE ON {}.alembic_version FROM {}").format(
            target, ident
        ),
    ]
    raw = connection.connection.driver_connection
    if raw is None:
        raise RuntimeError("The migration connection was closed before its role could be granted")
    with raw.cursor() as cursor:
        for statement in statements:
            cursor.execute(statement)


def _database_url() -> str:
    """Where migrations run: from the caller, never from a file in the repository.

    A service's `migrations` module sets the URL directly; the alembic CLI,
    which is what the chart and Compose run, reads PRIMER_DATABASE_URL.
    """
    configured = context.config.get_main_option("sqlalchemy.url", None)
    if configured:
        return configured
    from_env = os.environ.get(DATABASE_URL_ENV)
    if not from_env:
        raise RuntimeError(f"Set {DATABASE_URL_ENV} to run migrations from the alembic CLI")
    return as_sync_url(from_env)


def run_migrations(schema: str, metadata: MetaData) -> None:
    """Migrate `schema`, and only it, then grant the runtime role if one is named.

    Synchronous: migrations are a one-shot operational step, so the async
    driver the application uses buys nothing here and complicates failure
    modes. The schemas share a database but not a migration history, so
    autogenerate ignores every other service's tables.
    """
    url = _database_url()
    config = context.config
    config.set_main_option("sqlalchemy.url", url)

    def include_object(obj, name, type_, reflected, compare_to) -> bool:
        if type_ == "table":
            return obj.schema == schema
        return True

    if context.is_offline_mode():
        context.configure(
            url=url,
            literal_binds=True,
            dialect_opts={"paramstyle": "named"},
            target_metadata=metadata,
            include_schemas=True,
            include_object=include_object,
            version_table_schema=schema,
        )
        with context.begin_transaction():
            context.run_migrations()
        return

    role = runtime_role_from_env()
    section = dict(config.get_section(config.config_ini_section, {}))
    section["sqlalchemy.url"] = url
    connectable = engine_from_config(section, prefix="sqlalchemy.", poolclass=pool.NullPool)
    with connectable.connect() as connection:
        connection.execute(text(f"CREATE SCHEMA IF NOT EXISTS {schema}"))
        connection.commit()
        context.configure(
            connection=connection,
            target_metadata=metadata,
            include_schemas=True,
            include_object=include_object,
            version_table_schema=schema,
        )
        with context.begin_transaction():
            context.run_migrations()
        if role is not None:
            grant_runtime_role(connection, schema, role)
            connection.commit()
