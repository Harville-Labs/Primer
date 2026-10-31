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
        # Sized for libraries in the terabytes rather than for a demo. Full
        # vectors are memory-mapped from disk and search runs against an
        # int8 copy kept in RAM, a quarter the size; Qdrant then rescores
        # the shortlist against the originals, so ranking holds. Held in
        # RAM at full precision, fifty million passages need two hundred
        # gigabytes of memory before a single one is searched.
        #
        # Passage text is payload, and search reads it only for the results
        # it returns, so it stays on disk too. Indexed fields stay in RAM.
        #
        # All three apply only when the collection is created.
        on_disk=True,
        on_disk_payload=True,
        quantization_config={"scalar": {"type": "int8", "quantile": 0.99, "always_ram": True}},
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
