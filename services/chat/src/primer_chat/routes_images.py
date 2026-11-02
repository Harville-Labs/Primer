"""Making images, for Studio.

Any signed-in user may, on any enabled provider that declares images. Who
may use Studio at all, how many images they may make, and where the results
are kept are Control's to decide: it is the caller here, and it stores what
comes back. These routes only reach the backend.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import APIRouter, Depends, Request, status
from fastapi.responses import StreamingResponse
from primer_contracts.errors import ErrorCode
from primer_contracts.images import (
    ImageDone,
    ImageFailed,
    ImageProgress,
    ImageProvider,
    ImageRequest,
    ImageResult,
    ImageStreamEvent,
)
from primer_service.db import get_session
from primer_service.durable import DurableRoute
from primer_service.errors import ProblemError
from primer_service.sse import encode
from sqlalchemy.ext.asyncio import AsyncSession

from primer_chat import comfyui, images
from primer_chat.config import Settings
from primer_chat.identity import CurrentPrincipal
from primer_chat.providers_store import ProviderStore, ResolvedProvider

router = APIRouter(prefix="/api/v1/images", tags=["images"], route_class=DurableRoute)

Session = Annotated[AsyncSession, Depends(get_session)]


def store_for(request: Request, session: AsyncSession) -> ProviderStore:
    settings: Settings = request.app.state.settings
    return ProviderStore(session, settings, request.app.state.secret_box)


@router.get("/providers", summary="Providers that make images, and what each declares")
async def list_image_providers(
    principal: CurrentPrincipal, request: Request, session: Session
) -> list[ImageProvider]:
    del principal
    return [
        ImageProvider(provider_id=p.id, provider_name=p.name, capabilities=p.images)
        for p in await store_for(request, session).images()
        if p.images is not None
    ]


async def provider_for(
    request: Request, session: AsyncSession, payload: ImageRequest
) -> ResolvedProvider:
    provider = await store_for(request, session).image_provider(payload.provider_id)
    if provider is None:
        raise ProblemError(
            code=ErrorCode.NOT_FOUND,
            title="Provider not found",
            status_code=status.HTTP_404_NOT_FOUND,
            detail="That provider does not make images on this deployment.",
        )
    return provider


async def make(
    provider: ResolvedProvider,
    payload: ImageRequest,
    settings: Settings,
    progress: comfyui.Progress | None = None,
) -> ImageResult:
    """Through whichever adapter the provider is declared to speak."""
    timeout = settings.images_timeout_seconds
    if provider.images is not None and provider.images.api == "comfyui":
        return await comfyui.make(provider, payload, timeout_seconds=timeout, progress=progress)
    return await images.make(provider, payload, timeout_seconds=timeout)


@router.post("", summary="Generate or edit images")
async def make_images(
    payload: ImageRequest, principal: CurrentPrincipal, request: Request, session: Session
) -> ImageResult:
    del principal
    provider = await provider_for(request, session, payload)
    return await make(provider, payload, request.app.state.settings)


@router.post(
    "/stream",
    summary="Generate or edit images, reporting progress",
    response_class=StreamingResponse,
)
async def stream_images(
    payload: ImageRequest, principal: CurrentPrincipal, request: Request, session: Session
) -> StreamingResponse:
    """The same as `POST /images`, as server-sent events.

    Anything wrong with the request itself is an ordinary error response,
    checked before the stream opens; only what happens at the backend
    arrives as a `failed` event, since by then the status line is sent.
    """
    del principal
    settings: Settings = request.app.state.settings
    provider = await provider_for(request, session, payload)
    if provider.images is not None and provider.images.api == "comfyui":
        comfyui.template_for(provider, payload)
    else:
        images.check_declared(provider, payload)

    async def events() -> AsyncIterator[str]:
        queue: asyncio.Queue[ImageStreamEvent] = asyncio.Queue()
        counter = iter(range(2**31))

        async def progress(value: int, most: int) -> None:
            await queue.put(ImageProgress(id=next(counter), value=value, max=most))

        async def run() -> None:
            try:
                result = await make(provider, payload, settings, progress)
                await queue.put(ImageDone(id=next(counter), result=result))
            except ProblemError as error:
                await queue.put(
                    ImageFailed(
                        id=next(counter),
                        code=error.code.value,
                        title=error.title,
                        detail=error.detail,
                        status=error.status_code,
                    )
                )

        task = asyncio.create_task(run())
        try:
            while True:
                event = await queue.get()
                yield encode(event)
                if isinstance(event, ImageDone | ImageFailed):
                    return
        finally:
            # A caller that hung up has no use for the image; the run is
            # abandoned here, though ComfyUI may finish it anyway.
            task.cancel()

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
