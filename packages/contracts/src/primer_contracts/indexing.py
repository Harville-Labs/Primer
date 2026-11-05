"""The Retrieval service's cluster-internal API.

Retrieval owns the vector store exclusively. Nothing outside it names a
collection, a table, or a filter syntax; callers name a library and a
generation, and Retrieval decides what that means for the backend it was
configured with.
"""

from __future__ import annotations

from typing import Literal
from uuid import UUID

from pydantic import Field

from primer_contracts.base import WireModel
from primer_contracts.chunks import DocumentChunk
from primer_contracts.graph import ChunkGraph
from primer_contracts.identity import Principal
from primer_contracts.retrieval import RetrievedChunk, SourceLocator


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
    #: way and for the same reason. Zero for a generation indexed with no
    #: extraction model, which has vectors and an empty graph.
    graph_count: int = Field(ge=0)


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


#: How much of a library a question is about. A focused question reads the
#: passages closest to it; a library-wide one reads passages chosen to cover
#: the library's communities, most of which do not resemble the question.
#: See ADR 0006.
Breadth = Literal["focused", "library"]


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
    breadth: Breadth = "focused"


class SearchResult(WireModel):
    chunks: tuple[RetrievedChunk, ...] = ()


#: The most passages one listing returns. A scan reads each of them through
#: a model, so a library larger than this is scanned in part, and the
#: listing says how much was left out rather than reading all of it.
MAX_PASSAGES = 2000


class PassagesRequest(WireModel):
    """List one library's passages, within a known set of active generations.

    Scoped exactly as a search is, and for the same reason: the generation
    list is required, so a caller that forgot it is refused, not answered
    with everything.
    """

    principal: Principal
    library_id: UUID
    generation_ids: tuple[UUID, ...] = Field(min_length=1)
    limit: int = Field(default=500, ge=1, le=MAX_PASSAGES)


class Passage(WireModel):
    """A passage as it is stored, with what is needed to point back to it."""

    chunk_id: UUID
    document_id: UUID
    filename: str
    #: Its position in the document, from zero.
    ordinal: int
    content: str
    locator: SourceLocator | None = None


class PassagesResult(WireModel):
    #: In reading order: by file, then by position within it.
    passages: tuple[Passage, ...] = ()
    #: How many the library holds, of which `passages` is the first part.
    total: int = 0


class GraphStatusRequest(WireModel):
    """Describe one library's knowledge graph, within its active generations.

    Scoped like a search: a pending rebuild's graph is not yet the
    library's, and an empty scope is refused rather than read as everything.
    """

    principal: Principal
    library_id: UUID
    generation_ids: tuple[UUID, ...] = Field(min_length=1)


class GraphStatus(WireModel):
    """How much of a library the knowledge graph covers."""

    entities: int = Field(ge=0, description="Distinct entities the library's passages mention")
    relations: int = Field(ge=0, description="Relations extracted between those entities")
    communities: int | None = Field(
        default=None,
        ge=0,
        description=(
            "Topics a library-wide question reads from: the largest communities, at most "
            "PRIMER_GRAPH_COMMUNITIES. Null when the library is too large to group into "
            "topics, which is also when library-wide questions are refused."
        ),
    )
    graphed_versions: tuple[UUID, ...] = Field(
        default=(),
        description=(
            "Document versions with a graph. A searchable version missing from this list "
            "was indexed with no extraction model, and needs reindexing to gain one."
        ),
    )


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
