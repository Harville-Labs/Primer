"""Library-wide questions through the search route, with fake stores.

What matters is what reaches the answer: one passage from each topic rather
than the nearest few, nothing the caller's scope does not cover, and a
focused answer, not an error, whenever the graph cannot help.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from fastapi.testclient import TestClient
from haystack import Document
from neo4j.exceptions import ServiceUnavailable
from primer_contracts.identity import Principal
from primer_retrieval.app import create_app
from primer_retrieval.breadth import Communities
from primer_retrieval.config import Settings

SERVICE_TOKEN = "test-service-token"  # noqa: S105 - a fixture value, not a real credential
LIBRARY = uuid.uuid4()
GENERATION = uuid.uuid4()


def passage(content: str, score: float) -> Document:
    return Document(
        id=str(uuid.uuid4()),
        content=content,
        score=score,
        meta={
            "library_id": str(LIBRARY),
            "document_id": str(uuid.uuid4()),
            "document_version_id": str(uuid.uuid4()),
            "generation_id": str(GENERATION),
        },
    )


#: Three chemistry passages that resemble the question and one astronomy
#: passage that does not. A focused search never reaches the astronomy.
CHEMISTRY = [passage(f"Polonium, part {i}.", 0.9 - i / 100) for i in range(3)]
ASTRONOMY = passage("Comets have elliptical orbits.", 0.1)
#: Named by the graph, absent from the scoped store: another library's.
FOREIGN = str(uuid.uuid4())


class Retriever:
    """In scope is whatever is in the pool; a read by id returns only those."""

    def __init__(self) -> None:
        self.pool = [*CHEMISTRY, ASTRONOMY]

    def run(self, query_embedding: Any, filters: dict[str, Any], top_k: int) -> dict[str, Any]:
        named = [c["value"] for c in filters["conditions"] if c["field"] == "id"]
        if named:
            return {"documents": [d for d in self.pool if d.id in named[0]]}
        ranked = sorted(self.pool, key=lambda d: -(d.score or 0.0))
        return {"documents": ranked[:top_k]}


class Graph:
    """Records each clustering read, so a test can prove the cache was used."""

    def __init__(self, fail: bool = False) -> None:
        self.fail = fail
        self.reads: list[tuple[uuid.UUID, tuple[uuid.UUID, ...], int]] = []

    def mentions(
        self, library_id: uuid.UUID, generation_ids: tuple[uuid.UUID, ...], limit: int
    ) -> list[tuple[str, str]]:
        self.reads.append((library_id, generation_ids, limit))
        if self.fail:
            raise ServiceUnavailable("graph down")
        chemistry = [(d.id, e) for d in CHEMISTRY for e in ("polonium", "radium")]
        return [*chemistry, (ASTRONOMY.id, "comet"), (FOREIGN, "comet"), (FOREIGN, "orbit")]

    def neighbourhood(self, *args: Any, **kwargs: Any) -> list[Any]:
        return []


class Embedder:
    def run(self, text: str) -> dict[str, Any]:
        return {"embedding": [0.1] * 8}


def build_client(graph: Graph | None, **settings: Any) -> TestClient:
    configured = Settings(internal_api_token=SERVICE_TOKEN, embedding_dimensions=8, **settings)
    app = create_app(
        configured,
        store=object(),  # ty: ignore[invalid-argument-type]
        document_embedder=object(),  # ty: ignore[invalid-argument-type]
        text_embedder=Embedder(),
        retriever=Retriever(),
    )
    state = app.state.retrieval
    state.graph = graph
    state.communities = Communities(graph, configured) if graph else None  # ty: ignore[invalid-argument-type]
    return TestClient(app, raise_server_exceptions=False)


def ask(client: TestClient, breadth: str, generations: list[uuid.UUID] | None = None) -> Any:
    reader = Principal(subject="reader", user_id=uuid.uuid4())
    return client.post(
        "/internal/v1/search",
        json={
            "principal": reader.model_dump(mode="json"),
            "library_id": str(LIBRARY),
            "generation_ids": [str(g) for g in generations or [GENERATION]],
            "query": "What are the main themes of this library?",
            "limit": 2,
            "breadth": breadth,
        },
        headers={"X-Primer-Service-Token": SERVICE_TOKEN},
    )


def contents(response: Any) -> list[str]:
    assert response.status_code == 200, response.text
    return [chunk["content"] for chunk in response.json()["chunks"]]


def test_a_library_wide_question_reads_every_topic_not_the_nearest_one() -> None:
    """The floor would drop the astronomy passage; a library-wide question skips it."""
    client = build_client(Graph(), min_score=0.5)

    assert contents(ask(client, "focused")) == ["Polonium, part 0.", "Polonium, part 1."]
    assert contents(ask(client, "library")) == [
        "Polonium, part 0.",
        "Comets have elliptical orbits.",
    ]


def test_a_passage_the_graph_names_outside_the_callers_scope_is_not_read() -> None:
    """FOREIGN shares a community with the astronomy passage and is never returned."""
    client = build_client(Graph())

    chunk_ids = {chunk["chunk_id"] for chunk in ask(client, "library").json()["chunks"]}

    assert chunk_ids
    assert FOREIGN not in chunk_ids


@pytest.mark.parametrize("graph", [None, Graph(fail=True)], ids=["no graph", "graph down"])
def test_without_a_working_graph_a_library_wide_question_gets_the_focused_answer(
    graph: Graph | None,
) -> None:
    client = build_client(graph)

    assert contents(ask(client, "library")) == contents(ask(client, "focused"))


def test_a_library_over_the_limit_is_refused_by_name() -> None:
    """Not quietly answered from one corner: the user asked about all of it."""
    graph = Graph()
    client = build_client(graph, graph_community_max_mentions=3)

    response = ask(client, "library")

    assert response.status_code == 422, response.text
    assert response.json()["code"] == "quota_exceeded"
    assert "PRIMER_GRAPH_COMMUNITY_MAX_MENTIONS" in response.json()["detail"]
    assert graph.reads[0][2] == 4, "one past the limit, so a library at it is still answered"


def test_a_library_is_clustered_once_until_its_generations_change() -> None:
    graph = Graph()
    client = build_client(graph)
    rebuilt = uuid.uuid4()

    ask(client, "library")
    ask(client, "library")
    ask(client, "library", [GENERATION, rebuilt])

    assert [read[1] for read in graph.reads] == [
        (GENERATION,),
        tuple(sorted([GENERATION, rebuilt])),
    ]


def test_a_focused_question_never_clusters() -> None:
    graph = Graph()

    ask(build_client(graph), "focused")

    assert graph.reads == []
