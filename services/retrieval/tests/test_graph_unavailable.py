"""The graph store, reported as itself when it cannot be reached.

Erasing a document must not quietly half-succeed: a delete that removed the
vectors and shrugged at the graph would report a document gone while the
names it contained stayed behind. So an unreachable graph fails the request
with a 503 that says which store was down, and the cleanup is retried.

Search is the opposite. The vector results are a correct answer without the
graph, so an unreachable graph makes search worse, not broken.
"""

from __future__ import annotations

import time
import uuid
from typing import Any

import pytest
from fastapi.testclient import TestClient
from haystack import Document
from primer_contracts.identity import Principal
from primer_retrieval.app import create_app
from primer_retrieval.config import Settings
from pydantic import ValidationError

SERVICE_TOKEN = "test-service-token"  # noqa: S105 - a fixture value, not a real credential


class EmptyStore:
    """A vector store holding nothing, so only the graph can fail."""

    def count_documents_by_filter(self, filters: dict[str, Any]) -> int:
        return 0

    def delete_by_filter(self, filters: dict[str, Any]) -> int:
        return 0


class FixedEmbedder:
    def run(self, text: str) -> dict[str, Any]:
        return {"embedding": [0.1] * 8}


class OneHitRetriever:
    """A vector search that always finds the same passage."""

    def __init__(self) -> None:
        self.document = Document(
            id=str(uuid.uuid4()),
            content="Marie Curie discovered polonium.",
            score=0.7,
            meta={
                "library_id": str(uuid.uuid4()),
                "document_id": str(uuid.uuid4()),
                "document_version_id": str(uuid.uuid4()),
                "generation_id": str(uuid.uuid4()),
            },
        )

    def run(self, **kwargs: Any) -> dict[str, Any]:
        return {"documents": [self.document]}


def build_client(**settings: Any) -> TestClient:
    app = create_app(
        Settings(
            internal_api_token=SERVICE_TOKEN,
            worker_token=SERVICE_TOKEN,
            embedding_dimensions=8,
            **settings,
        ),
        store=EmptyStore(),  # ty: ignore[invalid-argument-type]
        verify_embedder=lambda _consequence: None,
        document_embedder=object(),  # ty: ignore[invalid-argument-type]
        text_embedder=FixedEmbedder(),
        retriever=OneHitRetriever(),
    )
    return TestClient(app, raise_server_exceptions=False)


def delete_body() -> dict[str, Any]:
    reader = Principal(subject="reader", user_id=uuid.uuid4())
    return {
        "principal": reader.model_dump(mode="json"),
        "library_id": str(uuid.uuid4()),
        "document_version_id": str(uuid.uuid4()),
        "generation_id": str(uuid.uuid4()),
    }


@pytest.mark.parametrize("route", ["/internal/v1/delete", "/internal/v1/purge"])
def test_an_unreachable_graph_fails_erasure_by_name(route: str) -> None:
    # Port 1 refuses at once, so this is an outage without a wait.
    client = build_client(graph_url="bolt://127.0.0.1:1", graph_timeout_seconds=2)
    body = delete_body()
    if route.endswith("purge"):
        body.pop("generation_id")

    response = client.post(route, json=body, headers={"X-Primer-Service-Token": SERVICE_TOKEN})

    assert response.status_code == 503, response.text
    assert "graph store could not be reached" in response.json()["detail"]


@pytest.mark.parametrize("missing", ["PRIMER_GRAPH_URL", "PRIMER_GRAPH_PASSWORD"])
def test_retrieval_without_a_graph_refuses_to_start(
    missing: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Every deployment has a graph (ADR 0014).

    Starting without one would search on from the vectors alone, and the
    only symptom would be answers that never follow a relation.
    """
    monkeypatch.delenv(missing)

    with pytest.raises(ValidationError) as raised:
        Settings(internal_api_token=SERVICE_TOKEN, worker_token=SERVICE_TOKEN)

    assert missing.removeprefix("PRIMER_").lower() in str(raised.value)


def test_an_unreachable_graph_leaves_search_answering_from_vectors() -> None:
    """Through the real driver, so the error a refused connection raises is
    the one search knows to search past. The vectors are a correct answer on
    their own, so waiting the driver's full transaction retry budget before
    falling back to them is not useful to the searcher."""
    client = build_client(graph_url="bolt://127.0.0.1:1", graph_timeout_seconds=2)
    body = delete_body()
    body.pop("document_version_id")
    body["generation_ids"] = [body.pop("generation_id")]

    started = time.monotonic()
    response = client.post(
        "/internal/v1/search",
        json={**body, "query": "Who did Marie Curie work with?"},
        headers={"X-Primer-Service-Token": SERVICE_TOKEN},
    )
    elapsed = time.monotonic() - started

    assert response.status_code == 200, response.text
    assert [c["content"] for c in response.json()["chunks"]] == ["Marie Curie discovered polonium."]
    assert elapsed < 10, f"vector fallback took {elapsed:.1f}s with an unavailable graph"
