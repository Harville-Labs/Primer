"""The Retrieval service's cluster-internal API.

Retrieval owns the vector store exclusively. Nothing outside it names a
collection, a table, or a filter syntax; callers name a library and a
generation, and Retrieval decides what that means for the backend it was
configured with.
"""

from __future__ import annotations

from uuid import UUID

from pydantic import Field

from primer_contracts.base import WireModel
from primer_contracts.chunks import DocumentChunk
from primer_contracts.graph import ChunkGraph
from primer_contracts.identity import Principal
from primer_contracts.retrieval import RetrievedChunk


class IndexRequest(WireModel):
    """Write one generation's chunks.

    Chunks are written into a pending generation, which nothing searches
    until it is activated. A half-finished rebuild is therefore invisible
    rather than partially answering questions.
    """

    principal: Principal
    library_id: UUID
    document_version_id: UUID
    generation_id: UUID
    chunks: tuple[DocumentChunk, ...] = Field(min_length=1)


class IndexResult(WireModel):
    generation_id: UUID
    written: int = Field(ge=0)


class GenerationQuery(WireModel):
    """Address one generation of one version."""

    principal: Principal
    library_id: UUID
    document_version_id: UUID
    generation_id: UUID


class GenerationCount(WireModel):
    """How many chunks a generation actually holds.

    Activation compares this against what ingestion produced. A generation
    that is short of its expected count is an incomplete index, and
    activating it would silently drop the missing passages from every future
    answer.
    """

    generation_id: UUID
    count: int = Field(ge=0)
    #: Chunks the knowledge graph holds for the generation, counted the same
    #: way and for the same reason. None when Retrieval has no graph store,
    #: which a worker that extracted a graph must treat as a deployment
    #: fault rather than as zero.
    graph_count: int | None = Field(default=None, ge=0)


class GraphIndexRequest(WireModel):
    """Write what one batch of a generation's chunks say, as a graph.

    Into the same pending generation as the batch's vectors, so the two are
    activated together and neither is searchable before the other.
    """

    principal: Principal
    library_id: UUID
    document_version_id: UUID
    generation_id: UUID
    graphs: tuple[ChunkGraph, ...] = Field(min_length=1)


class GraphIndexResult(WireModel):
    generation_id: UUID
    written: int = Field(ge=0)


class LibraryAccessRequest(WireModel):
    """Ask Control whether a principal may read a library, and what to search."""

    principal: Principal
    library_id: UUID


class LibraryScope(WireModel):
    """Permission and search scope in one answer.

    They travel together because they are decided from the same rows: asking
    separately would leave a window where a library became readable, or
    stopped being, between the two calls.
    """

    library_id: UUID
    generation_ids: tuple[UUID, ...] = ()


class SearchRequest(WireModel):
    """Search one library, within a known set of active generations.

    Both scope fields are required and neither has a default. A search that
    could omit them would, on the day someone forgot, quietly return another
    user's documents - so omission is a validation error, not an empty
    filter.
    """

    principal: Principal
    library_id: UUID
    generation_ids: tuple[UUID, ...] = Field(min_length=1)
    query: str = Field(min_length=1, max_length=4000)
    limit: int = Field(default=8, ge=1, le=50)


class SearchResult(WireModel):
    chunks: tuple[RetrievedChunk, ...] = ()


class DeleteRequest(WireModel):
    """Remove one generation's chunks.

    Deletion is addressed by generation, so retiring a superseded index and
    erasing a deleted document are the same operation with the same
    idempotency.
    """

    principal: Principal
    library_id: UUID
    document_version_id: UUID
    generation_id: UUID


class PurgeRequest(WireModel):
    """Remove a version's chunks, optionally sparing one generation.

    Retiring a superseded build and erasing a deleted document are the same
    operation with one parameter different, so they share a code path rather
    than two that must stay in agreement about what "gone" means.
    """

    principal: Principal
    library_id: UUID
    document_version_id: UUID
    #: The generation to keep. None erases the version entirely.
    keep_generation_id: UUID | None = None


class DeleteResult(WireModel):
    generation_id: UUID
    deleted: int = Field(ge=0)
