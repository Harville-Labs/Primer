"""Choosing passages that cover a library, without either store."""

from __future__ import annotations

from primer_retrieval.communities import communities, cover


def two_topics() -> list[tuple[str, str]]:
    """Five passages about chemistry and three about astronomy, sharing no entity."""
    chemistry = [(f"chem{i}", entity) for i in range(5) for entity in ("polonium", "radium")]
    astronomy = [(f"astro{i}", entity) for i in range(3) for entity in ("comet", "orbit")]
    return chemistry + astronomy


def test_passages_that_name_the_same_things_share_a_community() -> None:
    found = communities(two_topics(), largest=10, probes=10)

    assert [set(chunks) for chunks in found] == [
        {f"chem{i}" for i in range(5)},
        {f"astro{i}" for i in range(3)},
    ]


def test_the_largest_communities_are_the_ones_kept() -> None:
    """A library with more topics than are considered loses its smallest."""
    assert communities(two_topics(), largest=1, probes=10) == [[f"chem{i}" for i in range(5)]]


def test_a_community_offers_the_passages_that_name_most_of_it() -> None:
    """The passage naming three of a topic's entities is more about it than one naming one."""
    # Named so that id order is the reverse of centrality, which is the
    # order a tie-break mistaken for the ranking would produce. The second
    # topic is there because modularity needs something to separate from:
    # one dense cluster on its own scores no better split apart than whole.
    chemistry = ["polonium", "radium", "uranium", "thorium"]
    mentions = [("c-broad", entity) for entity in chemistry]
    mentions += [("b-middle", entity) for entity in chemistry[:3]]
    mentions += [("a-narrow", entity) for entity in chemistry[:2]]
    mentions += [(f"astro{i}", e) for i in range(5) for e in ("comet", "orbit", "moon")]

    assert communities(mentions, largest=2, probes=2)[1] == ["c-broad", "b-middle"]


def test_clustering_is_the_same_every_time() -> None:
    """Asking twice must read the same passages, on any replica."""
    mentions = [*two_topics(), ("bridge", "radium"), ("bridge", "comet")]

    assert communities(mentions, 10, 10) == communities(list(reversed(mentions)), 10, 10)


def test_a_library_with_no_mentions_has_no_communities() -> None:
    assert communities([], largest=10, probes=4) == []


def test_every_community_is_read_once_before_any_is_read_twice() -> None:
    """Breadth before depth: one example of each topic beats three of the nearest."""
    probed = [["a1", "a2", "a3"], ["b1", "b2"], ["c1"]]
    scores = {"a1": 0.9, "a2": 0.8, "a3": 0.7, "b1": 0.5, "b2": 0.4, "c1": 0.2}

    assert cover(probed, scores, limit=4) == ["a1", "b1", "c1", "a2"]


def test_the_community_nearest_the_question_goes_first() -> None:
    """Size orders the communities considered; the question orders their turns."""
    probed = [["big1", "big2"], ["small1"]]
    scores = {"big1": 0.1, "big2": 0.2, "small1": 0.9}

    assert cover(probed, scores, limit=2) == ["small1", "big2"]


def test_a_passage_the_scoped_search_did_not_return_is_never_chosen() -> None:
    """The graph suggests; the scoped vector read decides what the caller may see."""
    probed = [["visible", "hidden"], ["also-hidden"]]

    assert cover(probed, {"visible": 0.3}, limit=8) == ["visible"]
