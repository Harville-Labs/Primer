"""Reordering what the vector search found, by reading it properly.

A vector search compares a question to a passage through two embeddings that
were made without knowing about each other. It is fast and it is
approximate, and the approximation shows in the ordering: the passage that
actually answers the question is often in the first twenty results but not
in the first six.

A reranker reads the pair together and scores it. That is far more expensive
per passage, which is the whole design: search widely and cheaply, then read
a shortlist carefully. Fetching twenty and keeping six costs one reranker
pass over twenty short texts and buys an ordering that a vector search
cannot produce at any `top_k`.

Off unless configured. Compose and the chart configure the reranker they
bundle, but either can be turned off, and a deployment without a reranker
endpoint must behave exactly as it did before this existed - the vector
ordering, truncated to what was asked for.

The protocol is a `/rerank` endpoint, which text-embeddings-inference,
vLLM, Cohere and Jina all serve - though not identically, and the
differences are load-bearing. TEI reads the passages from `texts` and
returns `score`; the others read `documents` and return `relevance_score`.
Primer sends both names and reads either, because an operator may swap the
bundled server for anyone's.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import httpx2

logger = logging.getLogger(__name__)


def _score_of(entry: dict) -> float:
    """The relevance this server put on a passage, under either name.

    `relevance_score` is Cohere's and `score` is TEI's. Reading only one
    with a default is worse than failing: every passage comes back scored
    zero, the ordering still looks reranked because the indices are
    honoured, and a relevance floor then discards the entire result.
    """
    for key in ("relevance_score", "score"):
        if key in entry:
            return float(entry[key])
    raise KeyError(f"a rerank result carried no score: {sorted(entry)}")


@dataclass(frozen=True)
class Reranked:
    """One passage's place after reading, by its position in the input."""

    index: int
    score: float


class Reranker:
    """Scores question-and-passage pairs against a configured endpoint."""

    def __init__(
        self, base_url: str, model: str, api_key: str | None, timeout_seconds: float
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._api_key = api_key
        self._timeout = timeout_seconds

    def rank(self, query: str, passages: list[str], keep: int) -> list[Reranked]:
        """Order these passages by how well they answer the question.

        Returns positions rather than the passages themselves, so the caller
        keeps whatever it had attached to each one - the chunk id, the page,
        the document it came from.
        """
        if not passages:
            return []
        response = httpx2.post(
            f"{self._base_url}/rerank",
            timeout=self._timeout,
            headers={"Authorization": f"Bearer {self._api_key or 'none'}"},
            # The passages are sent under both names because the servers
            # that speak this endpoint do not agree on one.
            # text-embeddings-inference reads `texts` and rejects a request
            # without it; Cohere, vLLM and Jina read `documents`. Sending
            # both satisfies either, and the one that is not read is
            # ignored rather than refused - checked against TEI, which
            # accepts the pair and answers normally.
            #
            # Probing instead would mean a wasted round trip on every
            # deployment whose server was not guessed first, and hardcoding
            # one would make Primer's choice of endpoint a fork.
            json={
                "model": self._model,
                "query": query,
                "texts": passages,
                "documents": passages,
                "top_n": keep,
            },
        )
        response.raise_for_status()
        body = response.json()
        # Servers differ on the envelope - some return a bare list, some wrap
        # it in `results` - and agree on the entries.
        entries = body.get("results", body) if isinstance(body, dict) else body
        ranked = [Reranked(index=int(entry["index"]), score=_score_of(entry)) for entry in entries]
        return ranked[:keep]


def reorder(
    reranker: Reranker | None,
    query: str,
    hits: list,
    text_of,
    keep: int,
) -> list:
    """Rerank if a reranker is configured, and never fail the search over it.

    A reranker that is down is a worse ordering, not a lost answer. The
    vector results are already correct and already the answer Primer would
    have given a moment ago, so a failure here falls back to them rather
    than turning a working search into an error.
    """
    if reranker is None or not hits:
        return hits[:keep]
    try:
        ranked = reranker.rank(query, [text_of(hit) for hit in hits], keep)
    except Exception:
        logger.warning("reranking failed; falling back to the vector ordering", exc_info=True)
        return hits[:keep]
    if not ranked:
        return hits[:keep]
    # An index the server invented would be a passage nobody retrieved, so
    # anything out of range is dropped rather than trusted.
    kept = []
    for entry in ranked:
        if not 0 <= entry.index < len(hits):
            continue
        hit = hits[entry.index]
        # The reranker's score replaces the vector one, because from here on
        # it is the score that means something: it decided this ordering, and
        # leaving the cosine value in place would report a number that no
        # longer explains the position it sits at. A relevance floor reading
        # these would also be comparing against the wrong scale entirely -
        # cosine from this embedding model runs about 0.19 to 0.69, while a
        # cross-encoder's output is far wider and differently shaped.
        hit.score = entry.score
        kept.append(hit)
    return kept
