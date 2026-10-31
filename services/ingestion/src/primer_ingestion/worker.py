"""Worker entrypoint.

Run with: celery -A primer_ingestion.worker worker -Q ingestion.parse,...

Registering every stage in one place means a worker either knows how to run
a stage or fails it loudly. A process that silently lacked a handler would
mark documents failed for a deployment reason, so the registration is not
conditional on anything.

Registration also happens a second time in every forked child, which is the
subject of the note on `rebuild_after_fork` below. That applies to the
prefork pool the parse worker uses. The index worker runs on threads, which
never fork: the stages built at import are the ones every thread shares, and
everything they hold - HTTP clients, storage handles - is safe to share.
"""

from __future__ import annotations

import fsspec
import fsspec.asyn
from celery.signals import worker_process_init
from primer_contracts.ingestion import StageName

from primer_ingestion.config import Settings
from primer_ingestion.stages.delete import DeleteStage
from primer_ingestion.stages.embed import EmbedStage
from primer_ingestion.stages.index import IndexStage
from primer_ingestion.stages.parse import ParseStage
from primer_ingestion.tasks import app, register_handler


def register_stages(settings: Settings | None = None) -> None:
    settings = settings or Settings()
    register_handler(StageName.PARSE, ParseStage(settings))
    register_handler(StageName.EMBED, EmbedStage(settings))
    register_handler(StageName.INDEX, IndexStage(settings))
    register_handler(StageName.DELETE, DeleteStage(settings))


@worker_process_init.connect
def rebuild_after_fork(**_: object) -> None:
    """Give each pool child its own storage clients.

    A stage holds an fsspec filesystem, and for anything but a local
    directory that is an async filesystem: it records the pid and the event
    loop it was built on, and refuses to run once either belongs to another
    process. Building the stages at import puts them in the Celery parent,
    and the prefork pool then hands every child a filesystem that raises
    `RuntimeError: This class is not fork-safe` on its first call - which
    the parse stage reports as a failed document, so nothing a deployment
    uploads is ever readable.

    Rebuilding is not enough on its own. fsspec caches instances by their
    constructor arguments, and that cache is inherited across the fork too,
    so a child asking for the same URL is handed the parent's object back.
    The cache is cleared first and the loop's lock reset, so what the child
    builds is genuinely its own.
    """
    fsspec.asyn.reset_lock()
    fsspec.AbstractFileSystem.clear_instance_cache()
    register_stages()


register_stages()

__all__ = ["app", "rebuild_after_fork", "register_stages"]
