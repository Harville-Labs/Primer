"""Record how retrieval found each cited passage.

Revision ID: 0011_citation_provenance
Revises: 0010_provider_images
Create Date: 2026-11-04
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0011_citation_provenance"
down_revision: str | None = "0010_provider_images"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "chat"


def upgrade() -> None:
    """Nullable, and left null on existing rows.

    Nobody recorded how an older answer's passages were found, and filling
    in "vector" would be wrong for every one the graph contributed.
    """
    op.add_column(
        "message_citations",
        sa.Column("found_by", sa.String(length=8), nullable=True),
        schema=SCHEMA,
    )
    op.add_column(
        "message_citations",
        sa.Column("graph_entities", postgresql.ARRAY(sa.Text()), nullable=True),
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.drop_column("message_citations", "graph_entities", schema=SCHEMA)
    op.drop_column("message_citations", "found_by", schema=SCHEMA)
