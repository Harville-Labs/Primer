"""Create the Lab schema with saved experiments.

Revision ID: 0001_experiments
Revises:
Create Date: 2026-11-01
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001_experiments"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "lab"


def upgrade() -> None:
    op.execute(f"CREATE SCHEMA IF NOT EXISTS {SCHEMA}")

    op.create_table(
        "experiments",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("owner_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("model", postgresql.JSONB(), nullable=False),
        sa.Column("run", postgresql.JSONB(), nullable=False),
        sa.Column("vectors", postgresql.JSONB(), nullable=False),
        sa.Column("probes", postgresql.JSONB(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint("kind IN ('neurons', 'anomalies')", name=op.f("ck_experiments_kind")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_experiments")),
        schema=SCHEMA,
    )
    op.create_index(
        op.f("ix_experiments_owner_user_id"), "experiments", ["owner_user_id"], schema=SCHEMA
    )
    op.create_index(op.f("ix_experiments_created_at"), "experiments", ["created_at"], schema=SCHEMA)


def downgrade() -> None:
    op.drop_index(op.f("ix_experiments_created_at"), table_name="experiments", schema=SCHEMA)
    op.drop_index(op.f("ix_experiments_owner_user_id"), table_name="experiments", schema=SCHEMA)
    op.drop_table("experiments", schema=SCHEMA)
