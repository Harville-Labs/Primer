"""Whether the embedder still makes the vectors this collection was built from.

Changing the embedding model at the same width is the one misconfiguration
nothing else catches. Qdrant refuses a vector of the wrong width; it accepts
a vector from another model, or from the right model pooled the wrong way,
and search then ranks passages by a similarity that means nothing. Every
answer still arrives with citations.

So the first time this process embeds, it embeds a fixed sentence and
compares the result with the one recorded on the collection when the
collection was first used. Measured on Qwen3-Embedding-0.6B under
llama-server, the same model at another quantization scored 0.9994 against
its own earlier vector, mean pooling instead of last scored 0.74, and an
unrelated sentence scored under 0.3 - so the threshold sits well clear of
both sides.

The probe goes through the document embedder, never the query embedder:
the query side carries an instruction prompt, and a prompt changed on
purpose would otherwise read as a different model.

Not part of readiness. A readiness probe that embedded would take every
replica out of service whenever the embedder restarted, which is when
search already says plainly that the embedder is down. Only success is
remembered, so a failure is checked again on the next request rather than
cached as a verdict.
"""

from __future__ import annotations

import math
from typing import Any, Protocol

from haystack import Document

from primer_retrieval.config import Settings
from primer_retrieval.errors import (
    UnembeddedChunks,
    embedder_changed,
    embedding_endpoint,
    vector_store,
    wrong_dimensions,
)
from primer_retrieval.pipelines import DocumentEmbedder

#: Ordinary prose, so no model treats it as an edge case. Changing it
#: invalidates every recorded fingerprint, so it never changes.
PROBE = "Primer checks that the embedding model still writes the vectors this index was built from."

#: The collection metadata key. Qdrant merges metadata, so this leaves
#: anything else recorded there alone.
METADATA_KEY = "primer_embedding_fingerprint"

#: See the module docstring for where this number comes from.
SAME_EMBEDDER = 0.98


class CollectionMetadata(Protocol):
    def collection_metadata(self) -> dict[str, Any]: ...

    def record_collection_metadata(self, metadata: dict[str, Any]) -> None: ...


class EmbeddingFingerprint:
    """Refuse to index or search with an embedder the collection did not use.

    Called with the consequence a caller would read, like the guards in
    `errors`, because the same mismatch stops a search and an upload.
    """

    def __init__(
        self, store: CollectionMetadata, embedder: DocumentEmbedder, settings: Settings
    ) -> None:
        self._store = store
        self._embedder = embedder
        self._settings = settings
        self._verified = False

    def __call__(self, consequence: str) -> None:
        # Two requests arriving before the first check finishes both run
        # it. That is harmless: at worst both record the same vector, and a
        # lock would hold every request behind one slow embedder call.
        if self._verified:
            return
        with embedding_endpoint(consequence):
            probe = self._probe()
        with vector_store(consequence, self._settings.vector_dimensions):
            recorded = self._store.collection_metadata().get(METADATA_KEY)
            if recorded is None:
                # A collection from before this check, or a new one. Either
                # way the embedder in use now is the best evidence there is
                # of what built it.
                self._store.record_collection_metadata(
                    {
                        METADATA_KEY: {
                            "model": self._settings.embedding_model,
                            "vector": [round(value, 6) for value in probe],
                        }
                    }
                )
        if recorded is not None:
            if len(recorded["vector"]) != len(probe):
                raise wrong_dimensions(str(len(recorded["vector"])), str(len(probe)), consequence)
            if similarity(recorded["vector"], probe) < SAME_EMBEDDER:
                raise embedder_changed(recorded["model"], consequence)
        self._verified = True

    def _probe(self) -> list[float]:
        embedded = self._embedder.run([Document(content=PROBE)])["documents"][0].embedding
        if embedded is None:
            raise UnembeddedChunks(1, 1)
        return list(embedded)


def similarity(recorded: list[float], probe: list[float]) -> float:
    """Cosine similarity of two vectors of the same width."""
    dot = sum(a * b for a, b in zip(recorded, probe, strict=True))
    norms = math.sqrt(sum(a * a for a in recorded)) * math.sqrt(sum(b * b for b in probe))
    return dot / norms if norms else 0.0
