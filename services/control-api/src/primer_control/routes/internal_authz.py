"""Authorization answers for other Primer services.

Cluster-internal, and separate from the ingestion routes because these do
take a principal: a worker acts on a job, but Chat acts on behalf of a
person and has to be told what that person may read.

Control answers these questions rather than exporting its rules, so there is
exactly one implementation of who may see what. A service that decided
locally would be a second copy to drift.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, status
from primer_contracts.errors import ErrorCode
from primer_contracts.indexing import LibraryAccessRequest, LibraryScope
from primer_service.credentials import require_credential
from primer_service.db import get_session
from primer_service.errors import ProblemError
from sqlalchemy.ext.asyncio import AsyncSession

from primer_control.repositories.ingestion_jobs import IngestionJobRepository
from primer_control.repositories.libraries import LibraryRepository
from primer_control.services.library_access import LibraryAccess

router = APIRouter(
    prefix="/internal/v1/authz",
    tags=["internal"],
    dependencies=[Depends(require_credential("internal_api_token"))],
    include_in_schema=False,
)

Session = Annotated[AsyncSession, Depends(get_session)]
access = LibraryAccess()


@router.post("/library-scope", summary="Authorize a library and return its search scope")
async def library_scope(payload: LibraryAccessRequest, session: Session) -> LibraryScope:
    """Decide access and search scope together, from the same rows.

    Chat calls this before retrieving anything, so a library the principal
    cannot read is 404 here - which is what stops an unauthorized question
    from ever reaching Retrieval or a model.

    Permission and scope are one answer because they come from the same
    query. Asking separately would leave a window in which a library became
    readable, or stopped being, between the two calls.

    An empty generation list is a real answer, not an error: a library whose
    documents are still being indexed is readable and has nothing to search.
    """
    library = await LibraryRepository(session).get(
        payload.library_id, where=access.readable(payload.principal.user_id)
    )
    if library is None:
        raise ProblemError(
            code=ErrorCode.NOT_FOUND,
            title="Library not found",
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No library with that identifier is available to that principal.",
        )
    generations = await IngestionJobRepository(session).active_generations(payload.library_id)
    return LibraryScope(library_id=payload.library_id, generation_ids=tuple(generations))
