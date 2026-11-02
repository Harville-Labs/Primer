"""Let a provider declare that it makes images, and whether it serves chat.

Revision ID: 0010_provider_images
Revises: 0009_provider_logprobs
Create Date: 2026-11-01
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0010_provider_images"
down_revision: str | None = "0009_provider_logprobs"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "chat"


def upgrade() -> None:
    """Two columns on the existing list, rather than a second list.

    Every existing row serves chat, which is all a provider could do before,
    and makes no images, which nobody has declared. The declaration is one
    JSON document because it is read and replaced whole, and its shape is
    the contract's to check, not the database's.
    """
    op.add_column(
        "providers",
        sa.Column("serves_chat", sa.Boolean(), nullable=False, server_default="true"),
        schema=SCHEMA,
    )
    op.add_column(
        "providers",
        sa.Column("images", postgresql.JSONB(), nullable=True),
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.drop_column("providers", "images", schema=SCHEMA)
    op.drop_column("providers", "serves_chat", schema=SCHEMA)
