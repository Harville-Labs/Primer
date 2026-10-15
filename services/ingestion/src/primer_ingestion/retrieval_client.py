"""The worker's view of the Retrieval service.

Workers never touch a vector store. They hand chunks to Retrieval, which is
the only process that knows what backend is deployed or how it filters.

Everything that can go wrong on the wire is turned into a `StageError`
here, at the one place that makes the call. A stage that let an HTTP error
escape reported it through the catch-all in `tasks`, which is deliberately
sanitized - so an embedding endpoint that was refusing connections reached
the user as "The stage failed unexpectedly", the same words as a genuine
bug in Primer, and told whoever read it nothing about where to look.
"""

from __future__ import annotations

from types import TracebackType
from typing import Protocol
from uuid import UUID

import httpx2
from primer_contracts.identity import Principal
from primer_contracts.indexing import (
    DeleteRequest,
    DeleteResult,
    GenerationCount,
    GenerationQuery,
    GraphIndexRequest,
    GraphIndexResult,
    IndexRequest,
    IndexResult,
    PurgeRequest,
)

from primer_ingestion.config import Settings
from primer_ingestion.errors import PermanentStageError, StageError

SERVICE_TOKEN_HEADER = "X-Primer-Service-Token"  # noqa: S105 - a header name, not a secret


class VectorIndex(Protocol):
    """What a stage needs from Retrieval, so a test can supply it."""

    def index(self, request: IndexRequest) -> IndexResult: ...

    def index_graph(self, request: GraphIndexRequest) -> GraphIndexResult: ...

    def verify(self, request: GenerationQuery) -> GenerationCount: ...

    def delete(self, request: DeleteRequest) -> DeleteResult: ...

    def purge(self, request: PurgeRequest) -> DeleteResult: ...


class RetrievalClient:
    def __init__(self, settings: Settings, client: httpx2.Client | None = None) -> None:
        token = settings.retrieval_token or settings.service_token
        self._client = client or httpx2.Client(
            base_url=settings.retrieval_url.rstrip("/"),
            # Generous, because embedding a batch is the slow part and a
            # timeout here costs a whole batch of work.
            timeout=settings.retrieval_timeout_seconds,
            headers=({SERVICE_TOKEN_HEADER: token.get_secret_value()} if token is not None else {}),
        )

    def __enter__(self) -> RetrievalClient:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self._client.close()

    def _post(self, path: str, payload: object) -> dict[str, object]:
        try:
            response = self._client.post(path, json=payload)
            response.raise_for_status()
        except httpx2.HTTPStatusError as error:
            raise _refused(error.response) from error
        except httpx2.HTTPError as error:
            # Nothing answered: a refused connection, a timeout, a name that
            # does not resolve. Worth retrying, and worth saying which
            # service was not there.
            raise StageError("retrieval_unavailable", "Retrieval could not be reached.") from error
        body: dict[str, object] = response.json()
        return body

    def index(self, request: IndexRequest) -> IndexResult:
        return IndexResult.model_validate(
            self._post("/internal/v1/index", request.model_dump(mode="json"))
        )

    def index_graph(self, request: GraphIndexRequest) -> GraphIndexResult:
        return GraphIndexResult.model_validate(
            self._post("/internal/v1/graph/index", request.model_dump(mode="json"))
        )

    def verify(self, request: GenerationQuery) -> GenerationCount:
        return GenerationCount.model_validate(
            self._post("/internal/v1/verify", request.model_dump(mode="json"))
        )

    def delete(self, request: DeleteRequest) -> DeleteResult:
        return DeleteResult.model_validate(
            self._post("/internal/v1/delete", request.model_dump(mode="json"))
        )

    def purge(self, request: PurgeRequest) -> DeleteResult:
        return DeleteResult.model_validate(
            self._post("/internal/v1/purge", request.model_dump(mode="json"))
        )


def _refused(response: httpx2.Response) -> StageError:
    """Turn a status Retrieval returned into a failure a stage can report.

    Server errors and rate limits are retried; anything else in the 4xx
    range is not. A request Retrieval refuses on its merits - a malformed
    chunk, a scope it will not accept - is refused identically every time,
    and retrying it only delays the moment someone is told.

    Retrieval's own explanation is carried through. It is Primer's words
    rather than a model's or a user's, it is already written for whoever
    has to fix it, and it is the difference between knowing the embedding
    endpoint is down and knowing only that something went wrong.
    """
    detail = _explanation(response) or f"Retrieval answered {response.status_code}."
    if response.status_code >= 500 or response.status_code == httpx2.codes.TOO_MANY_REQUESTS:
        return StageError("retrieval_unavailable", detail)
    return PermanentStageError("retrieval_rejected", detail)


def _explanation(response: httpx2.Response) -> str | None:
    """The `detail` of a problem document, when that is what came back."""
    try:
        body = response.json()
    except ValueError:
        return None
    if not isinstance(body, dict):
        return None
    detail = body.get("detail")
    return detail if isinstance(detail, str) else None


def worker_principal(owner_user_id: UUID) -> Principal:
    """Audit context for work done on a user's behalf, not authorization.

    The document's owner is named because that is whose data is being
    handled; the worker is not acting as them, and Retrieval does not treat
    this as permission to read anything.
    """
    return Principal(subject=f"ingestion-worker:{owner_user_id}", user_id=owner_user_id)
