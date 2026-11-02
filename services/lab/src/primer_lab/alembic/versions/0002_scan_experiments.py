"""Let a saved experiment be a scan of a library's passages.

Revision ID: 0002_scan_experiments
Revises: 0001_experiments
Create Date: 2026-11-01
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0002_scan_experiments"
down_revision: str | None = "0001_experiments"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "lab"


def upgrade() -> None:
    op.drop_constraint(op.f("ck_experiments_kind"), "experiments", schema=SCHEMA)
    op.create_check_constraint(
        op.f("ck_experiments_kind"),
        "experiments",
        "kind IN ('neurons', 'anomalies', 'scan')",
        schema=SCHEMA,
    )


def downgrade() -> None:
    # The older Lab cannot open a scan, and the narrower constraint would
    # refuse to be added while one exists, so they go with the kind.
    op.execute("DELETE FROM lab.experiments WHERE kind = 'scan'")
    op.drop_constraint(op.f("ck_experiments_kind"), "experiments", schema=SCHEMA)
    op.create_check_constraint(
        op.f("ck_experiments_kind"),
        "experiments",
        "kind IN ('neurons', 'anomalies')",
        schema=SCHEMA,
    )
