"""Keep Studio images and the generations that made them.

Revision ID: 0006_studio
Revises: 0005_library_grants
Create Date: 2026-11-01
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0006_studio"
down_revision: str | None = "0005_library_grants"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "control"


def upgrade() -> None:
    op.create_table(
        "studio_generations",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("owner_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.ForeignKeyConstraint(
            ["owner_user_id"],
            [f"{SCHEMA}.users.id"],
            name="fk_studio_generations_owner_user_id_users",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_studio_generations"),
        schema=SCHEMA,
    )
    # The hourly limit's question: how many has this person started lately.
    op.create_index(
        "ix_studio_generations_owner_created",
        "studio_generations",
        ["owner_user_id", "created_at"],
        schema=SCHEMA,
    )
    op.create_table(
        "studio_images",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("owner_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("origin", sa.String(length=16), nullable=False),
        sa.Column("media_type", sa.String(length=64), nullable=False),
        sa.Column("width", sa.Integer(), nullable=False),
        sa.Column("height", sa.Integer(), nullable=False),
        sa.Column("byte_size", sa.BigInteger(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("parent_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("generation_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("provider_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("model", sa.String(length=200), nullable=True),
        sa.Column("operation", sa.String(length=16), nullable=True),
        sa.Column("prompt", sa.String(length=4000), nullable=True),
        sa.Column("negative_prompt", sa.String(length=4000), nullable=True),
        sa.Column("size", sa.String(length=16), nullable=True),
        sa.Column("seed", sa.BigInteger(), nullable=True),
        sa.Column("steps", sa.Integer(), nullable=True),
        sa.CheckConstraint(
            "origin IN ('upload', 'generated')", name="ck_studio_images_origin_known"
        ),
        sa.ForeignKeyConstraint(
            ["owner_user_id"],
            [f"{SCHEMA}.users.id"],
            name="fk_studio_images_owner_user_id_users",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["parent_id"],
            [f"{SCHEMA}.studio_images.id"],
            name="fk_studio_images_parent_id_studio_images",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["generation_id"],
            [f"{SCHEMA}.studio_generations.id"],
            name="fk_studio_images_generation_id_studio_generations",
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_studio_images"),
        schema=SCHEMA,
    )
    # The gallery, newest first, and every quota sum.
    op.create_index(
        "ix_studio_images_owner_created",
        "studio_images",
        ["owner_user_id", "created_at"],
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.drop_index("ix_studio_images_owner_created", table_name="studio_images", schema=SCHEMA)
    op.drop_table("studio_images", schema=SCHEMA)
    op.drop_index(
        "ix_studio_generations_owner_created", table_name="studio_generations", schema=SCHEMA
    )
    op.drop_table("studio_generations", schema=SCHEMA)
