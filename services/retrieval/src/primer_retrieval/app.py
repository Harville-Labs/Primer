"""The Retrieval service.

Every route takes a principal, a library, and a generation. Those are not
optional and have no defaults: a request that could omit its scope would, on
the day a caller forgot, return another user's documents. Pydantic rejects
such a request with 422 before any store is reached.

The principal is audit context, not authorization. Whether this caller may
read this library was decided by Control before the request was made;
repeating that decision here without Control's data would mean guessing.
"""

from __future__ import annotations

import logging
from typing import Annotated, Any

from fastapi import APIRouter, Depends, FastAPI, Request
from fastapi.responses import JSONResponse
from haystack.document_stores.types import DuplicatePolicy
from haystack_integrations.document_stores.qdrant import QdrantDocumentStore
from primer_contracts.indexing import (
    DeleteRequest,
    DeleteResult,
    GenerationCount,
    GenerationQuery,
    GraphIndexRequest,
    GraphIndexResult,
    IndexRequest,
    IndexResult,
    PassagesRequest,
    PassagesResult,
    PurgeRequest,
    SearchRequest,
    SearchResult,
)
from primer_service.errors import ProblemError, problem_response

from primer_retrieval import __version__
from primer_retrieval.breadth import Communities, library_wide
from primer_retrieval.config import Settings
from primer_retrieval.errors import (
    embedding_endpoint,
    graph_store,
    no_graph_store,
    vector_store,
)
from primer_retrieval.graph import GraphStore, build_graph_store
from primer_retrieval.hybrid import blend, related_chunks
from primer_retrieval.pipelines import (
    LIBRARY_ID,
    DocumentEmbedder,
    TextEmbedder,
    build_document_embedder,
    build_retriever,
    build_text_embedder,
    chunks_filter,
    reading_order,
    scope_filter,
    superseded_filter,
    to_documents,
    to_passage,
    to_retrieved,
)
from primer_retrieval.reranking import Reranker, reorder
from primer_retrieval.security import require_service_credential
from primer_retrieval.stores import build_document_store

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/internal/v1",
    dependencies=[Depends(require_service_credential)],
    include_in_schema=False,
)


#: Matches nothing: no library is named this, since library ids are UUIDs.
READINESS_FILTER = {"field": f"meta.{LIBRARY_ID}", "operator": "==", "value": "readiness-probe"}


class RetrievalState:
    """Everything a request needs, built once at startup."""

    def __init__(
        self,
        settings: Settings,
        store: QdrantDocumentStore,
        document_embedder: DocumentEmbedder,
        text_embedder: TextEmbedder,
        retriever: Any | None = None,
        reranker: Reranker | None = None,
    ) -> None:
        self.settings = settings
        self.store = store
        self.document_embedder = document_embedder
        self.text_embedder = text_embedder
        self.retriever = retriever if retriever is not None else build_retriever(store)
        # None unless an endpoint is configured, and None is what makes a
        # deployment without one behave exactly as it did before.
        self.reranker = reranker if reranker is not None else build_reranker(settings)
        # None unless Neo4j is configured, for the same reason as above.
        self.graph: GraphStore | None = build_graph_store(settings)
        self.communities = Communities(self.graph, settings) if self.graph else None


def build_reranker(settings: Settings) -> Reranker | None:
    """The configured reranker, or nothing at all."""
    if not settings.reranking_enabled:
        return None
    return Reranker(
        base_url=settings.rerank_base_url or "",
        model=settings.rerank_model,
        api_key=(settings.rerank_api_key.get_secret_value() if settings.rerank_api_key else None),
        timeout_seconds=settings.rerank_timeout_seconds,
    )


def get_state(request: Request) -> RetrievalState:
    state: RetrievalState = request.app.state.retrieval
    return state


State = Annotated[RetrievalState, Depends(get_state)]


#: Health is deliberately on its own router. A router-level dependency
#: applies to every route on it, and `dependencies=[]` on a single route adds
#: nothing rather than clearing the router's - so a liveness probe declared
#: on the router above would demand a service credential no orchestrator
#: sends, and the container would never be reported healthy.
health = APIRouter(tags=["health"])


@health.get("/health/live", summary="Process liveness")
def live() -> dict[str, str]:
    """Whether the process is up. Deliberately touches no dependency.

    A liveness probe that checked the vector store would restart this
    service whenever the store was briefly unreachable, which is precisely
    when restarting it helps least.
    """
    return {"status": "ok"}


@health.get("/health/ready", summary="Readiness")
def ready(state: State) -> JSONResponse:
    """Whether this instance can serve a search.

    Counts a library that cannot exist, which proves the store is reachable
    and the collection is there while touching nothing in it: the count is
    answered from the `library_id` payload index. Counting the collection
    instead is exact by default, and on a deployment holding tens of
    millions of passages every probe on every replica would be a full scan.
    """
    try:
        state.store.count_documents_by_filter(READINESS_FILTER)
    except Exception:
        # Logged, because the response deliberately says nothing: a readiness
        # probe is reachable to anyone who can reach the port, and the reason
        # a store is unreachable names hosts and schemas. An operator with a
        # container stuck unhealthy needs that reason somewhere, and the log
        # is where they are allowed to have it.
        logger.exception("Readiness check failed: the document store is unreachable")
        return JSONResponse(status_code=503, content={"status": "unready"})
    return JSONResponse(status_code=200, content={"status": "ok"})


