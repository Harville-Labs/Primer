"""Failures only Retrieval can raise.

The envelope they travel in - `ProblemError`, `problem_response` - is
`primer_service.errors`, and is imported from there directly by whatever
needs it. This module re-exports none of it: a name that arrives through a
second module is a name whose home you have to go and look for.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Generator
from contextlib import contextmanager

from fastapi import status
from neo4j.exceptions import AuthError, ServiceUnavailable, SessionExpired, TransientError
from primer_contracts.errors import ErrorCode
from primer_service.errors import ProblemError

logger = logging.getLogger(__name__)

#: What the Neo4j driver raises when the graph is down, busy or gone, as
#: opposed to when Primer sent it something wrong. Shared by the write path,
#: which fails over it, and search, which carries on without the graph.
GRAPH_OUTAGES = (ServiceUnavailable, SessionExpired, TransientError)


def dependency_unavailable(detail: str) -> ProblemError:
    return ProblemError(
        code=ErrorCode.DEPENDENCY_UNAVAILABLE,
        title="Retrieval dependency unavailable",
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail=detail,
    )


class UnembeddedChunks(RuntimeError):
    """Chunks came back from the embedder with no vector on them.

    Its own type because it is not the endpoint being down - the call
    succeeded - and the two need different words in front of an operator.
    """

    def __init__(self, missing: int, total: int) -> None:
        self.missing = missing
        self.total = total
        super().__init__(f"{missing} of {total} chunks came back without a vector.")


@contextmanager
def embedding_endpoint(consequence: str) -> Generator[None]:
    """Mark a block that cannot run without the embedding endpoint.

    An unreachable embedder is a deployment fault, not a bad request, and it
    is the single most likely thing to be wrong on a self-hosted install: it
    is a separate process that has to be running and reachable. Left
    unhandled it surfaces as a bare 500, and a 500 is the shape every
    unrelated bug also has - so whoever reads it goes looking at Primer
    rather than at the endpoint that is actually down.

    `consequence` completes the sentence the caller reads, because what
    cannot happen is the part that differs: a library that cannot be
    searched and a document that cannot be indexed are the same fault
    arriving at two different people.

    Everything is caught, not one exception type. The embedder is a library
    call over a network to somebody else's server, and what it raises for a
    refused connection, a timeout, a bad key, or a model that is not loaded
    is not a set this module can enumerate and should not pretend to.
    """
    try:
        yield
    except UnembeddedChunks as error:
        # Reached, and answered, and still did not embed everything - so
        # saying it could not be reached would send whoever reads this to
        # check a service that is up. 503 all the same: it is transient and
        # the caller should retry rather than record a partial index.
        logger.warning(
            "the embedding endpoint returned %d of %d chunks without a vector",
            error.missing,
            error.total,
            exc_info=True,
        )
        raise dependency_unavailable(
            f"The embedding endpoint returned {error.missing} of {error.total} chunks "
            f"without a vector, so {consequence}."
        ) from error
    except Exception as error:
        logger.warning("the embedding endpoint could not be reached", exc_info=True)
        raise dependency_unavailable(
            f"The embedding endpoint could not be reached, so {consequence}."
        ) from error


#: What Qdrant says when a vector's width does not match its collection's,
#: in the two places it says it. The integration refuses to open a
#: collection created at another width, naming only the stored one; Qdrant
#: itself refuses a point of the wrong width, naming both. The stored width
#: is the one an operator cannot otherwise see.
COLLECTION_WIDTH = re.compile(r"configured with a vector size '(\d+)'")
POINT_WIDTH = re.compile(r"expected dim: (\d+), got (\d+)")


def dimension_mismatch(message: str, configured: int) -> tuple[str, str] | None:
    """The stored and offered widths, if `message` is a width mismatch."""
    if point := POINT_WIDTH.search(message):
        return point.group(1), point.group(2)
    if collection := COLLECTION_WIDTH.search(message):
        return collection.group(1), str(configured)
    return None


def wrong_dimensions(stored: str, offered: str, consequence: str) -> ProblemError:
    """The embedding model was changed without rebuilding the store.

    A conflict rather than an unavailable dependency, and deliberately a 4xx:
    nothing is down and nothing is coming back. A worker reading this must
    stop rather than retry, because every retry will be refused identically
    and the budget spent before anyone is told.

    This is the one failure the chart warns about and cannot check for
    itself - a collection keeps the width it was created with, so a
    deployment that swaps embedding models looks healthy right up until the
    first document is indexed.
    """
    return ProblemError(
        code=ErrorCode.CONFLICT,
        title="Embedding size does not match the vector store",
        status_code=status.HTTP_409_CONFLICT,
        detail=(
            f"The vector store holds {stored}-dimensional vectors and this deployment is "
            f"producing {offered}-dimensional ones, so {consequence}. A collection keeps "
            "the width it was created with, so changing the embedding model means deleting "
            "the Qdrant collection and reindexing, or setting the model back to the one that "
            "matches."
        ),
    )


@contextmanager
def vector_store(consequence: str, configured: int) -> Generator[None]:
    """Mark a block that cannot run unless the vector store accepts a write.

    `configured` is the width this deployment embeds at. The integration's
    refusal to open a collection names only the width it was created with,
    and an operator needs both numbers to know which side to change.

    Writing is the half of the store that was never guarded. A search that
    could not reach its backend said so; an index that could not write to it
    raised, became a bare 500, and reached the user as "Retrieval answered
    500" - which says only that Retrieval was reached, and sends whoever
    reads it nowhere in particular.

    Everything is caught rather than one exception type: what a document
    store raises for a refused connection, a full disk, a schema that does
    not match, or a driver that has given up is a set that changes with the
    backend, and this module should not pretend to enumerate it.
    """
    try:
        yield
    except Exception as error:
        mismatch = dimension_mismatch(str(error), configured)
        if mismatch:
            logger.error("the vector store collection does not match the embedding model")
            raise wrong_dimensions(*mismatch, consequence) from error
        logger.warning("the vector store refused a write", exc_info=True)
        raise dependency_unavailable(
            f"The vector store could not be written to, so {consequence}."
        ) from error


def no_graph_store(consequence: str) -> ProblemError:
    """A worker extracted a graph for a Retrieval that has nowhere to put it.

    A conflict between two halves of one deployment's configuration, not an
    outage, so a 4xx: the worker must stop rather than retry, because every
    retry is refused the same way until an operator changes one side.
    """
    return ProblemError(
        code=ErrorCode.CONFLICT,
        title="Retrieval has no graph store",
        status_code=status.HTTP_409_CONFLICT,
        detail=(
            f"Retrieval has no graph store configured, so {consequence}. Set PRIMER_GRAPH_URL "
            "on Retrieval, or turn graph extraction off on the ingestion worker by unsetting "
            "PRIMER_GRAPH_EXTRACTION_MODEL."
        ),
    )


def too_many_mentions(limit: int) -> ProblemError:
    """A library too large to cluster for a library-wide question.

    A 4xx with its own code, because asking again gets the same answer and
    the caller has to tell it apart from a malformed request. Not answered
    from a focused search instead: the user asked about the whole library,
    and an answer about one corner of it would look like what they asked
    for.
    """
    return ProblemError(
        code=ErrorCode.QUOTA_EXCEEDED,
        title="Library too large to ask across",
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        detail=(
            f"This library's passages mention entities more than {limit:,} times, which is "
            "more than this deployment will group into topics. Ask a focused question "
            "instead, or ask an operator to raise PRIMER_GRAPH_COMMUNITY_MAX_MENTIONS."
        ),
    )


@contextmanager
def graph_store(consequence: str) -> Generator[None]:
    """Mark a block that cannot run without the knowledge graph.

    Named apart from the vector store because it is a different server. An
    operator told the vector store was unreachable would check Qdrant while
    Neo4j was the one down.

    Unlike the embedder and the vector store, this catches named exceptions
    rather than everything. The graph is reached through one driver whose
    errors are a closed hierarchy, so a Cypher mistake can be told apart
    from an outage - and it has to be, because reporting a bug in Primer as
    an unreachable dependency sends whoever reads it to restart a database
    that is fine.
    """
    try:
        yield
    except AuthError as error:
        logger.warning("the graph store refused Primer's credentials", exc_info=True)
        raise dependency_unavailable(
            f"The graph store refused Primer's credentials, so {consequence}. "
            "Check PRIMER_GRAPH_USERNAME and PRIMER_GRAPH_PASSWORD."
        ) from error
    except GRAPH_OUTAGES as error:
        logger.warning("the graph store could not be reached", exc_info=True)
        raise dependency_unavailable(
            f"The graph store could not be reached, so {consequence}."
        ) from error
