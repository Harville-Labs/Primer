"""The Retrieval service against a real Qdrant.

The conformance suite proves the store meets the isolation contract. This proves
the service wired on top of one behaves: that routes filter, count, and
delete the way the endpoints promise.
"""

from __future__ import annotations

import hashlib
import math
import re
from collections.abc import Iterator
from dataclasses import replace
from typing import Any

import pytest
from fastapi.testclient import TestClient
from haystack import Document
from primer_retrieval.app import create_app
from primer_retrieval.config import Settings
from primer_retrieval.graph import GraphStore, build_graph_store
from testcontainers.community.neo4j import Neo4jContainer
from testcontainers.community.qdrant import QdrantContainer

QDRANT_IMAGE = "qdrant/qdrant:v1.19.0"
NEO4J_IMAGE = "neo4j:5.26-community"
SERVICE_TOKEN = "retrieval-service-token"  # noqa: S105 - a fixture value, not a real credential
DIMENSIONS = 32

WORD = re.compile(r"[a-z0-9]+")


def embed(text: str) -> list[float]:
    """Deterministic and offline; see tests/vector_conformance for the rationale."""
    vector = [0.0] * DIMENSIONS
    for word in WORD.findall(text.lower()):
        vector[hashlib.sha256(word.encode()).digest()[0] % DIMENSIONS] += 1.0
    norm = math.sqrt(sum(value * value for value in vector))
    if norm == 0:
        return [1.0 / math.sqrt(DIMENSIONS)] * DIMENSIONS
    return [value / norm for value in vector]


class StubDocumentEmbedder:
    def run(self, documents: list[Document]) -> dict[str, Any]:
        return {"documents": [replace(d, embedding=embed(d.content or "")) for d in documents]}


class StubTextEmbedder:
    def run(self, text: str) -> dict[str, Any]:
        return {"embedding": embed(text)}


@pytest.fixture(scope="session")
def qdrant_url() -> Iterator[str]:
    """One Qdrant for the session; each test gets its own collection in it."""
    with QdrantContainer(QDRANT_IMAGE) as container:
        yield f"http://{container.get_container_host_ip()}:{container.get_exposed_port(6333)}"


@pytest.fixture(scope="session")
def neo4j() -> Iterator[Neo4jContainer]:
    """One Neo4j for the session, its schema applied the way a deployment's is.

    Shared rather than one per test because every graph test writes into
    libraries it made up, and isolation between libraries is the property
    under test - a fresh database per test would hide a leak between them.
    """
    with Neo4jContainer(NEO4J_IMAGE) as container:
        store = build_graph_store(graph_settings(container))
        assert store is not None
        store.apply_schema()
        store.close()
        yield container


def graph_settings(container: Neo4jContainer, **overrides: Any) -> Settings:
    return Settings(
        graph_url=container.get_connection_url(),
        graph_username=container.username,
        graph_password=container.password,
        **overrides,
    )


@pytest.fixture
def client(qdrant_url: str, request: pytest.FixtureRequest) -> TestClient:
    """A service with its own collection, so no test can see another's writes."""
    unique = abs(hash(request.node.nodeid)) % 10**9
    settings = Settings(
        qdrant_url=qdrant_url,
        qdrant_index=f"service_{unique}",
        embedding_dimensions=DIMENSIONS,
        internal_api_token=SERVICE_TOKEN,
    )
    app = create_app(
        settings, document_embedder=StubDocumentEmbedder(), text_embedder=StubTextEmbedder()
    )
    return TestClient(app)


@pytest.fixture
def neo4j_settings(neo4j: Neo4jContainer) -> Settings:
    """Settings naming the session's Neo4j, for code that builds its own store."""
    return graph_settings(neo4j)


@pytest.fixture
def graph(neo4j: Neo4jContainer) -> Iterator[GraphStore]:
    store = build_graph_store(graph_settings(neo4j))
    assert store is not None
    yield store
    store.close()


@pytest.fixture
def graph_client(qdrant_url: str, neo4j: Neo4jContainer, request: pytest.FixtureRequest):
    """The service with both stores, the way a graph deployment runs it."""
    unique = abs(hash(request.node.nodeid)) % 10**9
    settings = graph_settings(
        neo4j,
        qdrant_url=qdrant_url,
        qdrant_index=f"service_{unique}",
        embedding_dimensions=DIMENSIONS,
        internal_api_token=SERVICE_TOKEN,
    )
    app = create_app(
        settings, document_embedder=StubDocumentEmbedder(), text_embedder=StubTextEmbedder()
    )
    with TestClient(app) as client:
        yield client
    graph = app.state.retrieval.graph
    graph.close()
