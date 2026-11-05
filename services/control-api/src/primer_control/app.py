"""Control API application factory."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager, suppress
from datetime import timedelta

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from primer_service.db import Database
from primer_service.errors import ProblemError, problem_response, rendered, validation_problem
from primer_storage import SourceStore, StudioStore

from primer_control import __version__
from primer_control.config import Settings
from primer_control.dispatch import JobDispatcher
from primer_control.health import DependencyRegistry
from primer_control.middleware import RequestIDMiddleware
from primer_control.publisher import JobPublisher, NullPublisher, build_publisher
from primer_control.routes import admin as admin_routes
from primer_control.routes import capabilities as capability_routes
from primer_control.routes import documents as document_routes
from primer_control.routes import health as health_routes
from primer_control.routes import identity as identity_routes
from primer_control.routes import internal_authz as internal_authz_routes
from primer_control.routes import internal_ingestion as internal_ingestion_routes
from primer_control.routes import libraries as library_routes
from primer_control.routes import studio as studio_routes
from primer_control.routes import studio_generations as studio_generation_routes


def create_app(
    settings: Settings | None = None,
    dependencies: DependencyRegistry | None = None,
    database: Database | None = None,
    source_store: SourceStore | None = None,
    publisher: JobPublisher | None = None,
    studio_store: StudioStore | None = None,
) -> FastAPI:
    """Build the Control API.

    Settings, the readiness registry, and the database are injected rather
    than read from module globals so tests, and later the Compose and Helm
    entrypoints, can compose a deployment without mutating process state.
    """
    settings = settings or Settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncGenerator[None]:
        # No sweep without a broker: the null publisher accepts everything,
        # so there is never anything to send again.
        if isinstance(app.state.publisher, NullPublisher):
            yield
            return
        interval = timedelta(seconds=settings.job_resend_interval_seconds)
        sweep = asyncio.create_task(app.state.dispatcher.run(interval))
        try:
            yield
        finally:
            sweep.cancel()
            with suppress(asyncio.CancelledError):
                await sweep

    app = FastAPI(
        title="Primer Control API",
        version=__version__,
        summary="Public domain operations and authorization for Primer",
        lifespan=lifespan,
    )
    app.state.settings = settings
    app.state.database = database or Database(settings.database_url)
    app.state.source_store = source_store or SourceStore(
        settings.source_store_url,
        max_bytes=settings.max_upload_bytes,
        chunk_bytes=settings.upload_chunk_bytes,
    )
    app.state.studio_store = studio_store or StudioStore(
        settings.source_store_url, chunk_bytes=settings.upload_chunk_bytes
    )
    #: Generations in progress, held so a run outlives the request that
    #: started it and is not collected while it waits on Chat.
    app.state.studio_runs = set()
    app.state.publisher = publisher or build_publisher(settings.broker_url)
    app.state.dispatcher = JobDispatcher(
        app.state.database,
        app.state.publisher,
        resend_after=timedelta(seconds=settings.job_resend_after_seconds),
    )
    registry = dependencies or DependencyRegistry()
    registry.register_async("database", app.state.database.check)
    app.state.dependencies = registry

    app.add_middleware(RequestIDMiddleware, header_name=settings.request_id_header)

    @app.exception_handler(ProblemError)
    async def _handle_problem(request: Request, exc: ProblemError) -> JSONResponse:
        return problem_response(request, exc)

    @app.exception_handler(RequestValidationError)
    async def _handle_validation(request: Request, exc: RequestValidationError) -> JSONResponse:
        """Answer a malformed request in the shape every other failure uses.

        Without this, FastAPI answers in its own: a list of objects under
        `detail`, where the contract promises a string. Clients read it as
        one, and a rejected field arrived on screen as "[object Object]".
        """
        return rendered(request, validation_problem(exc))

    app.include_router(health_routes.router)
    app.include_router(identity_routes.router)
    app.include_router(library_routes.router)
    app.include_router(document_routes.router)
    app.include_router(capability_routes.router)
    app.include_router(studio_routes.router)
    app.include_router(studio_generation_routes.router)
    app.include_router(admin_routes.router)
    app.include_router(internal_ingestion_routes.router)
    app.include_router(internal_authz_routes.router)
    return app
