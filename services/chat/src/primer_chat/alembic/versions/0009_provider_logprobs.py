"""Let an administrator declare which providers return logprobs.

Revision ID: 0009_provider_logprobs
Revises: 0008_message_provider
Create Date: 2026-10-31
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0009_provider_logprobs"
down_revision: str | None = "0008_message_provider"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "chat"


def upgrade() -> None:
    """Two declarations per provider, both off for every existing row.

    Off rather than guessed. A provider added before this existed was never
    described as returning logprobs, and scoring against one that silently
    drops them would show a flat line where the anomalies should be.
    """
    for column in ("supports_logprobs", "supports_prompt_logprobs"):
        op.add_column(
            "providers",
            sa.Column(column, sa.Boolean(), nullable=False, server_default="false"),
            schema=SCHEMA,
        )


def downgrade() -> None:
    for column in ("supports_prompt_logprobs", "supports_logprobs"):
        op.drop_column("providers", column, schema=SCHEMA)
