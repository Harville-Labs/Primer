"""Retrieval contracts.

Every request carries both the acting principal and an explicit library
scope. The Retrieval service rejects unscoped queries, so the scope is
required here rather than inferred from ownership.
"""

from __future__ import annotations

from typing import Annotated, Literal
from uuid import UUID

from pydantic import Field

from primer_contracts.base import WireModel
from primer_contracts.identity import Principal

Query = Annotated[str, Field(min_length=1, max_length=4000)]

#: Which ranking found a passage. "graph" means only the knowledge graph
#: reached it: the passage shares too little with the question's wording for
#: vector search to rank it, and is connected to what the question names.
FoundBy = Literal["vector", "graph", "both"]

#: Entity names carried with a passage the graph found. Enough to say why it
#: was found; a reader wanting the whole neighbourhood is asking a different
#: question.
MAX_GRAPH_ENTITIES = 5

GraphEntityName = Annotated[str, Field(min_length=1, max_length=200)]


class SourceLocator(WireModel):
    """Where a passage sits inside its source document.

    Locators are user-meaningful positions only. Filesystem or object-store
    paths are deliberately absent from citation-facing contracts.
    """

    page: int | None = Field(default=None, ge=1)
    section: str | None = Field(default=None, max_length=255)


class RetrievalRequest(WireModel):
    """A user- and library-scoped search."""

    principal: Principal
    library_id: UUID
    query: Query
    limit: int = Field(default=8, ge=1, le=50)


class RetrievedChunk(WireModel):
    """A ranked passage with the scope needed to authorize and cite it."""

    chunk_id: UUID
    library_id: UUID
    document_id: UUID
    document_version_id: UUID
    content: str
    score: float
    locator: SourceLocator | None = None
    #: Which index build answered. A UUID, not a counter: generations are
    #: created concurrently by reindexing and deletion, and a counter would
    #: need a coordinator to hand out the next value.
    index_generation: UUID
    found_by: FoundBy = "vector"
    #: The entities the graph reached this passage through, nearest first.
    #: Empty unless the graph found it.
    graph_entities: tuple[GraphEntityName, ...] = Field(default=(), max_length=MAX_GRAPH_ENTITIES)
