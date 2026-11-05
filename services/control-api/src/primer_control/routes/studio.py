"""Studio images: upload, list, fetch, download and delete.

Every route answers a missing image and someone else's image with the same
404, so an id reveals nothing about whether it exists. That includes an
administrator's request: see `StudioAccess`.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import datetime
from typing import Annotated
from uuid import UUID, uuid4

from fastapi import APIRouter, BackgroundTasks, Depends, Query, Request, Response, status
from fastapi.responses import StreamingResponse
from primer_contracts.documents import DocumentSummary
from primer_contracts.studio import STUDIO_MEDIA_TYPE, StudioImage, StudioToLibrary
from primer_service.db import get_session
from primer_service.durable import DurableRoute
from primer_storage import IMAGE_EXTENSIONS, SourceStore, SourceStoreError, StudioStore
from sqlalchemy.ext.asyncio import AsyncSession

from primer_control.config import Settings
from primer_control.dispatch import Dispatcher
from primer_control.identity import CurrentPrincipal, get_settings
from primer_control.repositories.studio import StudioRepository, view_of
from primer_control.repositories.users import UserRepository
from primer_control.routes.documents import (
    as_problem,
    enqueue,
    get_source_store,
    record_version,
    require_library,
    summarize,
)
from primer_control.studio.limits import (
    not_found,
    over_quota,
    require_studio,
    require_studio_library,
    upload_too_large,
)
from primer_control.studio.pictures import normalized, with_provenance
from primer_control.upload_stream import REQUEST_BODY, open_upload


def studio_on(settings: Annotated[Settings, Depends(get_settings)]) -> Settings:
    require_studio(settings)
    return settings


def get_studio_store(request: Request) -> StudioStore:
    store: StudioStore = request.app.state.studio_store
    return store


router = APIRouter(
    prefix="/api/v1/studio/images",
    tags=["studio"],
    route_class=DurableRoute,
    dependencies=[Depends(studio_on)],
)

Session = Annotated[AsyncSession, Depends(get_session)]
Config = Annotated[Settings, Depends(studio_on)]
Store = Annotated[StudioStore, Depends(get_studio_store)]
Sources = Annotated[SourceStore, Depends(get_source_store)]

MAX_PAGE = 100


@router.post(
    "",
    status_code=status.HTTP_201_CREATED,
    summary="Upload an image to edit",
    openapi_extra=REQUEST_BODY,
)
async def upload_image(
    principal: CurrentPrincipal, session: Session, settings: Config, store: Store, request: Request
) -> StudioImage:
    """Store an uploaded image as a PNG with its metadata removed.

    Read whole rather than streamed to the store, unlike a document: it has
    to be decoded before anything is kept, and the limit that bounds it is
    one image, not a book.
    """
    upload = open_upload(request, chunk_bytes=settings.upload_chunk_bytes)
    await upload.start()
    received: list[bytes] = []
    size = 0
    async for chunk in upload.chunks():
        size += len(chunk)
        if size > settings.studio_max_upload_bytes:
            raise upload_too_large(settings)
        received.append(chunk)
    picture = normalized(b"".join(received), max_side=settings.studio_max_side)

    repository = StudioRepository(session)
    refusal = over_quota(
        await repository.bytes_used(principal.user_id), len(picture.data), settings
    )
    if refusal is not None:
        raise refusal

    await UserRepository(session).ensure(principal)
    image_id = uuid4()
    await store.put(image_id, picture.data)
    try:
        image = await repository.add(
            image_id=image_id,
            owner_user_id=principal.user_id,
            origin="upload",
            width=picture.width,
            height=picture.height,
            byte_size=len(picture.data),
        )
    except BaseException:
        await store.remove(image_id)
        raise
    return view_of(image)


@router.get("", summary="List your Studio images, newest first")
async def list_images(
    principal: CurrentPrincipal,
    session: Session,
    limit: Annotated[int, Query(ge=1, le=MAX_PAGE)] = 50,
    before: Annotated[
        datetime | None, Query(description="Only images made before this time, to page back")
    ] = None,
) -> list[StudioImage]:
    images = await StudioRepository(session).page(principal.user_id, limit=limit, before=before)
    return [view_of(image) for image in images]


@router.get("/{image_id}", summary="One Studio image and how it was made")
async def get_image(image_id: UUID, principal: CurrentPrincipal, session: Session) -> StudioImage:
    image = await StudioRepository(session).readable(principal.user_id, image_id)
    if image is None:
        raise not_found()
    return view_of(image)


@router.get(
    "/{image_id}/content",
    summary="Download a Studio image",
    response_class=StreamingResponse,
    responses={200: {"content": {STUDIO_MEDIA_TYPE: {}}}},
)
async def download_image(
    image_id: UUID, principal: CurrentPrincipal, session: Session, store: Store
) -> StreamingResponse:
    image = await StudioRepository(session).readable(principal.user_id, image_id)
    if image is None:
        raise not_found()
    return StreamingResponse(
        store.open_stream(image.id),
        media_type=image.media_type,
        headers={
            "Content-Disposition": f'attachment; filename="primer-{image.id}.png"',
            # Private to one person, so no shared cache may keep a copy.
            "Cache-Control": "private, max-age=3600",
        },
    )


@router.delete(
    "/{image_id}", status_code=status.HTTP_204_NO_CONTENT, summary="Delete a Studio image"
)
async def delete_image(
    image_id: UUID,
    principal: CurrentPrincipal,
    session: Session,
    store: Store,
    background: BackgroundTasks,
) -> Response:
    """Delete the row now and the bytes once that is committed.

    In that order because bytes left behind by a failed removal are a file
    nobody can reach and no quota counts, where a row whose bytes went first
    is a gallery entry that will not load.
    """
    repository = StudioRepository(session)
    image = await repository.manageable(principal.user_id, image_id)
    if image is None:
        raise not_found()
    await repository.delete(image)
    background.add_task(store.remove, image_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/{image_id}/library",
    status_code=status.HTTP_201_CREATED,
    summary="Add a copy of a Studio image to a library",
)
async def add_to_library(
    image_id: UUID,
    body: StudioToLibrary,
    principal: CurrentPrincipal,
    session: Session,
    settings: Config,
    store: Store,
    sources: Sources,
    dispatcher: Dispatcher,
    background: BackgroundTasks,
) -> DocumentSummary:
    """Make the image a document, indexed by what a vision model says it shows.

    A copy, not a reference: a library belongs to whoever may manage it,
    and a Studio image to one person, who may delete it tomorrow. The copy
    carries its prompt and model in the file, so the document still says
    how it was made once the original is gone.
    """
    require_studio_library(settings)
    image = await StudioRepository(session).readable(principal.user_id, image_id)
    if image is None:
        raise not_found()
    await require_library(body.library_id, principal.user_id, session, to_manage=True)

    data = b"".join([chunk async for chunk in store.open_stream(image.id)])
    tagged = with_provenance(data, prompt=image.prompt, model=image.model)
    filename = f"primer-{image.id}.png"

    async def once() -> AsyncIterator[bytes]:
        yield tagged

    try:
        stored = await sources.put(once(), filename=filename, extensions=IMAGE_EXTENSIONS)
    except SourceStoreError as error:
        raise as_problem(error) from error
    record = await record_version(session, stored, filename=filename, library_id=body.library_id)
    enqueue(background, dispatcher, record)
    return summarize(record)
