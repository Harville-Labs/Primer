"""Record whether a job's message was actually sent to the workers.

Revision ID: 0007_job_dispatch
Revises: 0006_studio
Create Date: 2026-11-05
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0007_job_dispatch"
down_revision: str | None = "0006_studio"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "control"


def upgrade() -> None:
    # Left null on existing rows rather than backfilled. A job already
    # waiting in `queued` or `deleting` may be one whose publish failed
    # before this column existed - the case it was added for - and null is
    # what gets it sent. One that was in fact sent gets a second message,
    # which the claim refuses as already taken or already done.
    op.add_column(
        "ingestion_jobs",
        sa.Column("dispatched_at", sa.DateTime(timezone=True), nullable=True),
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.drop_column("ingestion_jobs", "dispatched_at", schema=SCHEMA)
