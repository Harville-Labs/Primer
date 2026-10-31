"""Which passages a question about a whole library reads.

A focused search reads the passages closest to the question, and for a
question like "what are the main themes here?" those all come from one
corner of the library. This module chooses passages that cover the library
instead: it clusters the graph of which passages mention which entities,
and takes passages from each cluster. See ADR 0006.

Everything here is pure. The mentions come in from the graph store, the
scores from the vector store, and what goes out is chunk ids, so the
choices can be tested without either.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence

import networkx as nx

#: Louvain starts from a random order. A fixed seed makes the same library
#: cluster the same way on every replica and every run, so asking the same
#: question twice reads the same passages.
SEED = 0


def communities(mentions: Iterable[tuple[str, str]], largest: int, probes: int) -> list[list[str]]:
    """The `largest` communities, each as its `probes` most central chunk ids.

    `mentions` is (chunk id, entity key) pairs. Chunks and entities are both
    nodes, so two passages land in one community because they name the same
    things, not because their wording is alike.

    A chunk's centrality is how many of its community's entities it
    mentions: the passage that names most of what a community is about is
    the one most likely to be about it. Ties, and communities of equal
    size, go to the smaller id, so the result never depends on hash order.
    """
    graph = nx.Graph()
    # Sorted, because Louvain visits nodes in insertion order and the graph
    # store returns mentions in no particular order. The seed alone fixes the
    # shuffle, not what is shuffled.
    graph.add_edges_from(
        (("chunk", chunk), ("entity", key)) for chunk, key in sorted(set(mentions))
    )
    if graph.number_of_nodes() == 0:
        return []
    found = []
    for members in nx.community.louvain_communities(graph, seed=SEED):
        chunks = sorted(node for kind, node in members if kind == "chunk")
        # An entity alone in a community has no passage to read.
        if chunks:
            found.append((members, chunks))
    found.sort(key=lambda item: (-len(item[1]), item[1][0]))
    central = []
    for members, chunks in found[:largest]:

        def degree(chunk: str, members: set[tuple[str, str]] = members) -> int:
            return sum(1 for neighbour in graph[("chunk", chunk)] if neighbour in members)

        central.append(sorted(chunks, key=lambda chunk: (-degree(chunk), chunk))[:probes])
    return central


def cover(probed: Sequence[Sequence[str]], scores: Mapping[str, float], limit: int) -> list[str]:
    """Up to `limit` chunk ids, one per community before two from any.

    A community's turn comes in the order of its best passage's score, so
    the parts of the library nearest the question are read first, and a
    library with more communities than `limit` drops the furthest ones.

    A chunk missing from `scores` is skipped. Scores come from a search
    scoped to the caller's library and generations, so a chunk it did not
    return is one the caller may not read, whatever the graph said.
    """
    ranked = [
        sorted((chunk for chunk in chunks if chunk in scores), key=lambda c: -scores[c])
        for chunks in probed
    ]
    ranked = [chunks for chunks in ranked if chunks]
    ranked.sort(key=lambda chunks: -scores[chunks[0]])
    chosen: list[str] = []
    for turn in range(max((len(chunks) for chunks in ranked), default=0)):
        for chunks in ranked:
            if turn < len(chunks) and len(chosen) < limit:
                chosen.append(chunks[turn])
    return chosen
