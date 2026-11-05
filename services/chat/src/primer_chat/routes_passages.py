"""A library's passages and graph coverage, for reading about rather than answering.

Lab scans a library for passages that read like injected instructions, and
reads them from here rather than for itself. Chat already asks Control what
a person may read and Retrieval what a library holds; giving Lab its own
way to the same answer would be a second path to every library, held by
the one service that runs arbitrary models. So Lab never names a library:
the web app reads the passages here, as the person, and hands Lab text.

A library's graph coverage is read here for the same reason: it is a
question about what a library holds, asked of Retrieval by the one service
that already asks Control first.
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

import httpx2
from fastapi import APIRouter, Query, Request, status
from primer_contracts.errors import ErrorCode
from primer_contracts.identity import Principal
from primer_contracts.indexing import (
    MAX_PASSAGES,
    GraphStatus,
    GraphStatusRequest,
    LibraryScope,
    PassagesRequest,
    PassagesResult,
)
from primer_service.durable import DurableRoute
from primer_service.errors import ProblemError

from primer_chat.clients import (
    LibraryAuthority,
    LibraryForbidden,
    PassageSource,
    SearchUnavailable,
)
from primer_chat.identity import CurrentPrincipal

router = APIRouter(prefix="/api/v1", tags=["passages"], route_class=DurableRoute)


def _unavailable(detail: str) -> ProblemError:
    return ProblemError(
        code=ErrorCode.DEPENDENCY_UNAVAILABLE,
        title="The library cannot be read just now",
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail=f"{detail} Try again shortly.",
    )


async def _readable_scope(request: Request, principal: Principal, library_id: UUID) -> LibraryScope:
    """The library's active generations, if this person may read it.

    Authorization before Retrieval, as for a question: a library the person
    may not read is never described, and says nothing about existing.
    """
    control: LibraryAuthority = request.app.state.control
    try:
        return await control.library_scope(principal, library_id)
    except LibraryForbidden:
        raise ProblemError(
            code=ErrorCode.NOT_FOUND,
            title="Library not found",
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No library with that identifier is available to you.",
        ) from None
    except httpx2.TransportError:
        raise _unavailable(
            "Control, which decides who may read it, could not be reached."
        ) from None


@router.get(
    "/libraries/{library_id}/passages",
    summary="A library's passages, in reading order",
    description=(
        "The passages of a library you can read, as they are stored, by file and then by "
        "position. At most `limit`; `total` says how many the library holds."
    ),
)
async def list_passages(
    library_id: UUID,
    principal: CurrentPrincipal,
    request: Request,
    limit: Annotated[int, Query(ge=1, le=MAX_PASSAGES)] = 500,
) -> PassagesResult:
    retrieval: PassageSource = request.app.state.retrieval
    scope = await _readable_scope(request, principal, library_id)
    if not scope.generation_ids:
        # Readable, and nothing indexed yet. Retrieval refuses an empty
        # scope rather than reading it as everything, so it is not asked.
        return PassagesResult()
    try:
        return await retrieval.passages(
            PassagesRequest(
                principal=principal,
                library_id=library_id,
                generation_ids=scope.generation_ids,
                limit=limit,
            )
        )
    except SearchUnavailable as failed:
        raise _unavailable(str(failed)) from None


@router.get(
    "/libraries/{library_id}/graph",
    summary="How much of a library the knowledge graph covers",
    description=(
        "Entity, relation, and topic counts for a library you can read, and which document "
        "versions have a graph. A searchable version with none was indexed with no "
        "extraction model, and gains one when it is reindexed."
    ),
)
async def graph_status(
    library_id: UUID, principal: CurrentPrincipal, request: Request
) -> GraphStatus:
    retrieval: PassageSource = request.app.state.retrieval
    scope = await _readable_scope(request, principal, library_id)
    if not scope.generation_ids:
        # Nothing indexed: no graph and no topics, said without asking a
        # Retrieval that refuses an empty scope.
        return GraphStatus(entities=0, relations=0, communities=0)
    try:
        return await retrieval.graph_status(
            GraphStatusRequest(
                principal=principal, library_id=library_id, generation_ids=scope.generation_ids
            )
        )
    except SearchUnavailable as failed:
        raise _unavailable(str(failed)) from None
