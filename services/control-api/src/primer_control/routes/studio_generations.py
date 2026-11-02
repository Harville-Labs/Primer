"""Starting a generation, and watching it while it runs.

Everything that can be refused is refused here, as an ordinary error
response, before the stream opens and before Chat is asked: the switch, the
count, the hourly limit, the quota, and whether the image to edit is the
caller's. Only what happens at the provider arrives as a `failed` event.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, Request, status
from fastapi.responses import StreamingResponse
from primer_contracts.errors import ErrorCode
from primer_contracts.studio import StudioDone, StudioFailed, StudioGeneration
from primer_service.db import get_session
from primer_service.durable import DurableRoute
from primer_service.errors import ProblemError
from primer_service.sse import encode
from sqlalchemy.ext.asyncio import AsyncSession

from primer_control.config import Settings
from primer_control.identity import CurrentPrincipal
from primer_control.repositories.studio import StudioRepository
from primer_control.repositories.users import UserRepository
from primer_control.routes.studio import Store, studio_on
from primer_control.studio.generation import Run, start
from primer_control.studio.limits import not_found, over_quota

router = APIRouter(prefix="/api/v1/studio", tags=["studio"], route_class=DurableRoute)

Session = Annotated[AsyncSession, Depends(get_session)]
Config = Annotated[Settings, Depends(studio_on)]


def unavailable() -> ProblemError:
    return ProblemError(
        code=ErrorCode.DEPENDENCY_UNAVAILABLE,
        title="No image generation here",
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail="This deployment has no Chat service, which is what makes images.",
    )


def too_many(settings: Settings) -> ProblemError:
    return ProblemError(
        code=ErrorCode.QUOTA_EXCEEDED,
        title="Too many generations",
        status_code=status.HTTP_429_TOO_MANY_REQUESTS,
        detail=(
            f"You can start {settings.studio_generations_per_hour} generations an hour. "
            "Try again later."
        ),
    )


def too_many_images(settings: Settings) -> ProblemError:
    return ProblemError(
        code=ErrorCode.VALIDATION_FAILED,
        title="Too many images",
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        detail=f"Ask for at most {settings.studio_max_images_per_request} images at a time.",
    )


@router.post(
    "/generations",
    summary="Generate or edit images into your Studio, reporting progress",
    response_class=StreamingResponse,
)
async def generate(
    body: StudioGeneration,
    principal: CurrentPrincipal,
    session: Session,
    settings: Config,
    store: Store,
    request: Request,
) -> StreamingResponse:
    if not settings.chat_service_url:
        raise unavailable()
    if body.count > settings.studio_max_images_per_request:
        raise too_many_images(settings)

    repository = StudioRepository(session)
    owner = principal.user_id
    hour_ago = datetime.now(UTC) - timedelta(hours=1)
    if await repository.generations_since(owner, hour_ago) >= settings.studio_generations_per_hour:
        raise too_many(settings)
    # A single byte, since the results' size is unknown until they arrive:
    # this refuses a full Studio, and `store_results` checks the real size.
    refusal = over_quota(await repository.bytes_used(owner), 1, settings)
    if refusal is not None:
        raise refusal

    source = None
    if body.source_id is not None:
        image = await repository.readable(owner, body.source_id)
        if image is None:
            raise not_found()
        source = b"".join([chunk async for chunk in store.open_stream(image.id)])

    await UserRepository(session).ensure(principal)
    generation = await repository.start_generation(owner)
    run = Run(
        generation_id=generation.id,
        principal=principal,
        body=body,
        source=source,
        settings=settings,
        database=request.app.state.database,
        store=store,
    )
    runs: set[asyncio.Task[None]] = request.app.state.studio_runs

    async def events() -> AsyncIterator[str]:
        # Started here rather than in the handler: this runs once the
        # response has begun, which is after `DurableRoute` committed the
        # generation row the run will later mark finished.
        queue = start(runs, run)
        while True:
            event = await queue.get()
            yield encode(event)
            if isinstance(event, StudioDone | StudioFailed):
                return

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
