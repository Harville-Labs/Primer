"""Fusing the graph's ranking with the vector one, without either store."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

import pytest
from neo4j.exceptions import CypherSyntaxError, ServiceUnavailable
from primer_retrieval.graph import GraphHit
from primer_retrieval.hybrid import blend, fuse, question_names, related_chunks


def test_fusion_puts_what_both_rankings_agree_on_first() -> None:
    """Second in both beats first in one: agreement is the signal fusion exists for."""
    assert fuse([["a", "b", "c"], ["d", "b", "e"]])[0] == "b"


def test_fusion_breaks_ties_in_favour_of_the_first_ranking() -> None:
    """Deterministic, and the vector ranking wins a draw."""
    assert fuse([["v1", "v2"], ["g1", "g2"]]) == ["v1", "g1", "v2", "g2"]


def test_fusion_with_one_ranking_keeps_its_order() -> None:
    assert fuse([["c", "a", "b"], []]) == ["c", "a", "b"]


def test_a_ranking_that_repeats_an_id_counts_it_once() -> None:
    """Otherwise one ranking could outvote the other by listing a hit twice."""
    assert fuse([["a", "b"], ["b", "b", "b", "a"]]) == ["a", "b"]


def test_fusion_of_nothing_is_nothing() -> None:
    assert fuse([[], []]) == []


def test_names_are_runs_of_whole_words_with_sentence_punctuation_stripped() -> None:
    names = question_names("Who did Marie Curie's (husband) marry?")
    assert {"Marie Curie", "husband", "marry", "Who did Marie"} <= set(names)
    assert "Curie's" not in names
    assert "arie" not in names


def test_punctuation_that_is_part_of_a_name_survives() -> None:
    assert "C++" in question_names("Who designed C++?")


def test_names_stop_at_the_longest_a_name_could_be() -> None:
    names = question_names("one two three four five six seven", max_words=6)
    assert "one two three four five six" in names
    assert "one two three four five six seven" not in names


@dataclass
class Hit:
    id: str
    score: float | None = None


def test_without_graph_hits_the_vector_hits_come_back_untouched() -> None:
    """A deployment with no graph must answer exactly as it did before."""
    hits: list[Any] = [Hit("a"), Hit("b")]

    def fetch(ids: list[str]) -> list[Any]:
        raise AssertionError("nothing should be fetched")

    assert blend(hits, [], fetch) is hits


def test_a_passage_only_the_graph_found_is_fetched_and_fused() -> None:
    fetched: list[list[str]] = []

    def fetch(ids: list[str]) -> list[Any]:
        fetched.append(ids)
        return [Hit(chunk_id) for chunk_id in ids]

    result = blend([Hit("v1"), Hit("shared")], ["shared", "g1"], fetch)

    assert fetched == [["g1"]]
    assert [hit.id for hit in result] == ["shared", "v1", "g1"]


def test_a_graph_id_the_scoped_fetch_withholds_is_dropped() -> None:
    """The fetch applies the caller's scope, so what it withholds the caller may not see."""
    result = blend([Hit("v1")], ["outside"], lambda ids: [])
    assert [hit.id for hit in result] == ["v1"]


class FakeGraph:
    def __init__(self, failure: Exception | None = None) -> None:
        self.failure = failure
        self.asked: list[dict[str, Any]] = []

    def neighbourhood(self, library_id, generation_ids, names, hops, limit) -> list[GraphHit]:
        self.asked.append({"names": names, "hops": hops, "limit": limit})
        if self.failure is not None:
            raise self.failure
        return [GraphHit(chunk_id=uuid.UUID(int=1), distance=0, support=1)]


def ask(graph: Any) -> list[str]:
    return related_chunks(graph, uuid.uuid4(), [uuid.uuid4()], "Marie Curie", hops=2, limit=5)


def test_related_chunks_names_what_the_question_mentions() -> None:
    graph = FakeGraph()
    assert ask(graph) == [str(uuid.UUID(int=1))]
    assert "Marie Curie" in graph.asked[0]["names"]
    assert (graph.asked[0]["hops"], graph.asked[0]["limit"]) == (2, 5)


def test_an_unreachable_graph_is_searched_past() -> None:
    assert ask(FakeGraph(ServiceUnavailable("down"))) == []


def test_a_mistake_in_a_graph_query_is_not_mistaken_for_an_outage() -> None:
    """Swallowing it would turn a bug into search quietly ignoring the graph."""
    with pytest.raises(CypherSyntaxError):
        ask(FakeGraph(CypherSyntaxError("bad query")))


def test_no_graph_means_no_related_chunks() -> None:
    assert ask(None) == []
