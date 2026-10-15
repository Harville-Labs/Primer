"""Search with the graph beside the vectors, against both real stores."""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from fastapi.testclient import TestClient
from primer_contracts.graph import ChunkGraph, GraphEntity, GraphRelation
from primer_contracts.identity import Principal

TOKEN_HEADER = {"X-Primer-Service-Token": "retrieval-service-token"}
QUESTION = "What did Marie Curie discover?"
LIMIT = 8


def scope(library: uuid.UUID, version: uuid.UUID, generation: uuid.UUID) -> dict[str, Any]:
    reader = Principal(subject="reader", user_id=uuid.uuid5(uuid.NAMESPACE_URL, "reader"))
    return {
        "principal": reader.model_dump(mode="json"),
        "library_id": str(library),
        "document_version_id": str(version),
        "generation_id": str(generation),
    }


def passage(body: dict[str, Any], ordinal: int, content: str) -> dict[str, Any]:
    return {
        "chunk_id": str(uuid.uuid4()),
        "ordinal": ordinal,
        "library_id": body["library_id"],
        "document_id": str(uuid.uuid4()),
        "document_version_id": body["document_version_id"],
        "owner_user_id": str(uuid.uuid4()),
        "generation_id": body["generation_id"],
        "content": content,
        "embedding_text": content,
        "filename": "notes.pdf",
    }


def names(chunk_id: str, *entities: str, relations: tuple[GraphRelation, ...] = ()):
    return ChunkGraph(
        chunk_id=uuid.UUID(chunk_id),
        entities=tuple(GraphEntity(name=name, type="person") for name in entities),
        relations=relations,
    ).model_dump(mode="json")


def found(client: TestClient, body: dict[str, Any]) -> list[str]:
    response = client.post(
        "/internal/v1/search",
        json={
            "principal": body["principal"],
            "library_id": body["library_id"],
            "generation_ids": [body["generation_id"]],
            "query": QUESTION,
            "limit": LIMIT,
        },
        headers=TOKEN_HEADER,
    )
    assert response.status_code == 200, response.text
    return [chunk["content"] for chunk in response.json()["chunks"]]


def test_a_passage_two_steps_from_the_question_is_found_through_the_graph(
    graph_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The case vector search cannot reach: a passage about someone the
    question never names, connected to it only by another passage that names
    both. More passages share the question's words than the limit allows, so
    without the graph it is crowded out."""
    body = scope(uuid.uuid4(), uuid.uuid4(), uuid.uuid4())
    mentions = passage(body, 0, "Marie Curie discovered polonium.")
    bridge = passage(body, 1, "She married Pierre Curie in 1895.")
    distant = passage(body, 2, "Piezoelectricity in quartz crystals was his early work.")
    crowd = [
        passage(body, 3 + n, f"What did they discover, Marie asked, in study {n}?")
        for n in range(LIMIT + 4)
    ]
    chunks = [mentions, bridge, distant, *crowd]
    indexed = graph_client.post(
        "/internal/v1/index", json={**body, "chunks": chunks}, headers=TOKEN_HEADER
    )
    assert indexed.status_code == 200, indexed.text
    graphs = [
        names(mentions["chunk_id"], "Marie Curie"),
        names(
            bridge["chunk_id"],
            "Marie Curie",
            "Pierre Curie",
            relations=(
                GraphRelation(source="Marie Curie", target="Pierre Curie", label="married"),
            ),
        ),
        names(distant["chunk_id"], "Pierre Curie"),
    ]
    written = graph_client.post(
        "/internal/v1/graph/index", json={**body, "graphs": graphs}, headers=TOKEN_HEADER
    )
    assert written.status_code == 200, written.text

    with_graph = found(graph_client, body)
    state = graph_client.app.state.retrieval  # ty: ignore[unresolved-attribute]
    monkeypatch.setattr(state, "graph", None)
    without_graph = found(graph_client, body)

    assert len(without_graph) == LIMIT
    assert distant["content"] not in without_graph
    assert distant["content"] in with_graph
    assert len(with_graph) == LIMIT
