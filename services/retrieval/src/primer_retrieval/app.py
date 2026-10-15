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
from haystack.document_stores.types import DocumentStore, DuplicatePolicy
from primer_contracts.indexing import (
    DeleteRequest,
    DeleteResult,
    GenerationCount,
    GenerationQuery,
    GraphIndexRequest,
    GraphIndexResult,
    IndexRequest,
    IndexResult,
    PurgeRequest,
    SearchRequest,
    SearchResult,
)
from primer_service.errors import ProblemError, problem_response

from primer_retrieval import __version__
from primer_retrieval.config import Settings
from primer_retrieval.errors import (
    embedding_endpoint,
    graph_store,
    no_graph_store,
    vector_store,
)
from primer_retrieval.graph import GraphStore, build_graph_store
from primer_retrieval.pipelines import (
    GENERATION_ID,
    DocumentEmbedder,
    TextEmbedder,
    build_document_embedder,
    build_retriever,
    build_text_embedder,
    scope_filter,
    to_documents,
    to_retrieved,
    version_filter,
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


class RetrievalState:
    """Everything a request needs, built once at startup."""

    def __init__(
        self,
        settings: Settings,
        store: DocumentStore,
        document_embedder: DocumentEmbedder,
        text_embedder: TextEmbedder,
        retriever: Any | None = None,
        reranker: Reranker | None = None,
    ) -> None:
        self.settings = settings
        self.store = store
        self.document_embedder = document_embedder
        self.text_embedder = text_embedder
        self.retriever = retriever if retriever is not None else build_retriever(store, settings)
        # None unless an endpoint is configured, and None is what makes a
        # deployment without one behave exactly as it did before.
        self.reranker = reranker if reranker is not None else build_reranker(settings)
        # None unless Neo4j is configured, for the same reason as above.
        self.graph: GraphStore | None = build_graph_store(settings)


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

    Counting documents in an empty filter is the cheapest call that proves
    the store is reachable and the schema exists.
    """
    try:
        state.store.count_documents()
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
    with vector_store("this document cannot be indexed"):
        written = state.store.write_documents(documents, policy=DuplicatePolicy.OVERWRITE)
    return IndexResult(generation_id=payload.generation_id, written=written)


@router.post("/verify", summary="Count what a generation actually holds")
def verify_generation(payload: GenerationQuery, state: State) -> GenerationCount:
    """Report a generation's size so activation can refuse an incomplete index.

    Activating a generation that is short of its expected count would drop
    the missing passages from every future answer, with nothing to show that
    anything was lost.
    """
    documents = state.store.filter_documents(
        filters=scope_filter(payload.library_id, (payload.generation_id,))
    )
    graph_count = None
    if state.graph is not None:
        with graph_store("this document's graph cannot be verified"):
            graph_count = state.graph.count(payload.library_id, payload.generation_id)
    return GenerationCount(
        generation_id=payload.generation_id, count=len(documents), graph_count=graph_count
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
    """
    with embedding_endpoint("this library cannot be searched"):
        embedded = state.text_embedder.run(payload.query)
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
    kept = reorder(
        state.reranker,
        payload.query,
        list(hits["documents"]),
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


@router.post("/delete", summary="Remove one generation's chunks")
def delete_generation(payload: DeleteRequest, state: State) -> DeleteResult:
    """Delete by generation, which makes retiring and erasing the same act.

    Deleting nothing is success. A repeated delete has to be safe: it is
    exactly what a redelivered cleanup message asks for.

    The graph is erased in the same request. Removing the passages and
    keeping the names they mentioned would leave part of a document in the
    library after the document had gone.
    """
    documents = state.store.filter_documents(
        filters=scope_filter(payload.library_id, (payload.generation_id,))
    )
    if documents:
        state.store.delete_documents([document.id for document in documents])
    if state.graph is not None:
        with graph_store("this document's graph cannot be erased"):
            state.graph.erase(
                payload.library_id, payload.document_version_id, only=payload.generation_id
            )
    return DeleteResult(generation_id=payload.generation_id, deleted=len(documents))


@router.post("/purge", summary="Remove a version's chunks")
def purge_version(payload: PurgeRequest, state: State) -> DeleteResult:
    """Erase a version, optionally sparing the generation now in use.

    The generation to keep is excluded here rather than in the store's
    filter, because a not-equals filter is not something every backend
    supports the same way, and cleanup is the one operation where being
    approximately right means deleting the wrong thing.
    """
    documents = state.store.filter_documents(
        filters=version_filter(payload.library_id, payload.document_version_id)
    )
    keep = str(payload.keep_generation_id) if payload.keep_generation_id else None
    doomed = [
        document.id
        for document in documents
        if keep is None or str(document.meta.get(GENERATION_ID)) != keep
    ]
    if doomed:
        state.store.delete_documents(doomed)
    if state.graph is not None:
        with graph_store("this document's graph cannot be erased"):
            state.graph.erase(
                payload.library_id, payload.document_version_id, keep=payload.keep_generation_id
            )
    return DeleteResult(
        generation_id=payload.keep_generation_id or payload.document_version_id,
        deleted=len(doomed),
    )


def create_app(
    settings: Settings | None = None,
    store: DocumentStore | None = None,
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
