"""Deleting Studio images past the retention an operator set.

Run as `python -m primer_control.studio.retention`, by the chart's CronJob
or by hand. Not on a timer inside the API: several replicas would race to
delete the same rows, and a deletion schedule is something an operator
should be able to see and suspend.

With no retention set it deletes nothing, which is the default: a person's
images are theirs to delete.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, timedelta

from primer_service.db import Database
from primer_storage import StudioStore

from primer_control.config import Settings
from primer_control.repositories.studio import StudioRepository

logger = logging.getLogger(__name__)

#: Rows deleted per transaction, so a first run over a large backlog does
#: not hold one lock across all of it.
BATCH = 500


async def purge(settings: Settings, database: Database, store: StudioStore, now: datetime) -> int:
    """Delete images older than the retention, and return how many."""
    if settings.studio_retention_days is None:
        return 0
    cutoff = now - timedelta(days=settings.studio_retention_days)
    deleted = 0
    while True:
        async with database.session() as session:
            repository = StudioRepository(session)
            expired = await repository.expired(cutoff, limit=BATCH)
            await repository.remove_rows(expired)
        # Bytes after the commit, as a person's own delete does.
        for image_id in expired:
            await store.remove(image_id)
        deleted += len(expired)
        if len(expired) < BATCH:
            break
    async with database.session() as session:
        await StudioRepository(session).forget_generations_before(cutoff)
    return deleted


async def _main() -> None:
    settings = Settings()
    database = Database(settings.database_url)
    try:
        store = StudioStore(settings.source_store_url, chunk_bytes=settings.upload_chunk_bytes)
        deleted = await purge(settings, database, store, datetime.now(UTC))
        logger.info("deleted %d Studio images past retention", deleted)
    finally:
        await database.dispose()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(_main())
