"""What Chat needs from the services it does not own.

Chat decides nothing about access on its own. It asks Control what the
principal may read, and Retrieval what that library contains. Both are
protocols so a test can drive the real orchestration without a cluster.
"""

from __future__ import annotations

from types import TracebackType
from typing import Protocol
from uuid import UUID

import httpx2
from primer_contracts.errors import ErrorCode
from primer_contracts.identity import Principal
from primer_contracts.indexing import (
    LibraryAccessRequest,
    LibraryScope,
    PassagesRequest,
    PassagesResult,
    SearchRequest,
    SearchResult,
)

from primer_chat.config import Settings

SERVICE_TOKEN_HEADER = "X-Primer-Service-Token"  # noqa: S105 - a header name, not a secret


class LibraryAuthority(Protocol):
    """Control, which is the only place access is decided."""

    async def library_scope(self, principal: Principal, library_id: UUID) -> LibraryScope: ...


class PassageSource(Protocol):
    """Retrieval, which is the only thing that touches a vector store."""

    async def search(self, request: SearchRequest) -> SearchResult: ...

    async def passages(self, request: PassagesRequest) -> PassagesResult: ...


class SearchFailed(Exception):
    """A library could not be searched, in Retrieval's own words.

    Distinct from a failed answer: nothing was wrong with the model. Each
    kind carries the code the stream reports it under, so the user is told
    which of the two it was and the stream handles both in one place.
    """

    code: str


class SearchUnavailable(SearchFailed):
    """Retrieval could not search: a dependency is down. Worth asking again."""

    code = "search_unavailable"


class SearchRefused(SearchFailed):
    """Retrieval will not search this way: a library too large to ask across.

    Asking again gets the same answer, so it is reported as a refusal rather
    than an outage, with Retrieval's sentence saying what to do instead.
    """

    code = "search_refused"


def _problem_of(response: httpx2.Response) -> dict[str, object]:
    try:
        body = response.json()
    except Exception:  # noqa: BLE001 - a body that is not JSON says nothing
        return {}
    return body if isinstance(body, dict) else {}


def _detail_of(response: httpx2.Response) -> str:
    """Retrieval's own words, when it sent any."""
    detail = _problem_of(response).get("detail")
    return str(detail) if detail else "This library could not be searched just now."


class LibraryForbidden(Exception):
    """The principal may not read this library, or it does not exist.

    One exception for both, because the API must not distinguish them.
    """


class _Client:
    def __init__(self, base_url: str, settings: Settings) -> None:
        token = settings.service_token
        self._client = httpx2.AsyncClient(
            base_url=base_url.rstrip("/"),
            timeout=settings.request_timeout_seconds,
            headers=({SERVICE_TOKEN_HEADER: token.get_secret_value()} if token is not None else {}),
        )

    async def __aenter__(self) -> _Client:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        await self._client.aclose()

    async def aclose(self) -> None:
        await self._client.aclose()


class ControlClient(_Client):
    def __init__(self, settings: Settings) -> None:
        super().__init__(settings.control_url, settings)

    async def library_scope(self, principal: Principal, library_id: UUID) -> LibraryScope:
        response = await self._client.post(
            "/internal/v1/authz/library-scope",
            json=LibraryAccessRequest(principal=principal, library_id=library_id).model_dump(
                mode="json"
            ),
        )
        if response.status_code == 404:
            raise LibraryForbidden(str(library_id))
        response.raise_for_status()
        return LibraryScope.model_validate(response.json())


class RetrievalClient(_Client):
    def __init__(self, settings: Settings) -> None:
        super().__init__(settings.retrieval_url, settings)

    async def search(self, request: SearchRequest) -> SearchResult:
        response = await self._client.post(
            "/internal/v1/search", json=request.model_dump(mode="json")
        )
        _raise_for(response)
        return SearchResult.model_validate(response.json())

    async def passages(self, request: PassagesRequest) -> PassagesResult:
        try:
            response = await self._client.post(
                "/internal/v1/passages", json=request.model_dump(mode="json")
            )
        except httpx2.TransportError as error:
            raise SearchUnavailable("Retrieval could not be reached.") from error
        _raise_for(response)
        return PassagesResult.model_validate(response.json())


def _raise_for(response: httpx2.Response) -> None:
    """Name Retrieval's refusals for what they are, and fail on anything else."""
    if response.status_code == 503:
        # Retrieval says which of its own dependencies is down, and that
        # sentence is worth more to whoever reads it than anything this
        # service could invent. Carried through rather than flattened to
        # "the answer stopped", which points at the model instead.
        raise SearchUnavailable(_detail_of(response))
    if response.status_code == 422 and _problem_of(response).get("code") == (
        ErrorCode.QUOTA_EXCEEDED
    ):
        # By code as well as status: any other 422 is a request Chat
        # built wrongly, which is a bug here and should fail as one.
        raise SearchRefused(_detail_of(response))
    response.raise_for_status()
