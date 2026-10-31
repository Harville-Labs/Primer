"""Library-wide questions: the graph and cache around `communities`.

Kept apart from the pure choices in `communities` so those can be tested
without a graph, and apart from the search route so that route reads as
the two kinds of search it serves rather than the mechanics of one.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Sequence
from functools import lru_cache
from typing import TYPE_CHECKING, Any
from uuid import UUID

from primer_retrieval.communities import communities, cover
from primer_retrieval.errors import GRAPH_OUTAGES, too_many_mentions

if TYPE_CHECKING:
    from primer_retrieval.config import Settings
    from primer_retrieval.graph import GraphStore

logger = logging.getLogger(__name__)

#: Central passages scored per community. One would make a community's turn
#: depend on whether its single most central passage happened to resemble
#: the question; four gives it a choice without scoring the whole library.
PROBES = 4

#: Libraries whose clustering one replica keeps. Each entry is a few hundred
#: chunk ids, so this bounds memory, not correctness.
CACHE_SIZE = 32


class Communities:
    """A library's communities, cached by the generations they came from.

    Keyed by generation set because a library's active generations change
    exactly when its contents do, so an entry can never be stale and needs
    no invalidation. A failure is not cached: an outage or a refusal is
    asked again next time, and the next time may succeed.
    """

    def __init__(self, graph: GraphStore, settings: Settings) -> None:
        self._graph = graph
        self._max_mentions = settings.graph_community_max_mentions
        self._largest = settings.graph_communities
        self._cached = lru_cache(maxsize=CACHE_SIZE)(self._compute)

    def get(self, library_id: UUID, generation_ids: Sequence[UUID]) -> tuple[tuple[str, ...], ...]:
        return self._cached(library_id, tuple(sorted(generation_ids)))

    def _compute(
        self, library_id: UUID, generation_ids: tuple[UUID, ...]
    ) -> tuple[tuple[str, ...], ...]:
        # One more than the limit, so a library exactly at it is answered
        # and one past it is caught without reading the rest.
        found = self._graph.mentions(library_id, generation_ids, self._max_mentions + 1)
        if len(found) > self._max_mentions:
            raise too_many_mentions(self._max_mentions)
        return tuple(tuple(chunks) for chunks in communities(found, self._largest, PROBES))


def library_wide(
    cache: Communities | None,
    library_id: UUID,
    generation_ids: Sequence[UUID],
    limit: int,
    fetch: Callable[[list[str]], list[Any]],
) -> list[Any] | None:
    """Passages covering the library, or None to answer as a focused search.

    `fetch` reads passages by id and must apply the caller's scope, for the
    same reason it must in `hybrid.blend`. Its scores are the store's
    similarity to the question, and they decide only which community goes
    first and which of its passages it offers.

    None when there is no graph, the graph is down, or nothing in the
    library mentions an entity. Each of those leaves the focused search as
    the best answer there is, which is the rule focused search follows when
    the graph is down.
    """
    if cache is None:
        return None
    try:
        probed = cache.get(library_id, generation_ids)
    except GRAPH_OUTAGES:
        logger.warning(
            "the graph store could not be reached; answering a library-wide question "
            "as a focused one",
            exc_info=True,
        )
        return None
    wanted = [chunk for chunks in probed for chunk in chunks]
    if not wanted:
        return None
    hits = {hit.id: hit for hit in fetch(wanted)}
    chosen = cover(probed, {chunk: hit.score or 0.0 for chunk, hit in hits.items()}, limit)
    return [hits[chunk] for chunk in chosen] or None
