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

from typing import Any, cast

from haystack.utils import Secret
from haystack_integrations.document_stores.qdrant import QdrantDocumentStore
from qdrant_client import QdrantClient

from primer_retrieval.config import Settings

#: What each `qdrant_layout` asks Qdrant for. Settings says when to pick which.
LAYOUTS: dict[str, dict[str, Any]] = {
    "memory": {"on_disk": False},
    "disk": {
        "on_disk": True,
        "quantization_config": {"scalar": {"type": "int8", "quantile": 0.99, "always_ram": True}},
    },
}


class RetryingQdrantDocumentStore(QdrantDocumentStore):
    """Qdrant whose collection setup is retried until it succeeds.

    The integration creates the collection on first use, and caches its
    client before that setup runs. A first use while Qdrant is still
    refusing connections - routine when the chart starts both together -
    therefore left a cached client and no collection, and every later call
    failed with a 404 until the pod was restarted. Dropping the client when
    setup fails makes the next call start over.

    The override is of a private method because that is where the client is
    cached; the reset goes through the public `close`. Setting the
    collection up eagerly at startup and exiting when Qdrant is unreachable
    would also work, but trades one failed probe for a crash loop whose
    backoff outlasts Qdrant's start by minutes.
    """

    def _initialize_client(self) -> None:
        try:
            super()._initialize_client()
        except BaseException:
            self.close()
            raise

    async def _initialize_async_client(self) -> None:
        try:
            await super()._initialize_async_client()
        except BaseException:
            await self.close_async()
            raise

    def collection_metadata(self) -> dict[str, Any]:
        """What has been recorded on the collection, or nothing.

        Here rather than in the caller because the integration exposes its
        client only privately, and reaching it from outside would be a
        second place that breaks when the integration changes.
        """
        self._initialize_client()
        client = cast(QdrantClient, self._client)
        return dict(client.get_collection(self.index).config.metadata or {})

    def record_collection_metadata(self, metadata: dict[str, Any]) -> None:
        """Merge `metadata` into what the collection already records."""
        self._initialize_client()
        client = cast(QdrantClient, self._client)
        client.update_collection(self.index, metadata=metadata)


def build_document_store(settings: Settings) -> RetryingQdrantDocumentStore:
    """Instantiate Qdrant, unchanged from the integration but for setup retries.

    The conformance suite runs Primer's isolation cases against it, so an
    integration upgrade that breaks a filter fails there rather than in a
    library nobody expected to be readable.
    """
    return RetryingQdrantDocumentStore(
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
