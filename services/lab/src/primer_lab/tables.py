"""Lab's tables: the experiments a user chose to save.

Named for tables rather than models, the name the other services use,
because in Lab a model is the thing it loads. Lab keeps its tables in their
own schema and migration history, as Chat and Control do, so its Alembic run
touches nothing of theirs.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import CheckConstraint, DateTime, MetaData, String, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

LAB_SCHEMA = "lab"

NAMING_CONVENTION = {
    "ix": "ix_%(table_name)s_%(column_0_name)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}

KINDS = ("neurons", "anomalies")


class Base(DeclarativeBase):
    metadata = MetaData(schema=LAB_SCHEMA, naming_convention=NAMING_CONVENTION)


class ExperimentRow(Base):
    """One saved experiment: what was run, on which model, and what came of it.

    The run, the model, and the saved vectors and probes are JSON rather than
    columns. Nothing queries inside them; they are written whole and read
    whole, and their shape is the wire contract's, which a column per field
    would have to follow by migration every time it grew.
    """

    __tablename__ = "experiments"
    __table_args__ = (CheckConstraint(f"kind IN {KINDS!r}", name="kind"),)

    id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    owner_user_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), index=True)
    kind: Mapped[str] = mapped_column(String(16))
    title: Mapped[str] = mapped_column(String(200))
    model: Mapped[dict[str, Any]] = mapped_column(JSONB)
    run: Mapped[dict[str, Any]] = mapped_column(JSONB)
    vectors: Mapped[list[Any]] = mapped_column(JSONB)
    probes: Mapped[list[Any]] = mapped_column(JSONB)
    #: Indexed because retention deletes by it.
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )
