"""Making images, for Studio.

Any signed-in user may, on any enabled provider that declares images. Who
may use Studio at all, how many images they may make, and where the results
are kept are Control's to decide: it is the caller here, and it stores what
comes back. These routes only reach the backend.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Request, status
from primer_contracts.errors import ErrorCode
from primer_contracts.images import ImageProvider, ImageRequest, ImageResult
from primer_service.db import get_session
from primer_service.durable import DurableRoute
from primer_service.errors import ProblemError
from sqlalchemy.ext.asyncio import AsyncSession

from primer_chat import images
from primer_chat.config import Settings
from primer_chat.identity import CurrentPrincipal
from primer_chat.providers_store import ProviderStore

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


@router.post("", summary="Generate or edit images")
async def make_images(
    payload: ImageRequest, principal: CurrentPrincipal, request: Request, session: Session
) -> ImageResult:
    del principal
    settings: Settings = request.app.state.settings
    provider = await store_for(request, session).image_provider(payload.provider_id)
    if provider is None:
        raise ProblemError(
            code=ErrorCode.NOT_FOUND,
            title="Provider not found",
            status_code=status.HTTP_404_NOT_FOUND,
            detail="That provider does not make images on this deployment.",
        )
    return await images.make(provider, payload, timeout_seconds=settings.images_timeout_seconds)
