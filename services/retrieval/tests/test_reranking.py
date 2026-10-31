"""Reordering a shortlist, and what happens when that cannot be done.

The reranker is an improvement to an ordering that is already correct. So
the properties worth pinning are mostly about it getting out of the way: a
deployment without one behaves exactly as it did before, and one whose
reranker is down returns the vector ordering rather than an error.

A search that failed because the optional stage failed would be a
regression dressed as a feature.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest
from primer_retrieval.reranking import Reranked, Reranker, reorder


@dataclass
class Hit:
    """Stands in for a retrieved chunk: its text is read, its score written."""

    content: str
    score: float | None = None


def hits(*texts: str) -> list[Hit]:
    return [Hit(content=text) for text in texts]


def text_of(hit: Hit) -> str:
    return hit.content


class Fake(Reranker):
    """A reranker that returns a fixed order, or refuses."""

    def __init__(self, order: list[int] | None = None, fail: bool = False) -> None:
        self.order = order or []
        self.fail = fail
        self.asked: list[tuple[str, int]] = []

    def rank(self, query: str, passages: list[str], keep: int) -> list[Reranked]:
        self.asked.append((query, len(passages)))
        if self.fail:
            raise RuntimeError("the reranker went away")
        return [Reranked(index=index, score=1.0) for index in self.order][:keep]


def test_with_no_reranker_the_vector_order_is_kept() -> None:
    """A deployment without one behaves exactly as it did before."""
    found = hits("a", "b", "c")

    assert reorder(None, "q", found, text_of, 2) == found[:2]


def test_the_reranker_decides_the_order() -> None:
    found = hits("first", "second", "third")
    reordered = reorder(Fake(order=[2, 0, 1]), "q", found, text_of, 2)

    assert [hit.content for hit in reordered] == ["third", "first"]


def test_a_failing_reranker_falls_back_rather_than_failing_the_search() -> None:
    """The one that matters. The vector results are already an answer."""
    found = hits("a", "b", "c")

    assert reorder(Fake(fail=True), "q", found, text_of, 2) == found[:2]


def test_a_reranker_returning_nothing_falls_back_too() -> None:
    """An empty ordering is not an instruction to answer with nothing."""
    found = hits("a", "b", "c")

    assert reorder(Fake(order=[]), "q", found, text_of, 2) == found[:2]


def test_an_index_nobody_retrieved_is_dropped() -> None:
    """A server inventing a position must not index into the wrong passage.

    Out of range is dropped rather than clamped: clamping would silently
    return a passage the reranker never scored, and cite it.
    """
    found = hits("a", "b")
    reordered = reorder(Fake(order=[1, 99, -1, 0]), "q", found, text_of, 4)

    assert [hit.content for hit in reordered] == ["b", "a"]


def test_it_is_given_every_candidate_and_asked_for_the_answer_size() -> None:
    """Search widely, keep a few - the whole point of the two numbers."""
    reranker = Fake(order=[0])
    reorder(reranker, "q", hits(*[str(index) for index in range(20)]), text_of, 6)

    assert reranker.asked == [("q", 20)]


def test_nothing_retrieved_asks_nothing() -> None:
    """A library with no match should not cost a reranker call."""
    reranker = Fake(order=[])
    assert reorder(reranker, "q", [], text_of, 6) == []
    assert reranker.asked == []


@pytest.mark.parametrize("keep", [1, 3, 50])
def test_never_more_than_asked_for(keep: int) -> None:
    found = hits(*[str(index) for index in range(10)])
    assert len(reorder(Fake(order=list(range(10))), "q", found, text_of, keep)) <= keep


# --- The relevance floor ------------------------------------------------
#
# A vector search always returns its top k, so a question the library
# cannot answer comes back with a full set of passages that are merely the
# closest of a bad lot. Measured on a live library before this existed:
# "Describe the plot of the film Casablanca" scored 0.392 against a corpus
# of mortgage paperwork, beating a question the corpus did answer.


@dataclass
class Scored:
    """Stands in for a retrieved chunk that has been ranked."""

    content: str
    score: float | None


def settings_with(floor: float | None):
    from primer_retrieval.config import Settings

    return Settings(min_score=floor)


def test_nothing_is_dropped_when_no_floor_is_set() -> None:
    """The default, and every deployment that predates this."""
    from primer_retrieval.app import above_floor

    found = [Scored("a", 0.9), Scored("b", 0.01)]

    assert above_floor(found, settings_with(None)) == found


def test_passages_below_the_floor_are_not_returned() -> None:
    from primer_retrieval.app import above_floor

    found = [Scored("answers it", 0.71), Scored("near miss", 0.44), Scored("unrelated", 0.19)]

    kept = above_floor(found, settings_with(0.5))

    assert [hit.content for hit in kept] == ["answers it"]


def test_a_question_the_library_cannot_answer_returns_nothing() -> None:
    """Returning fewer than `limit` is the point, not a side effect.

    An empty result is a true statement about the corpus. A full one made
    of the least bad passages is not, and everything downstream reads it as
    evidence.
    """
    from primer_retrieval.app import above_floor

    assert above_floor([Scored("x", 0.33), Scored("y", 0.31)], settings_with(0.5)) == []


def test_an_unscored_passage_is_treated_as_no_match() -> None:
    """A missing score is not a high one.

    Reading None as zero keeps a store that omits scores from silently
    admitting everything the floor exists to exclude.
    """
    from primer_retrieval.app import above_floor

    assert above_floor([Scored("x", None)], settings_with(0.1)) == []


# --- The wire format ----------------------------------------------------
#
# `reorder` swallows a failed rerank and falls back to the vector ordering,
# which is right - a reranker that is down should not lose the answer - and
# it means every mistake in this file is silent. A reranker that 422s on
# every request looks exactly like one that is working.
#
# Checked against a running text-embeddings-inference: Primer's original
# payload was refused with "missing field `texts`", and its response parser
# read `relevance_score` from a body that carries `score`.


class Recorder:
    """Stands in for the HTTP call, capturing what was sent."""

    def __init__(self, body: Any) -> None:
        self.body = body
        self.sent: dict[str, Any] = {}

    def __call__(self, url: str, **kwargs: Any) -> Recorder:
        self.sent = kwargs["json"]
        return self

    def raise_for_status(self) -> None:
        return None

    def json(self) -> Any:
        return self.body


def rank_with(monkeypatch: pytest.MonkeyPatch, body: Any) -> tuple[list[Reranked], dict[str, Any]]:
    from primer_retrieval import reranking

    recorder = Recorder(body)
    monkeypatch.setattr(reranking.httpx2, "post", recorder)
    reranker = Reranker("http://rerank", "a-model", None, 5.0)
    return reranker.rank("a question", ["first", "second"], keep=2), recorder.sent


def test_the_passages_are_sent_under_both_names(monkeypatch: pytest.MonkeyPatch) -> None:
    """TEI reads `texts` and refuses without it; the others read `documents`.

    Primer holds no model of its own, so it does not get to pick whose
    server this is.
    """
    _, sent = rank_with(monkeypatch, [{"index": 0, "score": 0.9}])

    assert sent["texts"] == ["first", "second"]
    assert sent["documents"] == ["first", "second"]


def test_a_tei_score_is_read(monkeypatch: pytest.MonkeyPatch) -> None:
    """TEI returns a bare list of `score`."""
    ranked, _ = rank_with(
        monkeypatch, [{"index": 1, "score": 0.48}, {"index": 0, "score": 0.00004}]
    )

    assert [(r.index, round(r.score, 5)) for r in ranked] == [(1, 0.48), (0, 0.00004)]


def test_a_cohere_relevance_score_is_read(monkeypatch: pytest.MonkeyPatch) -> None:
    """Cohere, vLLM and Jina wrap results and call the field something else."""
    ranked, _ = rank_with(monkeypatch, {"results": [{"index": 1, "relevance_score": 0.7}]})

    assert [(r.index, r.score) for r in ranked] == [(1, 0.7)]


def test_a_result_with_no_score_is_not_read_as_zero(monkeypatch: pytest.MonkeyPatch) -> None:
    """Defaulting to zero is worse than failing.

    Every passage comes back scored zero, the ordering still looks reranked
    because the indices are honoured, and a relevance floor then discards
    the whole result - with nothing anywhere saying why.
    """
    with pytest.raises(KeyError):
        rank_with(monkeypatch, [{"index": 0}])


class Ranks(Reranker):
    """A reranker that returns exactly these entries, scores included."""

    def __init__(self, entries: list[Reranked]) -> None:
        self.entries = entries

    def rank(self, query: str, passages: list[str], keep: int) -> list[Reranked]:
        return self.entries[:keep]


def test_the_rerankers_score_replaces_the_vector_one() -> None:
    """From here on it is the score that means something.

    It decided the ordering, so leaving the cosine value in place would
    report a number that no longer explains the position it sits at - and a
    relevance floor reading these would compare against the wrong scale
    entirely. Cosine from the embedding model runs about 0.19 to 0.69,
    while a cross-encoder's output is far wider and differently shaped.
    """
    found = [Hit("first", score=0.61), Hit("second", score=0.58)]

    kept = reorder(Ranks([Reranked(1, 0.84), Reranked(0, 0.0003)]), "q", found, text_of, keep=2)

    assert [(hit.content, hit.score) for hit in kept] == [("second", 0.84), ("first", 0.0003)]


def test_a_failed_rerank_leaves_the_vector_scores_alone() -> None:
    """The fallback is the ordering Primer would have given a moment ago,
    and its scores are the ones that produced it."""
    found = [Hit("first", score=0.61), Hit("second", score=0.58)]

    kept = reorder(Fake(fail=True), "q", found, text_of, keep=2)

    assert [(hit.content, hit.score) for hit in kept] == [("first", 0.61), ("second", 0.58)]
