"""A dependency Retrieval needs, reported as itself rather than as a 500.

The embedder is a separate process that has to be running and reachable, so
this is the single most likely thing to be wrong on a self-hosted install.
It used to surface as an unhandled exception and a bare 500 with a stack
trace, which Chat then reported to the reader as an answer that stopped -
sending whoever read it to look at the model, which was fine.

Both directions matter. Searching a library and indexing a document go
through the same endpoint and fail the same way, and for a while only
searching said so: an embedder that was down during ingestion surfaced to
the user as a document whose stage "failed unexpectedly", which is the
message every unrelated bug also produces.

The store is the other half. Indexing embeds and then writes, and the write
was unguarded long after the embedding was: a store that refused reached the
user as "Retrieval answered 500", which says only that Retrieval was reached.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any

import pytest
from fastapi.testclient import TestClient
from primer_retrieval.app import create_app
from primer_retrieval.config import Settings

#: These routes are cluster-internal and take a service credential; the
#: value is only ever compared against itself.
SERVICE_TOKEN = "test-service-token"  # noqa: S105


class UnreachableEmbedder:
    """Fails the way an endpoint that is not there fails."""

    def run(self, *args: Any, **kwargs: Any) -> Any:
        raise ConnectionError("Connection error.")


@pytest.fixture
def client() -> TestClient:
    app = create_app(
        Settings(
            embedding_base_url="http://nothing-here:8080/v1",
            embedding_dimensions=384,
            internal_api_token=SERVICE_TOKEN,
        ),
        store=object(),  # ty: ignore[invalid-argument-type]
        document_embedder=UnreachableEmbedder(),
        text_embedder=UnreachableEmbedder(),
        retriever=object(),
    )
    return TestClient(app, raise_server_exceptions=False)


def search(client: TestClient) -> Any:
    return client.post(
        "/internal/v1/search",
        headers={"X-Primer-Service-Token": SERVICE_TOKEN},
        json={
            "principal": {
                "subject": "someone",
                "user_id": "11111111-1111-4111-8111-111111111111",
                "groups": [],
            },
            "library_id": "22222222-2222-4222-8222-222222222222",
            "generation_ids": ["33333333-3333-4333-8333-333333333333"],
            "query": "anything",
            "limit": 6,
        },
    )


def index(client: TestClient) -> Any:
    return client.post(
        "/internal/v1/index",
        headers={"X-Primer-Service-Token": SERVICE_TOKEN},
        json={
            "principal": {
                "subject": "someone",
                "user_id": "11111111-1111-4111-8111-111111111111",
                "groups": [],
            },
            "library_id": "22222222-2222-4222-8222-222222222222",
            "document_version_id": "44444444-4444-4444-8444-444444444444",
            "generation_id": "33333333-3333-4333-8333-333333333333",
            "chunks": [
                {
                    "chunk_id": "55555555-5555-4555-8555-555555555555",
                    "owner_user_id": "11111111-1111-4111-8111-111111111111",
                    "library_id": "22222222-2222-4222-8222-222222222222",
                    "document_id": "66666666-6666-4666-8666-666666666666",
                    "document_version_id": "44444444-4444-4444-8444-444444444444",
                    "generation_id": "33333333-3333-4333-8333-333333333333",
                    "ordinal": 0,
                    "content": "The budget rose.",
                    "embedding_text": "Finances. The budget rose.",
                    "filename": "report.pdf",
                    "locator": {"page": 1},
                }
            ],
        },
    )


def test_it_is_a_dependency_failure_rather_than_a_crash(client: TestClient) -> None:
    """503, not 500. Nothing is wrong with the request."""
    response = search(client)

    assert response.status_code == 503
    assert response.json()["code"] == "dependency_unavailable"


def test_the_reason_names_the_embedder(client: TestClient) -> None:
    """So the reader looks at the embedder rather than at the model."""
    detail = search(client).json()["detail"]

    assert "embedding" in detail.lower()


def test_no_stack_trace_reaches_the_caller(client: TestClient) -> None:
    """The trace belongs in the log, correlated by request id."""
    body = str(search(client).json())

    assert "Traceback" not in body
    assert "ConnectionError" not in body


def test_indexing_is_a_dependency_failure_rather_than_a_crash(client: TestClient) -> None:
    """A worker gets 503, which it can retry, rather than an opaque 500."""
    response = index(client)

    assert response.status_code == 503
    assert response.json()["code"] == "dependency_unavailable"


def test_the_indexing_reason_names_the_embedder(client: TestClient) -> None:
    """So the operator looks at the endpoint rather than at the document."""
    detail = index(client).json()["detail"]

    assert "embedding" in detail.lower()
    assert "indexed" in detail.lower()


class RefusingStore:
    """A document store that will not accept a write.

    Only `write_documents` fails: indexing embeds first, and a store that
    refused everything could not tell the two halves apart.
    """

    def __init__(self, message: str) -> None:
        self._message = message

    def write_documents(self, *args: Any, **kwargs: Any) -> int:
        raise RuntimeError(self._message)


class WorkingEmbedder:
    """Embeds without complaint, so the failure has to be the store."""

    def run(self, documents: Any, *args: Any, **kwargs: Any) -> Any:
        return {"documents": [replace(document, embedding=[0.0] * 384) for document in documents]}


def client_storing(store: Any) -> TestClient:
    app = create_app(
        Settings(
            embedding_base_url="http://nothing-here:8080/v1",
            embedding_dimensions=384,
            internal_api_token=SERVICE_TOKEN,
        ),
        store=store,
        document_embedder=WorkingEmbedder(),
        text_embedder=UnreachableEmbedder(),
        retriever=object(),
    )
    return TestClient(app, raise_server_exceptions=False)


def test_a_store_that_refuses_a_write_is_named() -> None:
    """Not a bare 500, which says only that Retrieval was reached."""
    response = index(client_storing(RefusingStore("connection pool exhausted")))

    assert response.status_code == 503
    assert response.json()["code"] == "dependency_unavailable"
    assert "vector store" in response.json()["detail"]


#: What Qdrant and its integration say, verbatim, when the collection holds
#: 1024-dimensional vectors and this deployment is configured for 384. The
#: first is the integration refusing to open the collection, which is what a
#: deployment that swapped models meets; the second is Qdrant refusing a
#: point, which is what an endpoint that ignores `dimensions` produces.
WIDTH_MISMATCHES = [
    "Collection 'primer_chunks' already exists in Qdrant, but it is configured with a "
    "vector size '1024'. If you want to use that collection, but with a different vector "
    "size, please set `recreate_collection=True` argument.",
    "Unexpected Response: 400 (Bad Request)\nRaw response content:\n"
    'b\'{"status":{"error":"Wrong input: Vector dimension error: expected dim: 1024, '
    'got 384"},"time":0.000028417}\'',
]


@pytest.mark.parametrize("message", WIDTH_MISMATCHES)
def test_a_width_mismatch_says_what_to_do_about_it(message: str) -> None:
    """The one failure the chart warns about and cannot check for itself.

    A collection keeps the width it was created with, so a deployment that
    swaps embedding models looks healthy until the first document is
    indexed - and then fails with a message about vector sizes that means
    nothing unless you already know this.
    """
    response = index(client_storing(RefusingStore(message)))
    detail = response.json()["detail"]

    assert response.status_code == 409
    assert response.json()["code"] == "conflict"
    assert "384" in detail and "1024" in detail
    assert "reindex" in detail


@pytest.mark.parametrize("message", WIDTH_MISMATCHES)
def test_a_width_mismatch_is_not_retried(message: str) -> None:
    """Every retry is refused identically, and spends the budget for nothing.

    A 4xx is what tells the worker to stop: nothing is down here, and
    nothing is coming back without an operator changing something.
    """
    response = index(client_storing(RefusingStore(message)))

    assert 400 <= response.status_code < 500
