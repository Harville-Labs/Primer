"""Saved experiments in PostgreSQL: every query, each scoped to its owner.

Every read and write here takes the owner and filters on it, so a route
cannot fetch someone else's experiment and then forget to check whose it
was. Another user's experiment is indistinguishable from one that does not
exist, which is also what a guessed id should learn.

Expired experiments are left out of every read as well as deleted by the
cleanup, so how long one is kept does not depend on when the cleanup last
ran.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any, cast
from uuid import UUID, uuid4

from primer_service.db import Database
from sqlalchemy import CursorResult, delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from primer_lab.tables import ExperimentRow

log = logging.getLogger(__name__)


def kept_since(now: datetime, retention_days: int) -> datetime:
    """The oldest an experiment may have been saved and still be kept."""
    return now - timedelta(days=retention_days)


async def count(session: AsyncSession, owner: UUID, since: datetime) -> int:
    query = select(func.count()).where(
        ExperimentRow.owner_user_id == owner, ExperimentRow.created_at >= since
    )
    return (await session.execute(query)).scalar_one()


async def create(
    session: AsyncSession,
    owner: UUID,
    *,
    kind: str,
    title: str,
    model: dict[str, Any],
    run: dict[str, Any],
    vectors: list[dict[str, Any]],
    probes: list[dict[str, Any]],
) -> ExperimentRow:
    row = ExperimentRow(
        id=uuid4(),
        owner_user_id=owner,
        kind=kind,
        title=title,
        model=model,
        run=run,
        vectors=vectors,
        probes=probes,
    )
    session.add(row)
    await session.flush()
    # `created_at` is the database's clock, so read it back rather than
    # stamping one here that the row would then disagree with.
    await session.refresh(row)
    return row


async def listed(session: AsyncSession, owner: UUID, since: datetime) -> list[ExperimentRow]:
    """The owner's experiments, newest first."""
    query = (
        select(ExperimentRow)
        .where(ExperimentRow.owner_user_id == owner, ExperimentRow.created_at >= since)
        .order_by(ExperimentRow.created_at.desc(), ExperimentRow.id)
    )
    return list((await session.execute(query)).scalars())


async def find(
    session: AsyncSession, owner: UUID, experiment_id: UUID, since: datetime
) -> ExperimentRow | None:
    query = select(ExperimentRow).where(
        ExperimentRow.id == experiment_id,
        ExperimentRow.owner_user_id == owner,
        ExperimentRow.created_at >= since,
    )
    return (await session.execute(query)).scalar_one_or_none()


async def remove(session: AsyncSession, owner: UUID, experiment_id: UUID, since: datetime) -> bool:
    """Delete the owner's experiment; whether there was one to delete."""
    query = delete(ExperimentRow).where(
        ExperimentRow.id == experiment_id,
        ExperimentRow.owner_user_id == owner,
        ExperimentRow.created_at >= since,
    )
    return await _deleted(session, query) > 0


async def purge_expired(database: Database, *, now: datetime, retention_days: int) -> int:
    """Delete every experiment saved before the retention period; how many went."""
    async with database.session() as session:
        query = delete(ExperimentRow).where(
            ExperimentRow.created_at < kept_since(now, retention_days)
        )
        removed = await _deleted(session, query)
    if removed:
        log.info("Deleted %d experiments past the retention period", removed)
    return removed


async def _deleted(session: AsyncSession, query: Any) -> int:
    # A DELETE answers with a cursor result, which has a row count; the
    # session's signature promises only the general kind of result.
    return cast(CursorResult[Any], await session.execute(query)).rowcount
