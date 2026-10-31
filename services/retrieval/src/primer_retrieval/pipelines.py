"""Embedding, filtering, and the translation at Primer's boundary.

Haystack types live behind this module. Chunks arrive and leave as Primer
contracts, so a Haystack upgrade is a change here rather than a coordinated
release across ingestion and chat.
"""

from __future__ import annotations

from typing import Any, Protocol
from uuid import UUID

from haystack import Document
from haystack.components.embedders import OpenAIDocumentEmbedder, OpenAITextEmbedder
from haystack.document_stores.types import DocumentStore
from haystack.utils import Secret
from haystack_integrations.components.retrievers.qdrant import QdrantEmbeddingRetriever
from primer_contracts.chunks import DocumentChunk
from primer_contracts.retrieval import RetrievedChunk, SourceLocator

from primer_retrieval.config import Settings
from primer_retrieval.errors import UnembeddedChunks

#: Meta keys Primer filters and cites on. Named once because a typo in one
#: of them is an isolation failure, not a formatting bug.
LIBRARY_ID = "library_id"
GENERATION_ID = "generation_id"
DOCUMENT_ID = "document_id"
VERSION_ID = "document_version_id"
OWNER_ID = "owner_user_id"


class TextEmbedder(Protocol):
    def run(self, text: str) -> dict[str, Any]: ...


class DocumentEmbedder(Protocol):
    def run(self, documents: list[Document]) -> dict[str, Any]: ...


def build_text_embedder(settings: Settings) -> TextEmbedder:
    return OpenAITextEmbedder(
        api_key=Secret.from_token(
            settings.embedding_api_key.get_secret_value() if settings.embedding_api_key else "none"
        ),
        model=settings.embedding_model,
        dimensions=settings.vector_dimensions,
        api_base_url=settings.embedding_base_url,
        prefix=settings.embedding_query_prefix or "",
        timeout=settings.embedding_timeout_seconds,
    )


def build_document_embedder(settings: Settings) -> DocumentEmbedder:
    return OpenAIDocumentEmbedder(
        api_key=Secret.from_token(
            settings.embedding_api_key.get_secret_value() if settings.embedding_api_key else "none"
        ),
        model=settings.embedding_model,
        dimensions=settings.vector_dimensions,
        api_base_url=settings.embedding_base_url,
        prefix=settings.embedding_document_prefix or "",
        timeout=settings.embedding_timeout_seconds,
        progress_bar=False,
    )


def build_retriever(store: DocumentStore) -> Any:
    return QdrantEmbeddingRetriever(document_store=store)  # ty: ignore[invalid-argument-type]


def scope_filter(library_id: UUID, generation_ids: tuple[UUID, ...]) -> dict[str, Any]:
    """The filter every read and every delete carries.

    Built in one place and never assembled at a call site. Isolation that
    depends on each caller remembering to add a condition is isolation that
    holds until the first caller forgets.
    """
    return {
        "operator": "AND",
        "conditions": [
            {"field": f"meta.{LIBRARY_ID}", "operator": "==", "value": str(library_id)},
            {
                "field": f"meta.{GENERATION_ID}",
                "operator": "in",
                "value": [str(generation) for generation in generation_ids],
            },
        ],
    }


def chunks_filter(
    library_id: UUID, generation_ids: tuple[UUID, ...], chunk_ids: list[str]
) -> dict[str, Any]:
    """`scope_filter`, narrowed to named chunks.

    For passages the graph points at. The graph is scoped too, but the ids
    it returns are only a list of what to look at: whether the caller may
    see them is still decided here, by the same filter as every other read.
    """
    scope = scope_filter(library_id, generation_ids)
    return {
        "operator": "AND",
        "conditions": [
            *scope["conditions"],
            {"field": "id", "operator": "in", "value": chunk_ids},
        ],
    }


def version_filter(library_id: UUID, document_version_id: UUID) -> dict[str, Any]:
    """Everything stored for one version, whatever generation built it.

    Cleanup addresses a version rather than a generation because the
    generations a version has been through are not recorded anywhere: the
    point of cleanup is to leave nothing behind, including builds nobody
    remembers.
    """
    return {
        "operator": "AND",
        "conditions": [
            {"field": f"meta.{LIBRARY_ID}", "operator": "==", "value": str(library_id)},
            {"field": f"meta.{VERSION_ID}", "operator": "==", "value": str(document_version_id)},
        ],
    }


def to_documents(chunks: tuple[DocumentChunk, ...], embedder: DocumentEmbedder) -> list[Document]:
    """Embed chunks, then store what the document actually says.

    Two texts, deliberately. The vector is built from `embedding_text`, which
    carries section headings so a passage keeps the subject it is about. What
    is stored and later quoted is `content`, verbatim. Storing the augmented
    text would put words in a citation that are not in the document.

    A chunk with no vector is never written. Haystack's embedder does not
    raise when a batch fails - it logs, and returns those documents with
    `embedding` still None - so an endpoint that was restarting, timing out
    or refusing a batch arrives here looking exactly like success.

    Writing those rows anyway produced a store that reported the right
    number of chunks and could not retrieve them. A NULL vector matches no
    query at any distance, so search silently ranks whichever chunks did
    embed: on a live library at 225 of 249 unembedded, the same passage was
    returned for every question asked, scoring no better for one the corpus
    answered than for one about a film it had never heard of. The model was
    then handed those passages as evidence.

    Failing makes the stage retry, which is what a transient endpoint
    deserves - and a partial batch fails whole, because the tempting
    alternative of keeping the vectors that did arrive is exactly how a
    generation gets activated with holes nothing reports.
    """
    embedded = embedder.run([_for_embedding(chunk) for chunk in chunks])
    vectors = {document.id: document.embedding for document in embedded["documents"]}
    missing = sum(1 for chunk in chunks if vectors.get(str(chunk.chunk_id)) is None)
    if missing:
        raise UnembeddedChunks(missing, len(chunks))
    return [_for_storage(chunk, vectors[str(chunk.chunk_id)]) for chunk in chunks]


def _for_embedding(chunk: DocumentChunk) -> Document:
    return Document(id=str(chunk.chunk_id), content=chunk.embedding_text)


def _for_storage(chunk: DocumentChunk, embedding: list[float]) -> Document:
    return Document(
        id=str(chunk.chunk_id),
        content=chunk.content,
        embedding=embedding,
        meta={
            LIBRARY_ID: str(chunk.library_id),
            GENERATION_ID: str(chunk.generation_id),
            DOCUMENT_ID: str(chunk.document_id),
            VERSION_ID: str(chunk.document_version_id),
            OWNER_ID: str(chunk.owner_user_id),
            "ordinal": chunk.ordinal,
            "filename": chunk.filename,
            "page": chunk.locator.page,
            "section": chunk.locator.section,
        },
    )


def to_retrieved(document: Document) -> RetrievedChunk:
    """Translate a store hit back into Primer's contract."""
    meta = document.meta
    return RetrievedChunk(
        chunk_id=UUID(str(document.id)),
        library_id=UUID(str(meta[LIBRARY_ID])),
        document_id=UUID(str(meta[DOCUMENT_ID])),
        document_version_id=UUID(str(meta[VERSION_ID])),
        content=document.content or "",
        score=float(document.score or 0.0),
        locator=SourceLocator(page=meta.get("page"), section=meta.get("section")),
        index_generation=UUID(str(meta[GENERATION_ID])),
    )
