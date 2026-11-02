"""Studio persistence: images, the generations that made them, and use."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from uuid import UUID

from primer_contracts.studio import (
    STUDIO_MEDIA_TYPE,
    StudioOrigin,
    StudioProvenance,
    StudioUsage,
)
from primer_contracts.studio import (
    StudioImage as StudioImageView,
)
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from primer_control.models import StudioGeneration, StudioImage, User
from primer_control.services.studio_access import StudioAccess

access = StudioAccess()


def view_of(image: StudioImage) -> StudioImageView:
    """The wire shape of a stored image. One place, for list, get and done."""
    provenance = None
    if image.origin == "generated" and image.provider_id is not None:
        provenance = StudioProvenance.model_validate(
            {
                "provider_id": image.provider_id,
                "model": image.model,
                "operation": image.operation,
                "prompt": image.prompt,
                "negative_prompt": image.negative_prompt,
                "size": image.size,
                "seed": image.seed,
                "steps": image.steps,
            }
        )
    return StudioImageView.model_validate(
        {
            "id": image.id,
            "origin": image.origin,
            "media_type": image.media_type,
            "width": image.width,
            "height": image.height,
            "byte_size": image.byte_size,
            "created_at": image.created_at,
            "parent_id": image.parent_id,
            "provenance": provenance,
        }
    )


class StudioRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def page(
        self, principal_id: UUID, *, limit: int, before: datetime | None
    ) -> Sequence[StudioImage]:
        """Newest first, a page at a time, keyed by time rather than offset.

        An offset skips or repeats an image whenever one is made while
        someone is paging, which in a gallery that fills as you watch is
        most of the time.
        """
        query = select(StudioImage).where(access.readable(principal_id))
        if before is not None:
            query = query.where(StudioImage.created_at < before)
        query = query.order_by(StudioImage.created_at.desc(), StudioImage.id.desc()).limit(limit)
        return (await self._session.scalars(query)).all()

    async def readable(self, principal_id: UUID, image_id: UUID) -> StudioImage | None:
        return await self._session.scalar(
            select(StudioImage).where(StudioImage.id == image_id, access.readable(principal_id))
        )

    async def manageable(self, principal_id: UUID, image_id: UUID) -> StudioImage | None:
        return await self._session.scalar(
            select(StudioImage).where(StudioImage.id == image_id, access.manageable(principal_id))
        )

    async def add(
        self,
        *,
        image_id: UUID,
        owner_user_id: UUID,
        origin: StudioOrigin,
        width: int,
        height: int,
        byte_size: int,
        parent_id: UUID | None = None,
        generation_id: UUID | None = None,
        provenance: StudioProvenance | None = None,
    ) -> StudioImage:
        fields = provenance.model_dump() if provenance is not None else {}
        image = StudioImage(
            id=image_id,
            owner_user_id=owner_user_id,
            origin=origin,
            media_type=STUDIO_MEDIA_TYPE,
            width=width,
            height=height,
            byte_size=byte_size,
            parent_id=parent_id,
            generation_id=generation_id,
            **fields,
        )
        self._session.add(image)
        await self._session.flush()
        await self._session.refresh(image)
        return image

    async def delete(self, image: StudioImage) -> None:
        await self._session.delete(image)
        await self._session.flush()

    async def bytes_used(self, owner_user_id: UUID) -> int:
        total = await self._session.scalar(
            select(func.coalesce(func.sum(StudioImage.byte_size), 0)).where(
                StudioImage.owner_user_id == owner_user_id
            )
        )
        return int(total or 0)

    async def start_generation(self, owner_user_id: UUID) -> StudioGeneration:
        generation = StudioGeneration(owner_user_id=owner_user_id)
        self._session.add(generation)
        await self._session.flush()
        return generation

    async def finish_generation(self, generation_id: UUID, *, error_code: str | None) -> None:
        generation = await self._session.get(StudioGeneration, generation_id)
        if generation is not None:
            generation.finished_at = func.now()
            generation.error_code = error_code

    async def generations_since(self, owner_user_id: UUID, since: datetime) -> int:
        count = await self._session.scalar(
            select(func.count())
            .select_from(StudioGeneration)
            .where(
                StudioGeneration.owner_user_id == owner_user_id,
                StudioGeneration.created_at >= since,
            )
        )
        return int(count or 0)

    async def usage(self) -> list[StudioUsage]:
        """Each person with any Studio images, heaviest first."""
        rows = await self._session.execute(
            select(
                User.id,
                User.email,
                func.count(StudioImage.id),
                func.coalesce(func.sum(StudioImage.byte_size), 0),
            )
            .join(StudioImage, StudioImage.owner_user_id == User.id)
            .group_by(User.id, User.email)
            .order_by(func.sum(StudioImage.byte_size).desc(), User.id)
        )
        return [
            StudioUsage(user_id=user_id, email=email, image_count=count, byte_size=int(size))
            for user_id, email, count, size in rows
        ]

    async def expired(self, older_than: datetime, *, limit: int) -> Sequence[UUID]:
        return (
            await self._session.scalars(
                select(StudioImage.id)
                .where(StudioImage.created_at < older_than)
                .order_by(StudioImage.created_at)
                .limit(limit)
            )
        ).all()

    async def remove_rows(self, image_ids: Sequence[UUID]) -> None:
        await self._session.execute(delete(StudioImage).where(StudioImage.id.in_(image_ids)))

    async def forget_generations_before(self, older_than: datetime) -> None:
        """Generations past the hour only ever served the rate limit."""
        await self._session.execute(
            delete(StudioGeneration).where(StudioGeneration.created_at < older_than)
        )
