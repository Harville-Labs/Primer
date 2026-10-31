"""The document store, built directly from the official Haystack integration.

There is deliberately no Primer vector adapter interface. Wrapping the
integration would add a layer whose only job is to be a second place for
isolation bugs to live. What Primer owns is the filter every query carries,
not the store.

The concrete type is used rather than Haystack's `DocumentStore` protocol
because the routes count and delete by filter, and the protocol has no such
methods: without them a count means reading every passage back.
"""

from __future__ import annotations

from typing import Any

from haystack.utils import Secret
from haystack_integrations.document_stores.qdrant import QdrantDocumentStore

from primer_retrieval.config import Settings

#: What each `qdrant_layout` asks Qdrant for. Settings says when to pick which.
LAYOUTS: dict[str, dict[str, Any]] = {
    "memory": {"on_disk": False},
    "disk": {
        "on_disk": True,
        "quantization_config": {"scalar": {"type": "int8", "quantile": 0.99, "always_ram": True}},
    },
}


def build_document_store(settings: Settings) -> QdrantDocumentStore:
    """Instantiate Qdrant, unchanged from the integration.

    The conformance suite runs Primer's isolation cases against it, so an
    integration upgrade that breaks a filter fails there rather than in a
    library nobody expected to be readable.
    """
    return QdrantDocumentStore(
        url=settings.qdrant_url,
        api_key=(
            Secret.from_token(settings.qdrant_api_key.get_secret_value())
            if settings.qdrant_api_key
            else None
        ),
        index=settings.qdrant_index,
        embedding_dim=settings.vector_dimensions,
        return_embedding=False,
        # Primer writes deterministic chunk ids, so re-running a stage
        # must overwrite rather than accumulate duplicates.
        recreate_index=False,
        similarity="cosine",
        # Passage text is payload, and search reads it only for the results
        # it returns, so it stays on disk in either layout. Indexed fields
        # stay in RAM. Both apply only when the collection is created.
        on_disk_payload=True,
        **LAYOUTS[settings.qdrant_layout],
        # Filtered on every read and every delete. Without a payload index
        # Qdrant scans every point's payload to apply the filter, which is
        # unnoticeable on one library and the whole cost of a search on a
        # deployment holding thousands.
        payload_fields_to_index=[
            {"field_name": "meta.library_id", "field_schema": "keyword"},
            {"field_name": "meta.generation_id", "field_schema": "keyword"},
            {"field_name": "meta.document_id", "field_schema": "keyword"},
        ],
    )
