"""Lab application factory."""

from __future__ import annotations

from collections.abc import AsyncGenerator, Callable
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from nnterp import StandardizedTransformer
from primer_service.errors import ProblemError, problem_response, rendered, validation_problem

from primer_lab import __version__
from primer_lab.config import Settings
from primer_lab.host import ModelHost
from primer_lab.loader import ModelFactory, resolve_device
from primer_lab.queue import RunQueue
from primer_lab.routes import router
from primer_lab.sources import Hub, HuggingFaceHub


def create_app(
    settings: Settings | None = None,
    *,
    hub: Hub | None = None,
    factory: ModelFactory = StandardizedTransformer,
    cuda_available: Callable[[], bool] | None = None,
) -> FastAPI:
    """Build the Lab service.

    The hub, the model constructor, and the GPU check are injected so a test
    can run every path here against a model small enough to build in a
    fixture, and can prove what Lab asked for as well as what it got.
    """
    settings = settings or Settings()
    queue = RunQueue(max_waiting=settings.lab_max_waiting_runs)
    host = ModelHost(
        settings, queue, hub or HuggingFaceHub(settings), factory, cuda_available=cuda_available
    )

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncGenerator[None]:
        # A device the operator asked for and does not have is a deployment
        # mistake, and refusing to start is how it reaches them. A model
        # that fails to fetch or load is not: users can choose another, so
        # the service stays up and the status says what went wrong.
        resolve_device(settings, cuda_available)
        host.request(settings.lab_model)
        try:
            yield
        finally:
            await host.wait()

    app = FastAPI(
        title="Primer Lab",
        version=__version__,
        summary="Look inside an open model and edit how it runs",
        lifespan=lifespan,
    )
    app.state.settings = settings
    app.state.queue = queue
    app.state.host = host

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
        the status route's `ready`, which the web app reads.
        """
        return {"status": "ok"}

    app.include_router(router)
    return app
