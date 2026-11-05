"""Library CRUD.

Inaccessible libraries return 404, never 403: telling a stranger that a
library exists but is not theirs is itself a disclosure.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Depends, Response, status
from primer_contracts.errors import ErrorCode
from primer_contracts.ingestion import StageName
from primer_contracts.libraries import LibraryShare, LibrarySummary
from primer_service.db import get_session
from primer_service.durable import DurableRoute
from primer_service.errors import ProblemError
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy.ext.asyncio import AsyncSession

from primer_control.dispatch import Dispatcher
from primer_control.identity import CurrentPrincipal
from primer_control.models import Library, LibraryGrant, User
from primer_control.repositories.grants import LibraryGrantRepository
from primer_control.repositories.libraries import LibraryRepository
from primer_control.repositories.users import UserRepository
from primer_control.services.duplication import LibraryDuplicator, copy_name
from primer_control.services.library_access import LibraryAccess

router = APIRouter(prefix="/api/v1/libraries", tags=["libraries"], route_class=DurableRoute)

Session = Annotated[AsyncSession, Depends(get_session)]


access = LibraryAccess()


class LibraryCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    name: str = Field(min_length=1, max_length=120)

    @field_validator("name")
    @classmethod
    def _reject_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("name must not be blank")
        return value


class LibraryUpdate(LibraryCreate):
    #: Optimistic concurrency: when supplied, a rename only applies if the
    #: caller saw the current state, so two editors cannot silently overwrite
    #: each other.
    expected_updated_at: datetime | None = None


def not_found() -> ProblemError:
    return ProblemError(
        code=ErrorCode.NOT_FOUND,
        title="Library not found",
        status_code=status.HTTP_404_NOT_FOUND,
        detail="No library with that identifier is available to you.",
    )


def summarize(library: Library, document_count: int) -> LibrarySummary:
    """Describe a library, including how much is in it.

    The count is a parameter rather than a default, because the web app
    prints it everywhere a library is named and a library that silently
    reported zero would look like one that had lost its documents.
    """
    return LibrarySummary(
        id=library.id,
        name=library.name,
        owner_user_id=library.owner_user_id,
        document_count=document_count,
        created_at=library.created_at,
        updated_at=library.updated_at,
    )


async def summarize_one(repository: LibraryRepository, library: Library) -> LibrarySummary:
    counts = await repository.document_counts([library.id])
    return summarize(library, counts[library.id])


@router.get("", summary="List the caller's libraries")
async def list_libraries(principal: CurrentPrincipal, session: Session) -> list[LibrarySummary]:
    repository = LibraryRepository(session)
    libraries = await repository.find_all(where=access.readable(principal.user_id))
    # One grouped query for the whole list rather than one per library: this
    # is the layout's load on every page, not a detail view.
    counts = await repository.document_counts([library.id for library in libraries])
    return [summarize(library, counts[library.id]) for library in libraries]


@router.post("", status_code=status.HTTP_201_CREATED, summary="Create a private library")
async def create_library(
    payload: LibraryCreate, principal: CurrentPrincipal, session: Session
) -> LibrarySummary:
    await UserRepository(session).ensure(principal)
    library = await LibraryRepository(session).create(
        name=payload.name, owner_user_id=principal.user_id
    )
    return summarize(library, document_count=0)


class LibraryDuplicate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    #: Optional. Left out, the copy is named after the original.
    name: str | None = Field(default=None, min_length=1, max_length=120)


@router.post(
    "/{library_id}/duplicate",
    status_code=status.HTTP_201_CREATED,
    summary="Copy a library into a new one",
)
async def duplicate_library(
    library_id: UUID,
    payload: LibraryDuplicate,
    background: BackgroundTasks,
    principal: CurrentPrincipal,
    session: Session,
    dispatcher: Dispatcher,
) -> LibrarySummary:
    """Copy a library so the two can diverge.

    The copy is owned by whoever asked for it, not by whoever owned the
    original: a copy of something you were allowed to read is yours, and
    leaving it owned by someone else would make it unmanageable by the only
    person who knows it exists.

    Its documents are queued for indexing like any upload, so the copy is
    browsable immediately and answerable once they finish. Nothing is
    published to the broker until the transaction commits.
    """
    await UserRepository(session).ensure(principal)
    repository = LibraryRepository(session)
    readable = access.readable(principal.user_id)
    source = await repository.get(library_id, where=readable)
    if source is None:
        raise not_found()

    duplication = await LibraryDuplicator(session).duplicate(
        source,
        name=payload.name or copy_name(source.name),
        owner_user_id=principal.user_id,
        where=readable,
    )
    for job in duplication.jobs:
        background.add_task(dispatcher.dispatch, StageName.PARSE, job.id)
    return await summarize_one(repository, duplication.library)


@router.get("/{library_id}", summary="Read one library")
async def read_library(
    library_id: UUID, principal: CurrentPrincipal, session: Session
) -> LibrarySummary:
    repository = LibraryRepository(session)
    library = await repository.get(library_id, where=access.readable(principal.user_id))
    if library is None:
        raise not_found()
    return await summarize_one(repository, library)


@router.patch("/{library_id}", summary="Rename a library")
async def rename_library(
    library_id: UUID, payload: LibraryUpdate, principal: CurrentPrincipal, session: Session
) -> LibrarySummary:
    repository = LibraryRepository(session)
    library = await repository.get(library_id, where=access.manageable(principal.user_id))
    if library is None:
        raise not_found()

    if (
        payload.expected_updated_at is not None
        and library.updated_at != payload.expected_updated_at
    ):
        raise ProblemError(
            code=ErrorCode.CONFLICT,
            title="Library changed since it was read",
            status_code=status.HTTP_409_CONFLICT,
            detail="Reload the library and reapply the change.",
        )

    return await summarize_one(repository, await repository.rename(library, payload.name))


@router.delete("/{library_id}", status_code=status.HTTP_204_NO_CONTENT, summary="Delete a library")
async def delete_library(
    library_id: UUID, principal: CurrentPrincipal, session: Session
) -> Response:
    repository = LibraryRepository(session)
    library = await repository.get(library_id, where=access.manageable(principal.user_id))
    if library is None:
        raise not_found()
    await repository.soft_delete(library)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


class LibraryShareCreate(BaseModel):
    """Who to share with, named the way the owner knows them."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    #: An email address rather than a user id. Sharing is a decision about a
    #: person, and an id is not something the person deciding can check.
    email: str = Field(min_length=3, max_length=320)


