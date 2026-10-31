"""Document stores, built directly from official Haystack integrations.

There is deliberately no Primer vector adapter interface. Haystack's
`DocumentStore` protocol is already that abstraction, and wrapping it would
add a layer whose only job is to be a second place for isolation bugs to
live. What Primer owns is the filter every query carries, not the store.
"""

from __future__ import annotations

from haystack.document_stores.types import DocumentStore
from haystack.utils import Secret
from haystack_integrations.document_stores.pgvector import PgvectorDocumentStore
from haystack_integrations.document_stores.qdrant import QdrantDocumentStore

from primer_retrieval.config import Settings


def build_document_store(settings: Settings) -> DocumentStore:
    """Instantiate the configured backend.

    Both branches return an official integration unchanged. The conformance
    suite runs the same cases against each, so a backend that cannot meet
    Primer's isolation contract fails there rather than in a library nobody
    expected to be readable.
    """
    if settings.vector_store == "qdrant":
        return QdrantDocumentStore(
            url=settings.qdrant_url,
            index=settings.qdrant_index,
            embedding_dim=settings.vector_dimensions,
            return_embedding=False,
            # Primer writes deterministic chunk ids, so re-running a stage
            # must overwrite rather than accumulate duplicates.
            recreate_index=False,
            similarity="cosine",
        )

    return PgvectorDocumentStore(
        connection_string=Secret.from_token(settings.database_url),
        schema_name=settings.vector_schema,
        table_name=settings.vector_table,
        embedding_dimension=settings.vector_dimensions,
        vector_function="cosine_similarity",
        search_strategy="hnsw",
        # The migration creates the extension, with the privileges that
        # needs. An application role should not hold them, and asking for
        # them on the first search fails in a deployment that is correctly
        # locked down.
        create_extension=False,
        recreate_table=False,
        # Derived from the table, because the integration's defaults are
        # fixed names: two Primer tables in one schema would otherwise try to
        # create the same index and the second would fail at startup.
        hnsw_index_name=f"{settings.vector_table}_hnsw_index",
        keyword_index_name=f"{settings.vector_table}_keyword_index",
    )