@router.post("/index", summary="Write one generation's chunks")
def index_chunks(payload: IndexRequest, state: State) -> IndexResult:
    """Write into a pending generation.

    Nothing searches a generation until a caller names it, so a rebuild in
    progress is invisible rather than partially answering questions.

    Duplicate ids overwrite. Chunk ids are derived from version, generation,
    and ordinal, so a stage that ran twice rewrites identical rows instead of
    doubling the index.
    """
    with embedding_endpoint("this document cannot be indexed"):
        documents = to_documents(payload.chunks, state.document_embedder)
    with vector_store("this document cannot be indexed", state.settings.vector_dimensions):
        written = state.store.write_documents(documents, policy=DuplicatePolicy.OVERWRITE)
    return IndexResult(generation_id=payload.generation_id, written=written)


@router.post("/verify", summary="Count what a generation actually holds")
def verify_generation(payload: GenerationQuery, state: State) -> GenerationCount:
    """Report a generation's size so activation can refuse an incomplete index.

    Activating a generation that is short of its expected count would drop
    the missing passages from every future answer, with nothing to show that
    anything was lost.
    """
    # Counted inside Qdrant. Reading the generation back to take its length
    # would move every passage's text and vector across the network, which
    # for one large document is hundreds of megabytes to produce a number.
    count = state.store.count_documents_by_filter(
        scope_filter(payload.library_id, (payload.generation_id,))
    )
    graph_count = None
    if state.graph is not None:
        with graph_store("this document's graph cannot be verified"):
            graph_count = state.graph.count(payload.library_id, payload.generation_id)
    return GenerationCount(
        generation_id=payload.generation_id, count=count, graph_count=graph_count
    )


@router.post("/graph/index", summary="Write what one generation's chunks say, as a graph")
def index_graph(payload: GraphIndexRequest, state: State) -> GraphIndexResult:
    """Write extracted entities and relations into a pending generation.

    Refused rather than ignored when there is no graph store. Accepting and
    discarding would let a worker activate a generation it believes has a
    graph, and the only symptom would be hybrid search quietly never
    finding anything.
    """
    consequence = "this document's graph cannot be indexed"
    if state.graph is None:
        raise no_graph_store(consequence)
    with graph_store(consequence):
        state.graph.write(
            payload.library_id, payload.document_version_id, payload.generation_id, payload.graphs
        )
    return GraphIndexResult(generation_id=payload.generation_id, written=len(payload.graphs))


@router.post("/search", summary="Search one library")
def search(payload: SearchRequest, state: State) -> SearchResult:
    """Rank passages within one library's active generations.

    The filter is built from the request's scope, never from the query, and
    is applied by the store rather than by discarding results afterwards.
    Filtering after the fact would silently return fewer results than asked
    for, and would depend on this code never being reordered.

    With a graph, the passages connected to what the question names are
    fused with the vector hits by rank before the reranker reads them. The
    order is the fusion's; the scores are still the store's similarity or
    the reranker's, because a fused rank score is not something a relevance
    floor can be measured against.

    A library-wide question reads passages chosen to cover the library's
    communities instead, and skips the reranker and the floor: both measure
    how well a passage answers the question, and the point of asking across
    the library is to read passages that do not. See ADR 0006.
    """
    with embedding_endpoint("this library cannot be searched"):
        embedded = state.text_embedder.run(payload.query)

    # Passages the graph found are read back through the retriever rather
    # than fetched by id, so each carries the store's own similarity to the
    # question. That keeps every score on one scale and the floor meaningful
    # for them too, where a lookup by id would return them unscored.
    def fetch(chunk_ids: list[str]) -> list[Any]:
        return state.retriever.run(
            query_embedding=embedded["embedding"],
            filters=chunks_filter(payload.library_id, payload.generation_ids, chunk_ids),
            top_k=len(chunk_ids),
        )["documents"]

    if payload.breadth == "library":
        covering = library_wide(
            state.communities, payload.library_id, payload.generation_ids, payload.limit, fetch
        )
        if covering is not None:
            return SearchResult(chunks=tuple(to_retrieved(hit) for hit in covering))

    # Fetch wider than the answer needs when a reranker will read them: the
    # passage that answers the question is often inside the first twenty and
    # outside the first six. Without one, the vector ordering is the answer,
    # so asking for more would be work nothing looks at.
    candidates = (
        max(payload.limit, state.settings.rerank_candidates)
        if state.reranker is not None
        else payload.limit
    )
    hits = state.retriever.run(
        query_embedding=embedded["embedding"],
        filters=scope_filter(payload.library_id, payload.generation_ids),
        top_k=candidates,
    )
    related = related_chunks(
        state.graph,
        payload.library_id,
        payload.generation_ids,
        payload.query,
        hops=state.settings.graph_hops,
        limit=state.settings.graph_candidates,
    )

    kept = reorder(
        state.reranker,
        payload.query,
        blend(list(hits["documents"]), related, fetch),
        lambda hit: hit.content or "",
        payload.limit,
    )
    return SearchResult(
        chunks=tuple(to_retrieved(hit) for hit in above_floor(kept, state.settings))
    )


