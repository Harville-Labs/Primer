"""The Retrieval service against a real pgvector.

The conformance suite proves each backend meets the contract. This proves
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
from primer_retrieval.migrations import upgrade_to_head
from testcontainers.community.neo4j import Neo4jContainer
from testcontainers.community.postgres import PostgresContainer

POSTGRES_IMAGE = "pgvector/pgvector:pg17"
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
def pgvector_url() -> Iterator[str]:
    """A database prepared the way a deployment prepares one.

    The migration runs here rather than the extension being created inline,
    because installing it is exactly what the migration is for: a test that
    set the database up its own way would keep passing on the day the
    migration stopped doing it.
    """
    with PostgresContainer(POSTGRES_IMAGE) as container:
        url = container.get_connection_url(driver=None)
        upgrade_to_head(url)
        yield url


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
def client(pgvector_url: str, request: pytest.FixtureRequest) -> TestClient:
    """A service with its own table, so no test can see another's writes."""
    unique = abs(hash(request.node.nodeid)) % 10**9
    settings = Settings(
        vector_store="pgvector",
        database_url=pgvector_url,
        vector_schema="public",
        vector_table=f"service_{unique}",
        embedding_dimensions=DIMENSIONS,
        internal_api_token=SERVICE_TOKEN,
    )
    app = create_app(
        settings, document_embedder=StubDocumentEmbedder(), text_embedder=StubTextEmbedder()
    )
    return TestClient(app)


@pytest.fixture
def graph(neo4j: Neo4jContainer) -> Iterator[GraphStore]:
    store = build_graph_store(graph_settings(neo4j))
    assert store is not None
    yield store
    store.close()


@pytest.fixture
def graph_client(pgvector_url: str, neo4j: Neo4jContainer, request: pytest.FixtureRequest):
    """The service with both stores, the way a graph deployment runs it."""
    unique = abs(hash(request.node.nodeid)) % 10**9
    settings = graph_settings(
        neo4j,
        vector_store="pgvector",
        database_url=pgvector_url,
        vector_schema="public",
        vector_table=f"service_{unique}",
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
