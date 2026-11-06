"""A collection refuses vectors from an embedder that did not build it.

Swapping the embedding model at the same width is accepted by Qdrant without
complaint, and every search after it ranks passages by a similarity between
two different models' vectors. The answers still cite things. These pin that
the swap is refused instead, on both the read and the write path, and that
the same embedder coming back after a restart is not.
"""

from __future__ import annotations

import hashlib
import math
import uuid
from collections.abc import Callable
from dataclasses import replace
from typing import Any

import pytest
from fastapi.testclient import TestClient
from haystack import Document
from primer_retrieval.app import create_app
from primer_retrieval.config import Settings
from primer_retrieval.fingerprint import METADATA_KEY
from primer_retrieval.stores import build_document_store

TOKEN = "fingerprint-service-token"  # noqa: S105 - a fixture value, not a real credential
DIMENSIONS = 16
LIBRARY = uuid.uuid5(uuid.NAMESPACE_URL, "fingerprint-library")
GENERATION = uuid.uuid5(uuid.NAMESPACE_URL, "fingerprint-generation")


def hashed(text: str) -> list[float]:
    vector = [0.0] * DIMENSIONS
    for word in text.lower().split():
        vector[hashlib.sha256(word.encode()).digest()[0] % DIMENSIONS] += 1.0
    norm = math.sqrt(sum(value * value for value in vector)) or 1.0
    return [value / norm for value in vector]


def other_model(text: str) -> list[float]:
    """The same width and the same text, and none of the same directions."""
    vector = hashed(text)
    return vector[5:] + vector[:5]


class DocumentEmbedder:
    def __init__(self, embed: Callable[[str], list[float]]) -> None:
        self.embed = embed

    def run(self, documents: list[Document]) -> dict[str, Any]:
        return {"documents": [replace(d, embedding=self.embed(d.content or "")) for d in documents]}


class TextEmbedder:
    def __init__(self, embed: Callable[[str], list[float]]) -> None:
        self.embed = embed

    def run(self, text: str) -> dict[str, Any]:
        return {"embedding": self.embed(text)}


@pytest.fixture
def settings(qdrant_url: str, request: pytest.FixtureRequest) -> Settings:
    unique = abs(hash(request.node.nodeid)) % 10**9
    return Settings(
        qdrant_url=qdrant_url,
        qdrant_index=f"fingerprint_{unique}",
        embedding_dimensions=DIMENSIONS,
        internal_api_token=TOKEN,
    )


def service(settings: Settings, embed: Callable[[str], list[float]]) -> TestClient:
    """A fresh process: nothing remembered but what is in Qdrant."""
    return TestClient(
        create_app(
            settings, document_embedder=DocumentEmbedder(embed), text_embedder=TextEmbedder(embed)
        ),
        raise_server_exceptions=False,
    )


def index(client: TestClient, content: str) -> Any:
    version = uuid.uuid5(uuid.NAMESPACE_URL, "fingerprint-version")
    return client.post(
        "/internal/v1/index",
        headers={"X-Primer-Service-Token": TOKEN},
        json={
            "principal": principal(),
            "library_id": str(LIBRARY),
            "document_version_id": str(version),
            "generation_id": str(GENERATION),
            "chunks": [
                {
                    "chunk_id": str(uuid.uuid5(uuid.NAMESPACE_URL, content)),
                    "ordinal": 0,
                    "library_id": str(LIBRARY),
                    "document_id": str(uuid.uuid5(uuid.NAMESPACE_URL, "fingerprint-document")),
                    "document_version_id": str(version),
                    "owner_user_id": str(uuid.uuid5(uuid.NAMESPACE_URL, "fingerprint-owner")),
                    "generation_id": str(GENERATION),
                    "content": content,
                    "embedding_text": content,
                    "locator": {"page": 1},
                    "filename": "paper.pdf",
                }
            ],
        },
    )


def search(client: TestClient, query: str) -> Any:
    return client.post(
        "/internal/v1/search",
        headers={"X-Primer-Service-Token": TOKEN},
        json={
            "principal": principal(),
            "library_id": str(LIBRARY),
            "generation_ids": [str(GENERATION)],
            "query": query,
            "limit": 5,
        },
    )


def principal() -> dict[str, Any]:
    return {
        "subject": "reader",
        "user_id": str(uuid.uuid5(uuid.NAMESPACE_URL, "reader")),
        "groups": [],
    }


def test_the_first_embedder_to_use_a_collection_is_recorded_on_it(settings: Settings) -> None:
    """Including a collection that predates the check: there is nothing to
    compare it with, and the embedder in use is the best evidence there is."""
    assert index(service(settings, hashed), "attention is efficient").status_code == 200

    recorded = build_document_store(settings).collection_metadata()[METADATA_KEY]
    assert recorded["model"] == settings.embedding_model
    assert len(recorded["vector"]) == DIMENSIONS


def test_the_same_embedder_after_a_restart_is_accepted(settings: Settings) -> None:
    assert index(service(settings, hashed), "attention is efficient").status_code == 200

    restarted = service(settings, hashed)

    found = search(restarted, "attention")
    assert found.status_code == 200
    assert [hit["content"] for hit in found.json()["chunks"]] == ["attention is efficient"]


def test_another_embedder_at_the_same_width_is_refused_for_search_and_indexing(
    settings: Settings,
) -> None:
    """A 409, so an indexing job stops rather than retrying into the same wall."""
    assert index(service(settings, hashed), "attention is efficient").status_code == 200

    swapped = service(settings, other_model)

    for response in (search(swapped, "attention"), index(swapped, "a second passage")):
        assert response.status_code == 409, response.text
        problem = response.json()
        assert problem["code"] == "conflict"
        assert "PRIMER_EMBEDDING_BASE_URL" in problem["detail"]
        assert "PRIMER_QDRANT_INDEX" in problem["detail"]
    assert build_document_store(settings).count_documents() == 1


def test_an_embedder_that_returns_nothing_for_the_probe_is_not_recorded(
    settings: Settings,
) -> None:
    """Recording a failure would make the next, working embedder look wrong."""

    class Empty:
        def run(self, documents: list[Document]) -> dict[str, Any]:
            return {"documents": [replace(d, embedding=None) for d in documents]}

    client = TestClient(
        create_app(settings, document_embedder=Empty(), text_embedder=TextEmbedder(hashed)),
        raise_server_exceptions=False,
    )

    assert search(client, "attention").status_code == 503
    assert METADATA_KEY not in build_document_store(settings).collection_metadata()
