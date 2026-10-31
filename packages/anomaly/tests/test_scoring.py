import math

import numpy as np
import pytest
from primer_anomaly import (
    EmptySequence,
    EntropyEstimate,
    SequenceScore,
    SpanKind,
    TokenScore,
    TopLogprobs,
    find_spans,
    robust_z_scores,
    score_full,
    score_truncated,
)
from pydantic import ValidationError

FLAT = math.log(0.9)
SPIKE = math.log(0.001)


def _flat_with_spike_at(position: int, length: int = 12) -> list[float]:
    return [SPIKE if index == position else FLAT for index in range(length)]


def test_a_single_spike_in_a_flat_sequence_is_the_only_span() -> None:
    """The MAD of a flat sequence is zero; a naive z-score divides by it."""
    surprisals = [-logprob for logprob in _flat_with_spike_at(7)]
    spans = find_spans(surprisals, SpanKind.SURPRISAL_SPIKE)
    assert [(span.start, span.end, span.peak_index) for span in spans] == [(7, 8, 7)]
    assert math.isfinite(spans[0].peak_z)


def test_an_identical_sequence_scores_zero_everywhere_and_flags_nothing() -> None:
    assert robust_z_scores([2.0] * 6) == [0.0] * 6
    assert find_spans([2.0] * 6, SpanKind.SURPRISAL_SPIKE) == []


def test_a_low_confidence_run_shorter_than_the_minimum_is_not_a_span() -> None:
    values = [0.1] * 10 + [5.0] * 2 + [0.1] * 10
    assert find_spans(values, SpanKind.LOW_CONFIDENCE, threshold=2.0, min_length=3) == []
    longer = [0.1] * 10 + [5.0] * 3 + [0.1] * 10
    spans = find_spans(longer, SpanKind.LOW_CONFIDENCE, threshold=2.0, min_length=3)
    assert [(span.start, span.end) for span in spans] == [(10, 13)]


def test_a_spike_at_the_last_token_closes_its_span() -> None:
    surprisals = [-logprob for logprob in _flat_with_spike_at(11)]
    spans = find_spans(surprisals, SpanKind.SURPRISAL_SPIKE)
    assert [(span.start, span.end) for span in spans] == [(11, 12)]


def test_an_endpoint_score_is_labelled_truncated_with_the_k_it_saw() -> None:
    tokens = [
        TopLogprobs(token=f"t{index}", logprob=logprob, top_logprobs=[logprob, math.log(0.05)])
        for index, logprob in enumerate(_flat_with_spike_at(4))
    ]
    score = score_truncated(tokens)
    assert score.estimate is EntropyEstimate.TRUNCATED
    assert score.top_k == 2
    assert all(token.entropy_bits is None for token in score.tokens)
    assert all(token.truncated_entropy_bits is not None for token in score.tokens)
    assert [span.start for span in score.spans if span.kind is SpanKind.SURPRISAL_SPIKE] == [4]


def test_a_local_score_carries_exact_entropy_and_no_truncated_field() -> None:
    logprobs = _flat_with_spike_at(2, length=6)
    rows = np.tile(np.log([0.9, 0.05, 0.05]), (6, 1))
    score = score_full([f"t{index}" for index in range(6)], logprobs, rows)
    assert score.estimate is EntropyEstimate.FULL
    assert score.top_k is None
    assert all(token.truncated_entropy_bits is None for token in score.tokens)
    assert score.tokens[0].entropy_bits == pytest.approx(0.569, abs=1e-3)


def test_full_and_truncated_entropy_are_serialized_under_different_keys() -> None:
    """A consumer reading `entropy_bits` must never receive a lower bound."""
    tokens = [TopLogprobs(token="a", logprob=FLAT, top_logprobs=[FLAT])]
    dumped = score_truncated(tokens).model_dump()["tokens"][0]
    assert dumped["entropy_bits"] is None
    assert dumped["truncated_entropy_bits"] > 0


def test_a_token_cannot_carry_both_entropies() -> None:
    with pytest.raises(ValidationError, match="exactly one"):
        TokenScore(
            index=0,
            token="a",
            logprob=FLAT,
            surprisal_bits=0.1,
            entropy_bits=0.5,
            truncated_entropy_bits=0.4,
        )


def test_a_truncated_score_cannot_hold_exact_entropies() -> None:
    token = TokenScore(index=0, token="a", logprob=FLAT, surprisal_bits=0.1, entropy_bits=0.5)
    with pytest.raises(ValidationError, match="truncated_entropy_bits"):
        SequenceScore(
            estimate=EntropyEstimate.TRUNCATED, top_k=20, perplexity=1.1, tokens=[token], spans=[]
        )


def test_score_full_refuses_mismatched_lengths() -> None:
    rows = np.tile(np.log([0.5, 0.5]), (2, 1))
    with pytest.raises(ValueError, match="one logprob and one distribution per token"):
        score_full(["a", "b", "c"], [FLAT] * 3, rows)


def test_scoring_no_tokens_is_an_error() -> None:
    with pytest.raises(EmptySequence):
        score_truncated([])
    with pytest.raises(EmptySequence):
        score_full([], [], np.empty((0, 4)))