def above_floor(hits: list[Any], settings: Settings) -> list[Any]:
    """Drop passages that answer the question no better than anything else.

    This is the one place Retrieval discards results after the fact, which
    the scope filter deliberately never does - and the difference is what
    each is for. Scope decides what a caller is allowed to see, so it
    belongs in the query where it cannot be forgotten. Relevance decides
    whether what was found is worth showing, which cannot be known until it
    has been scored.

    Returning fewer than `limit` is the point rather than a side effect. A
    vector search always returns its top k, so a question the library
    cannot answer comes back with a full set of passages that are merely
    the closest of a bad lot - and everything downstream treats them as
    evidence. The model is told to say when the passages do not contain the
    answer, but it is being asked to overrule what looks like retrieved
    proof, and a small model will not.

    Nothing is dropped when no floor is configured, which is the default.
    """
    if settings.min_score is None:
        return hits
    return [hit for hit in hits if (hit.score or 0.0) >= settings.min_score]


@router.post("/passages", summary="List one library's passages, in reading order")
def passages(payload: PassagesRequest, state: State) -> PassagesResult:
    """Every passage in a library's active generations, up to a limit.

    For scanning a library rather than searching it, so there is no query
    and nothing is ranked. Read through the same scope filter as a search.

    The whole scope is read and sorted here, then cut, because a filter
    read in Qdrant has no order to cut by: taking the first `limit` it
    returned would scan a different part of a large library each time.
    Vectors are not read back, so the cost is the passages' text.
    """
    found = sorted(
        state.store.filter_documents(scope_filter(payload.library_id, payload.generation_ids)),
        key=reading_order,
    )
    return PassagesResult(
        passages=tuple(to_passage(document) for document in found[: payload.limit]),
        total=len(found),
    )


@router.post("/delete", summary="Remove one generation's chunks")
def delete_generation(payload: DeleteRequest, state: State) -> DeleteResult:
    """Delete by generation, which makes retiring and erasing the same act.

    Deleting nothing is success. A repeated delete has to be safe: it is
    exactly what a redelivered cleanup message asks for.

    The graph is erased in the same request. Removing the passages and
    keeping the names they mentioned would leave part of a document in the
    library after the document had gone.
    """
    deleted = state.store.delete_by_filter(
        scope_filter(payload.library_id, (payload.generation_id,))
    )
    if state.graph is not None:
        with graph_store("this document's graph cannot be erased"):
            state.graph.erase(
                payload.library_id, payload.document_version_id, only=payload.generation_id
            )
    return DeleteResult(generation_id=payload.generation_id, deleted=deleted)


@router.post("/purge", summary="Remove a version's chunks")
def purge_version(payload: PurgeRequest, state: State) -> DeleteResult:
    """Erase a version, optionally sparing the generation now in use.

    Deleted by filter inside Qdrant, which excludes the generation to keep
    with a `must_not` it applies exactly. Reading every passage back to pick
    the ids out here instead moved each one's vector across the network.
    """
    deleted = state.store.delete_by_filter(
        superseded_filter(
            payload.library_id, payload.document_version_id, payload.keep_generation_id
        )
    )
    if state.graph is not None:
        with graph_store("this document's graph cannot be erased"):
            state.graph.erase(
                payload.library_id, payload.document_version_id, keep=payload.keep_generation_id
            )
    return DeleteResult(
        generation_id=payload.keep_generation_id or payload.document_version_id,
        deleted=deleted,
    )


def create_app(
    settings: Settings | None = None,
    store: QdrantDocumentStore | None = None,
    document_embedder: DocumentEmbedder | None = None,
    text_embedder: TextEmbedder | None = None,
    retriever: Any | None = None,
) -> FastAPI:
    """Build the Retrieval service.

    The store and embedders are injected rather than read from globals so a
    test can run the real filtering logic against a real backend without an
    embedding endpoint, which is what makes the conformance suite possible.
    """
    settings = settings or Settings()
    app = FastAPI(
        title="Primer Retrieval",
        version=__version__,
        summary="The exclusive owner of Primer's vector stores",
    )
    app.state.settings = settings
    app.state.retrieval = RetrievalState(
        settings,
        store or build_document_store(settings),
        document_embedder or build_document_embedder(settings),
        text_embedder or build_text_embedder(settings),
        retriever,
    )

    @app.exception_handler(ProblemError)
    async def _handle_problem(request: Request, exc: ProblemError) -> JSONResponse:
        return problem_response(request, exc)

    app.include_router(health)
    app.include_router(router)
    return app
