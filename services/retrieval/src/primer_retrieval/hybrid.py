"""Graph and vector rankings, combined.

A vector search finds passages that resemble the question. The graph finds
passages connected to what the question names, including ones that share no
words with it - the paper that extended a method, found because another
paper names both. Neither ranking is a better version of the other, so they
are fused rather than one being used to filter or re-score the other.

Fusion is by rank, not by score. A cosine similarity and a hop count are not
on any common scale, and inventing one would be a weighting nobody could
measure. Reciprocal rank fusion needs only the two orderings.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Sequence
from typing import TYPE_CHECKING, Any
from uuid import UUID

from primer_retrieval.errors import GRAPH_OUTAGES

if TYPE_CHECKING:
    from primer_retrieval.graph import GraphStore

logger = logging.getLogger(__name__)

#: The constant from the paper that introduced reciprocal rank fusion
#: (Cormack, Clarke and Büttcher, 2009), and the one most systems still use.
#: It damps the difference between first and second place, so one ranking's
#: top hit cannot outvote everything the other ranking agrees on.
RRF_K = 60

#: The longest entity name a question is searched for. Names are matched
#: as runs of whole words, and past six words a run is a clause, not a name.
MAX_NAME_WORDS = 6

#: Stripped from either end of a word before matching, so "Curie?" and
#: "(Curie" find Curie. Not every punctuation mark: "C++" and "C#" are names
#: whose last character is the point.
_SENTENCE_PUNCTUATION = ".,;:!?\"'()[]{}\u201c\u201d\u2018\u2019"


def question_names(question: str, max_words: int = MAX_NAME_WORDS) -> list[str]:
    """Every run of up to `max_words` words in the question.

    Entity linking by exact match against these, rather than by asking a
    model which entities the question names: a model call on every search
    would be the slowest step in answering, and the graph only knows names
    exactly as extraction wrote them anyway. The cost is that "Curie" does
    not find "Marie Curie" - the same shallowness the graph's own entity
    resolution has, and no worse.
    """
    words = []
    for raw in question.split():
        word = raw.strip(_SENTENCE_PUNCTUATION)
        for possessive in ("'s", "\u2019s"):
            word = word.removesuffix(possessive)
        if word:
            words.append(word)
    names = {
        " ".join(words[start : start + length])
        for start in range(len(words))
        for length in range(1, max_words + 1)
        if start + length <= len(words)
    }
    return sorted(names)


def fuse(rankings: Sequence[Sequence[str]], k: int = RRF_K) -> list[str]:
    """Reciprocal rank fusion: each id scores the sum of 1 / (k + rank).

    Ties go to the id that appeared first, reading the rankings in the order
    given, so the result never depends on hash order and the first ranking
    wins a draw. Search passes the vector ranking first: when the graph adds
    nothing that agrees or disagrees, the vector order is what comes back.
    """
    scores: dict[str, float] = {}
    for ranking in rankings:
        for rank, item in enumerate(dict.fromkeys(ranking), start=1):
            scores[item] = scores.get(item, 0.0) + 1.0 / (k + rank)
    first_seen = {item: index for index, item in enumerate(scores)}
    return sorted(scores, key=lambda item: (-scores[item], first_seen[item]))


def related_chunks(
    graph: GraphStore | None,
    library_id: UUID,
    generation_ids: Sequence[UUID],
    question: str,
    hops: int,
    limit: int,
) -> list[str]:
    """Chunk ids the graph ranks for this question, or none at all.

    A graph that is down is a worse answer, not a lost one: the vector
    results are still correct and are what Primer answered with before the
    graph existed. So an outage is logged and searched past. Anything else
    the driver raises is a mistake in Primer and is left to fail loudly.
    """
    if graph is None:
        return []
    try:
        hits = graph.neighbourhood(
            library_id, generation_ids, question_names(question), hops=hops, limit=limit
        )
    except GRAPH_OUTAGES:
        logger.warning(
            "the graph store could not be reached; searching vectors alone", exc_info=True
        )
        return []
    return [str(hit.chunk_id) for hit in hits]


def blend(
    vector_hits: list[Any],
    graph_ids: list[str],
    fetch: Callable[[list[str]], list[Any]],
    k: int = RRF_K,
) -> list[Any]:
    """The vector hits and the graph's chunks, in fused order.

    `fetch` reads passages the vector search did not return. It must apply
    the caller's scope: a chunk id from the graph is a suggestion of what to
    look at, never permission to see it, and an id the scoped fetch does not
    return is dropped here rather than trusted.

    With nothing from the graph, the vector hits come back untouched -
    same objects, same order - so a deployment without a graph answers
    exactly as it did before there was one.
    """
    if not graph_ids:
        return vector_hits
    by_id = {hit.id: hit for hit in vector_hits}
    missing = [chunk_id for chunk_id in graph_ids if chunk_id not in by_id]
    if missing:
        for hit in fetch(missing):
            by_id.setdefault(hit.id, hit)
    found = [chunk_id for chunk_id in graph_ids if chunk_id in by_id]
    order = fuse([[hit.id for hit in vector_hits], found], k)
    return [by_id[chunk_id] for chunk_id in order]
