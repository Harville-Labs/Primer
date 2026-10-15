"""The graph store, reported as itself when it cannot be reached.

Erasing a document is the one thing that touches Neo4j today, and it is the
operation that must not quietly half-succeed: a delete that removed the
vectors and shrugged at the graph would report a document gone while the
names it contained stayed behind. So an unreachable graph fails the request
with a 503 that says which store was down, and the cleanup is retried.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from fastapi.testclient import TestClient
from primer_contracts.identity import Principal
from primer_retrieval.app import create_app
from primer_retrieval.config import Settings

SERVICE_TOKEN = "test-service-token"  # noqa: S105 - a fixture value, not a real credential


class EmptyStore:
    """A vector store holding nothing, so only the graph can fail."""

    def filter_documents(self, filters: dict[str, Any]) -> list[Any]:
        return []


def build_client(**settings: Any) -> TestClient:
    app = create_app(
        Settings(internal_api_token=SERVICE_TOKEN, embedding_dimensions=8, **settings),
        store=EmptyStore(),  # ty: ignore[invalid-argument-type]
        document_embedder=object(),  # ty: ignore[invalid-argument-type]
        text_embedder=object(),  # ty: ignore[invalid-argument-type]
        retriever=object(),
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


def test_without_a_graph_url_there_is_no_graph() -> None:
    client = build_client()
    assert client.app.state.retrieval.graph is None  # ty: ignore[unresolved-attribute]

    response = client.post(
        "/internal/v1/delete",
        json=delete_body(),
        headers={"X-Primer-Service-Token": SERVICE_TOKEN},
    )

    assert response.status_code == 200, response.text