def summarize_share(grant: LibraryGrant, user: User) -> LibraryShare:
    return LibraryShare(
        user_id=user.id,
        email=user.email,
        display_name=user.display_name,
        created_at=grant.created_at,
    )


async def owned_library(library_id: UUID, principal_id: UUID, session: AsyncSession) -> Library:
    """The library, if the caller may change who can see it.

    `manageable`, not `readable`. Someone a library was shared with may read
    it and ask questions of it; they may not hand it on, and they may not
    see who else holds it - the list of people trusted with a private
    library is the owner's business.
    """
    library = await LibraryRepository(session).get(
        library_id, where=access.manageable(principal_id)
    )
    if library is None:
        raise not_found()
    return library


@router.get("/{library_id}/shares", summary="List who a library is shared with")
async def list_shares(
    library_id: UUID, principal: CurrentPrincipal, session: Session
) -> list[LibraryShare]:
    await owned_library(library_id, principal.user_id, session)
    grants = await LibraryGrantRepository(session).shared_with(library_id)
    return [summarize_share(grant, user) for grant, user in grants]


@router.post(
    "/{library_id}/shares",
    status_code=status.HTTP_201_CREATED,
    summary="Share a library with another user",
)
async def share_library(
    library_id: UUID, payload: LibraryShareCreate, principal: CurrentPrincipal, session: Session
) -> LibraryShare:
    """Give one other Primer user read access.

    Nothing is copied. The grantee reads the same library, the same
    documents and the same vectors the owner does - which is the whole point
    of sharing rather than duplicating, and why revoking is immediate: there
    is no second copy to go and find.

    Only someone who has used this deployment can be named. User rows are
    written when an identity first acts, so an address that matches nothing
    belongs either to a colleague who has not signed in yet or to a typo,
    and the two are worth telling apart out loud. That does disclose whether
    an address has an account here, to an authenticated user of the same
    deployment - which is the trade for the feature being usable at all, and
    is a much smaller disclosure than sharing with the wrong person.
    """
    await UserRepository(session).ensure(principal)
    await owned_library(library_id, principal.user_id, session)

    candidates = await UserRepository(session).find_by_email(payload.email)
    if not candidates:
        raise ProblemError(
            code=ErrorCode.NOT_FOUND,
            title="No such Primer user",
            status_code=status.HTTP_404_NOT_FOUND,
            detail=(
                "Nobody has signed in to this deployment with that address. "
                "They have to sign in once before a library can be shared with them."
            ),
        )
    if len(candidates) > 1:
        # The identity provider is asserting one address for several
        # accounts. Picking one would share a private library with whichever
        # happened to sort first, and nobody would notice until it mattered.
        raise ProblemError(
            code=ErrorCode.CONFLICT,
            title="That address matches more than one account",
            status_code=status.HTTP_409_CONFLICT,
            detail="Ask an administrator which account to share with; Primer will not guess.",
        )

    grantee = candidates[0]
    if grantee.id == principal.user_id:
        raise ProblemError(
            code=ErrorCode.VALIDATION_FAILED,
            title="That is your own account",
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="You already have access to your own library.",
        )

    grant = await LibraryGrantRepository(session).grant(
        library_id=library_id,
        grantee_user_id=grantee.id,
        granted_by_user_id=principal.user_id,
    )
    return summarize_share(grant, grantee)


@router.delete(
    "/{library_id}/shares/{user_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Stop sharing a library with someone",
)
async def revoke_share(
    library_id: UUID, user_id: UUID, principal: CurrentPrincipal, session: Session
) -> Response:
    """Take the access away, effective on the grantee's next request.

    There is nothing to clean up. Authorization reads the grant on every
    request, so a question already in flight finishes and the one after it
    is refused - and because sharing copied nothing, there is no stray index
    left holding the library's passages.
    """
    await owned_library(library_id, principal.user_id, session)
    repository = LibraryGrantRepository(session)
    grant = await repository.live(library_id, user_id)
    if grant is None:
        raise not_found()
    await repository.revoke(grant)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
