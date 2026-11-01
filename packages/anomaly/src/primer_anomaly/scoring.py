"""Score a sequence end to end: per-token numbers, then the spans.

There are two entry points rather than one with an option, because the
inputs differ in kind. Lab has the whole distribution at every position;
an endpoint returns a token's logprob and its top k. Keeping them apart
means a caller cannot hand top-k rows to the full-entropy path by mistake.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

import numpy.typing as npt

from primer_anomaly.measures import (
    EmptySequence,
    entropy_bits,
    perplexity,
    surprisal_bits,
    truncated_entropy_bits,
)
from primer_anomaly.models import EntropyEstimate, SequenceScore, Span, SpanKind, TokenScore
from primer_anomaly.spans import SPIKE_THRESHOLD, find_spans

#: Entropy drifts more gradually than surprisal spikes, so a run of tokens
#: modestly above the sequence's usual uncertainty is what marks a stretch
#: the model was unsure of. One hesitant token is not a run.
LOW_CONFIDENCE_THRESHOLD = 2.0
LOW_CONFIDENCE_MIN_LENGTH = 3


@dataclass(frozen=True, slots=True)
class TopLogprobs:
    """One generated token, as an OpenAI-compatible endpoint reports it."""

    token: str
    logprob: float
    top_logprobs: Sequence[float]


def score_full(
    tokens: Sequence[str], logprobs: Sequence[float], distributions: npt.ArrayLike
) -> SequenceScore:
    """Score tokens whose full next-token distributions are known.

    `distributions` holds one `log_softmax` row per token, over the whole
    vocabulary; `logprobs[i]` is the row's value at the token that came.
    """
    if not tokens:
        raise EmptySequence("a score")
    return score_exact(tokens, logprobs, entropy_bits(distributions).tolist())


def score_exact(
    tokens: Sequence[str], logprobs: Sequence[float], entropies: Sequence[float]
) -> SequenceScore:
    """Score tokens whose exact entropies were measured with `entropy_bits`.

    For a sequence too long to hold every distribution at once: at a
    vocabulary of 150 thousand, six hundred positions in float64 are most
    of a gigabyte, so Lab measures them a block of rows at a time and
    passes what it measured. `entropy_bits` refuses anything that is not a
    whole distribution, which is what makes these exact.
    """
    if not tokens:
        raise EmptySequence("a score")
    if not len(tokens) == len(logprobs) == len(entropies):
        raise ValueError(
            f"Scoring needs one logprob and one distribution per token; got "
            f"{len(tokens)} tokens, {len(logprobs)} logprobs, {len(entropies)} distributions."
        )
    scores = [
        TokenScore(
            index=index,
            token=token,
            logprob=logprob,
            surprisal_bits=surprisal_bits(logprob),
            entropy_bits=entropy,
        )
        for index, (token, logprob, entropy) in enumerate(
            zip(tokens, logprobs, entropies, strict=True)
        )
    ]
    return SequenceScore(
        estimate=EntropyEstimate.FULL,
        perplexity=perplexity(logprobs),
        tokens=scores,
        spans=_spans(scores, list(entropies)),
    )


def score_truncated(tokens: Sequence[TopLogprobs]) -> SequenceScore:
    """Score tokens from an endpoint's logprobs and top-k alternatives.

    `top_k` is the most alternatives any token carried. An endpoint may
    return fewer than were asked for near the end of its vocabulary, and
    the label should describe what was seen, not what was requested.
    """
    if not tokens:
        raise EmptySequence("a score")
    entropies = [truncated_entropy_bits(token.top_logprobs) for token in tokens]
    scores = [
        TokenScore(
            index=index,
            token=token.token,
            logprob=token.logprob,
            surprisal_bits=surprisal_bits(token.logprob),
            truncated_entropy_bits=entropy,
        )
        for index, (token, entropy) in enumerate(zip(tokens, entropies, strict=True))
    ]
    return SequenceScore(
        estimate=EntropyEstimate.TRUNCATED,
        top_k=max(len(token.top_logprobs) for token in tokens),
        perplexity=perplexity([token.logprob for token in tokens]),
        tokens=scores,
        spans=_spans(scores, entropies),
    )


def _spans(scores: Sequence[TokenScore], entropies: Sequence[float]) -> list[Span]:
    spikes = find_spans(
        [score.surprisal_bits for score in scores],
        SpanKind.SURPRISAL_SPIKE,
        threshold=SPIKE_THRESHOLD,
    )
    unsure = find_spans(
        entropies,
        SpanKind.LOW_CONFIDENCE,
        threshold=LOW_CONFIDENCE_THRESHOLD,
        min_length=LOW_CONFIDENCE_MIN_LENGTH,
    )
    return sorted([*spikes, *unsure], key=lambda span: (span.start, span.kind))
