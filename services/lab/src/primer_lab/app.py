"""Lab application factory."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncGenerator, Callable
from contextlib import asynccontextmanager
from datetime import UTC, datetime

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from nnterp import StandardizedTransformer
from primer_service.db import Database
from primer_service.errors import ProblemError, problem_response, rendered, validation_problem

from primer_lab import __version__, editing, experiments, graphs, probes, saved, scan, scoring
from primer_lab.config import Settings
from primer_lab.host import ModelHost
from primer_lab.labels import read_labels
from primer_lab.loader import ModelFactory, resolve_device
from primer_lab.queue import RunQueue
from primer_lab.routes import router
from primer_lab.sources import Hub, HuggingFaceHub

log = logging.getLogger(__name__)

#: How often expired experiments are deleted. An expired one is already
#: left out of every read, so this bounds how long its rows linger on disk,
#: not how long anyone can see it.
PURGE_SECONDS = 3600.0


async def purge_periodically(database: Database, retention_days: int) -> None:
    """Delete expired experiments now and every `PURGE_SECONDS` after.

    In the process rather than as a CronJob, so a Compose deployment keeps
    to the retention period too. A failed pass is logged and the next one
    tries again: the database being briefly away is no reason to stop Lab.
    """
    while True:
        try:
            await saved.purge_expired(
                database, now=datetime.now(UTC), retention_days=retention_days
            )
        except Exception:
            log.exception("Could not delete expired experiments; trying again later")
        await asyncio.sleep(PURGE_SECONDS)


def create_app(
    settings: Settings | None = None,
    *,
    hub: Hub | None = None,
    factory: ModelFactory = StandardizedTransformer,
    cuda_available: Callable[[], bool] | None = None,
    database: Database | None = None,
) -> FastAPI:
    """Build the Lab service.

    The hub, the model constructor, and the GPU check are injected so a test
    can run every path here against a model small enough to build in a
    fixture, and can prove what Lab asked for as well as what it got. The
    database is injected for the same reason; without one, and without a
    URL to make one from, Lab runs and saves nothing.
    """
    settings = settings or Settings()
    queue = RunQueue(max_waiting=settings.lab_max_waiting_runs)
    host = ModelHost(
        settings,
        queue,
        hub or HuggingFaceHub(settings),
        factory,
        cuda_available=cuda_available,
        labels=read_labels(settings),
    )
    if database is None and settings.database_url:
        database = Database(settings.database_url)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncGenerator[None]:
        # A device the operator asked for and does not have is a deployment
        # mistake, and refusing to start is how it reaches them. A model
        # that fails to fetch or load is not: users can choose another, so
        # the service stays up and the status says what went wrong.
        resolve_device(settings, cuda_available)
        host.request(settings.lab_model)
        purging = (
            asyncio.create_task(
                purge_periodically(database, settings.lab_experiment_retention_days)
            )
            if database
            else None
        )
        try:
            yield
        finally:
            if purging:
                purging.cancel()
            await host.wait()
            if database:
                await database.dispose()

    app = FastAPI(
        title="Primer Lab",
        version=__version__,
        summary="Look inside an open model and edit how it runs",
        lifespan=lifespan,
    )
    app.state.settings = settings
    app.state.queue = queue
    app.state.host = host
    app.state.database = database

    @app.exception_handler(ProblemError)
    async def _handle_problem(request: Request, exc: ProblemError) -> JSONResponse:
        return problem_response(request, exc)

    @app.exception_handler(RequestValidationError)
    async def _handle_validation(request: Request, exc: RequestValidationError) -> JSONResponse:
        return rendered(request, validation_problem(exc))

    @app.get("/health/live", summary="Process liveness")
    async def live() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/health/ready", summary="Readiness")
    async def ready() -> dict[str, str]:
        """Ready to answer, whether or not a model is resident yet.

        Gating this on the model looks right and is a trap: an unready pod
        is taken out of its Service, so after a failed load nobody could
        reach Lab to choose a model that works. Whether runs can be made is
        the status route's `ready`, which the web app reads. The database is
        left out for the same reason: without it only saving fails, and the
        saving routes say so.
        """
        return {"status": "ok"}

    app.include_router(router)
    app.include_router(editing.router)
    app.include_router(scoring.router)
    app.include_router(probes.router)
    app.include_router(experiments.router)
    app.include_router(scan.router)
    app.include_router(graphs.router)
    return app
