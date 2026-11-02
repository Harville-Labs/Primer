"""Running one generation to the end, whether or not anyone is watching.

A generation takes minutes and a browser tab is closed in a second. The
run is therefore a task of its own, held by the application rather than by
the request: the stream to the browser reads its events while it is there,
and the images are stored either way, waiting in the gallery when the
person comes back. Cancelling the run on disconnect, as Chat does for its
caller, would throw away work a provider was paid for.

The run opens its own database sessions. The request's session is closed
once the response starts, which is long before the images arrive.
"""

from __future__ import annotations

import asyncio
import base64
import logging
from collections.abc import Callable
from dataclasses import dataclass
from uuid import UUID, uuid4

from fastapi import status
from primer_contracts.errors import ErrorCode
from primer_contracts.identity import Principal
from primer_contracts.images import ImageFailed, ImageProgress, ImageRequest, ImageResult
from primer_contracts.studio import (
    StudioDone,
    StudioFailed,
    StudioGeneration,
    StudioProgress,
    StudioProvenance,
)
from primer_service.db import Database
from primer_service.errors import ProblemError
from primer_storage import StudioStore

from primer_control.config import Settings
from primer_control.repositories.studio import StudioRepository, view_of
from primer_control.studio.chat_images import stream_images
from primer_control.studio.limits import over_quota
from primer_control.studio.pictures import Picture, normalized

logger = logging.getLogger(__name__)

StudioEvent = StudioProgress | StudioDone | StudioFailed


@dataclass(frozen=True, slots=True)
class Run:
    """Everything a generation needs once the request that started it is gone."""

    generation_id: UUID
    principal: Principal
    body: StudioGeneration
    source: bytes | None
    settings: Settings
    database: Database
    store: StudioStore


def chat_request(body: StudioGeneration, source: bytes | None) -> ImageRequest:
    """Chat's request for a Studio one: the same fields, with the image inline.

    Built from encoded values because `ImageRequest` decodes its images on
    the way in; passing the decoded bytes would decode them a second time.
    """
    fields = body.model_dump(exclude={"source_id", "mask"})
    for name, data in (("image", source), ("mask", body.mask)):
        if data is not None:
            fields[name] = base64.b64encode(data).decode()
    return ImageRequest.model_validate(fields)


def failed_from(problem: ProblemError | ImageFailed, event_id: int) -> StudioFailed:
    if isinstance(problem, ImageFailed):
        return StudioFailed(
            id=event_id,
            code=problem.code,
            title=problem.title,
            detail=problem.detail,
            status=problem.status,
        )
    return StudioFailed(
        id=event_id,
        code=problem.code.value,
        title=problem.title,
        detail=problem.detail,
        status=problem.status_code,
    )


def unusable_output() -> ProblemError:
    return ProblemError(
        code=ErrorCode.DEPENDENCY_UNAVAILABLE,
        title="The provider made something that is not an image",
        status_code=status.HTTP_502_BAD_GATEWAY,
        detail="Nothing was stored. The provider's log may say why; trying again may not help.",
    )


async def store_results(run: Run, result: ImageResult) -> StudioDone:
    """Decode, check the quota, write the bytes, then the rows."""
    try:
        pictures = [
            normalized(data, max_side=run.settings.studio_max_side) for data in result.images
        ]
    except ProblemError as error:
        raise unusable_output() from error

    owner = run.principal.user_id
    provenance = StudioProvenance(
        provider_id=result.provider_id,
        model=result.model,
        operation=run.body.operation,
        prompt=run.body.prompt,
        negative_prompt=run.body.negative_prompt,
        size=run.body.size,
        seed=result.seed if result.seed is not None else run.body.seed,
        steps=run.body.steps,
    )
    stored: list[tuple[UUID, Picture]] = [(uuid4(), picture) for picture in pictures]
    try:
        for image_id, picture in stored:
            await run.store.put(image_id, picture.data)
        async with run.database.session() as session:
            repository = StudioRepository(session)
            # Checked again here, not only before starting: the run took
            # minutes, and uploads or other runs may have landed meanwhile.
            refusal = over_quota(
                await repository.bytes_used(owner),
                sum(len(picture.data) for _, picture in stored),
                run.settings,
            )
            if refusal is not None:
                raise refusal
            images = [
                await repository.add(
                    image_id=image_id,
                    owner_user_id=owner,
                    origin="generated",
                    width=picture.width,
                    height=picture.height,
                    byte_size=len(picture.data),
                    parent_id=run.body.source_id,
                    generation_id=run.generation_id,
                    provenance=provenance,
                )
                for image_id, picture in stored
            ]
            return StudioDone(id=0, images=tuple(view_of(image) for image in images))
    except BaseException:
        for image_id, _ in stored:
            await run.store.remove(image_id)
        raise


async def finish(run: Run, error_code: str | None) -> None:
    async with run.database.session() as session:
        await StudioRepository(session).finish_generation(run.generation_id, error_code=error_code)


async def execute(run: Run, emit: Callable[[StudioEvent], None]) -> None:
    """Run the generation, emitting progress and then exactly one done or failed."""
    counter = iter(range(2**31))
    outcome: StudioEvent | None = None
    try:
        async for event in stream_images(
            chat_request(run.body, run.source), run.principal, run.settings
        ):
            if isinstance(event, ImageProgress):
                emit(StudioProgress(id=next(counter), value=event.value, max=event.max))
            elif isinstance(event, ImageFailed):
                outcome = failed_from(event, next(counter))
            else:
                done = await store_results(run, event.result)
                outcome = done.model_copy(update={"id": next(counter)})
        if outcome is None:
            # `stream_images` ends every stream with one; reaching here is a
            # bug, reported as one by the handler below.
            raise RuntimeError("the image stream ended without a result")
    except ProblemError as error:
        outcome = failed_from(error, next(counter))
    except Exception:
        # Logged without the request: a prompt is not for the log.
        logger.exception("studio generation %s failed", run.generation_id)
        outcome = StudioFailed(
            id=next(counter),
            code=ErrorCode.INTERNAL_ERROR.value,
            title="Something went wrong",
            detail="The images could not be stored. This is a bug in Primer.",
            status=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )
    try:
        await finish(run, outcome.code if isinstance(outcome, StudioFailed) else None)
    except Exception:
        # The generation row only feeds the hourly limit; failing to mark it
        # finished leaves that count right and is not worth losing images.
        logger.exception("could not record the end of studio generation %s", run.generation_id)
    emit(outcome)


def start(runs: set[asyncio.Task[None]], run: Run) -> asyncio.Queue[StudioEvent]:
    """Begin a run held by `runs`, and return the queue its events arrive on."""
    queue: asyncio.Queue[StudioEvent] = asyncio.Queue()
    task = asyncio.create_task(execute(run, queue.put_nowait))
    runs.add(task)
    task.add_done_callback(runs.discard)
    return queue
