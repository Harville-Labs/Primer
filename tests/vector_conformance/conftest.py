"""Primer's isolation contract, run against the vector store it ships with.

A vector store is only usable here if it can keep one library's contents
away from another's. Running these cases against a real Qdrant is how that
claim is checked, rather than trusted because the integration exists - and
how an integration upgrade that changes filter semantics is caught.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from conformance_support import DIMENSIONS
from haystack.document_stores.types import DocumentStore
from primer_retrieval.config import Settings
from primer_retrieval.stores import build_document_store
from testcontainers.community.qdrant import QdrantContainer

QDRANT_IMAGE = "qdrant/qdrant:v1.19.0"


@pytest.fixture(scope="session")
def qdrant_url() -> Iterator[str]:
    with QdrantContainer(QDRANT_IMAGE) as container:
        yield f"http://{container.get_container_host_ip()}:{container.get_exposed_port(6333)}"


@pytest.fixture
def settings(qdrant_url: str, request: pytest.FixtureRequest) -> Settings:
    """Configuration with a fresh collection per test.

    Sharing one and deleting between tests would make every isolation
    assertion depend on the cleanup working, which is not what is being
    tested.
    """
    unique = abs(hash(request.node.nodeid)) % 10**9
    return Settings(
        qdrant_url=qdrant_url,
        qdrant_index=f"conformance_{unique}",
        embedding_dimensions=DIMENSIONS,
    )


@pytest.fixture
def store(settings: Settings) -> DocumentStore:
    return build_document_store(settings)
