"""How the collection is laid out when Retrieval creates it.

These settings only take effect when the collection is created, and nothing
else exercises them: search works the same on a collection held entirely in
RAM, right up until the deployment runs out of it. So they are read back
from Qdrant itself rather than trusted from the constructor call.
"""

from __future__ import annotations

from typing import Any

from haystack import Document
from primer_retrieval.config import Settings
from primer_retrieval.stores import build_document_store
from qdrant_client import QdrantClient

DIMENSIONS = 8


def created_collection(qdrant_url: str, name: str) -> Any:
    settings = Settings(qdrant_url=qdrant_url, qdrant_index=name, embedding_dimensions=DIMENSIONS)
    store = build_document_store(settings)
    # The integration creates its collection on first use, not on construction.
    store.write_documents(
        [Document(id="layout", content="a passage", embedding=[1.0] + [0.0] * (DIMENSIONS - 1))]
    )
    return QdrantClient(url=qdrant_url).get_collection(name)


def test_full_vectors_live_on_disk_and_a_quantized_copy_in_memory(qdrant_url: str) -> None:
    """A large library must not need its whole index in RAM.

    Full-precision vectors at 1024 dimensions cost 4 KB each, so fifty
    million passages - a few terabytes of source documents - is two hundred
    gigabytes held in memory. Keeping the originals memory-mapped from disk
    and searching an int8 copy held in RAM cuts that by four, and Qdrant
    rescores the shortlist against the originals so results keep their
    order.
    """
    info = created_collection(qdrant_url, "layout_vectors")

    vectors = info.config.params.vectors
    assert vectors.on_disk is True

    scalar = info.config.quantization_config.scalar
    assert scalar.type == "int8"
    assert scalar.always_ram is True


def test_passage_text_is_read_from_disk(qdrant_url: str) -> None:
    """Payload holds each passage's text, which is most of the collection.

    Search only reads it for the handful of results it returns, so holding
    every passage in memory buys nothing. The indexed fields that filters
    use stay in RAM either way.
    """
    info = created_collection(qdrant_url, "layout_payload")

    assert info.config.params.on_disk_payload is True


def test_every_field_a_query_filters_on_is_indexed(qdrant_url: str) -> None:
    """Without these, a filtered search scans every point's payload."""
    info = created_collection(qdrant_url, "layout_indexes")

    assert {"meta.library_id", "meta.generation_id", "meta.document_id"} <= set(info.payload_schema)
