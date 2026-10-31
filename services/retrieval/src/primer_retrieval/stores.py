"""The document store, built directly from the official Haystack integration.

There is deliberately no Primer vector adapter interface. Haystack's
`DocumentStore` protocol is already that abstraction, and wrapping it would
add a layer whose only job is to be a second place for isolation bugs to
live. What Primer owns is the filter every query carries, not the store.
"""

from __future__ import annotations

from haystack.document_stores.types import DocumentStore
from haystack.utils import Secret
from haystack_integrations.document_stores.qdrant import QdrantDocumentStore

from primer_retrieval.config import Settings


def build_document_store(settings: Settings) -> DocumentStore:
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
