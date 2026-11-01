"""Chat application factory."""

from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from primer_service.db import Database
from primer_service.errors import ProblemError, problem_response, rendered, validation_problem

from primer_chat import __version__
from primer_chat.clients import ControlClient, LibraryAuthority, PassageSource, RetrievalClient
from primer_chat.config import Settings
from primer_chat.generation import ChatGenerator, HaystackChatGenerator
from primer_chat.routes import router
from primer_chat.routes_admin import router as admin_router
from primer_chat.routes_passages import router as passages_router
from primer_chat.routes_scoring import router as scoring_router
from primer_chat.routes_tools import router as tool_router
from primer_chat.secrets import SecretBox
from primer_chat.streaming import Responder


def create_app(
    settings: Settings | None = None,
    database: Database | None = None,
    control: LibraryAuthority | None = None,
    retrieval: PassageSource | None = None,
    generator: ChatGenerator | None = None,
) -> FastAPI:
    """Build the Chat service.

    Control, Retrieval, and the model are injected so the orchestration can
    be exercised end to end without a cluster or an inference endpoint - the
    ordering guarantees are what most need testing, and they do not depend
    on any of the three being real.
    """
    settings = settings or Settings()
    app = FastAPI(
        title="Primer Chat",
        version=__version__,
        summary="Cited, streamed answers grounded in a user's own library",
    )
    app.state.settings = settings
    app.state.database = database or Database(settings.database_url)
    # Built once: constructing the cipher validates the configured key, so a
    # deployment with a malformed one is told at startup rather than the
    # first time an administrator tries to save a provider.
    app.state.secret_box = SecretBox(
        settings.settings_encryption_key.get_secret_value()
        if settings.settings_encryption_key
        else None
    )
    # Kept beside the responder for the routes that read a library without
    # answering from it, so both ask the same Control and the same Retrieval.
    app.state.control = control or ControlClient(settings)
    app.state.retrieval = retrieval or RetrievalClient(settings)
    app.state.responder = Responder(
        settings,
        app.state.control,
        app.state.retrieval,
        generator or HaystackChatGenerator(settings),
    )

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

    @app.get("/health/live", summary="Process liveness")
    async def live() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/health/ready", summary="Readiness")
    async def ready() -> JSONResponse:
        reachable = await app.state.database.check()
        return JSONResponse(
            status_code=200 if reachable else 503,
            content={"status": "ok" if reachable else "unready", "database": reachable},
        )

    app.include_router(router)
    app.include_router(tool_router)
    app.include_router(admin_router)
    app.include_router(scoring_router)
    app.include_router(passages_router)
    return app
