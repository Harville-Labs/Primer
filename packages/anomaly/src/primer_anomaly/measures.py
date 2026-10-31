"""Surprisal, perplexity, and entropy of a model's predictions.

Everything is in bits. Logprobs arrive in nats, because that is what both
an OpenAI-compatible endpoint and `log_softmax` return; bits are what a
reader can reason about ("one bit: a coin flip"), so the conversion happens
once, here.
"""

from __future__ import annotations

import math
from collections.abc import Sequence

import numpy as np
import numpy.typing as npt

LN2 = math.log(2)

#: How far a full distribution's probabilities may sum from one before it is
#: refused. Float32 logits over a 150k vocabulary lose a little in
#: `log_softmax`; anything further off is not a distribution, and an entropy
#: computed from it would be a number with no meaning.
NORMALIZATION_TOLERANCE = 1e-3


class EmptySequence(ValueError):
    """Raised for a score over no tokens.

    The mean of nothing is NaN, and NaN passes through JSON, comparisons,
    and thresholds without complaint. A caller that scored an empty
    completion has a bug upstream, and should hear about it there.
    """

    def __init__(self, what: str) -> None:
        super().__init__(f"Cannot compute {what} of an empty sequence; score at least one token.")


def surprisal_bits(logprob: float) -> float:
    """How surprising a token was: its negative log probability, in bits."""
    return -logprob / LN2


def perplexity(logprobs: Sequence[float]) -> float:
    """The exponential of the mean negative log likelihood.

    Read as the number of equally likely tokens the model was choosing
    between, on average, at each step.
    """
    if not logprobs:
        raise EmptySequence("perplexity")
    return math.exp(-math.fsum(logprobs) / len(logprobs))


def entropy_bits(logprobs: npt.ArrayLike) -> npt.NDArray[np.float64]:
    """Shannon entropy of full next-token distributions, in bits.

    Takes one distribution as a 1-D array or one per row of a 2-D array, so
    Lab can pass every position's `log_softmax` at once rather than looping
    over a vocabulary in Python. Each row must be the whole distribution:
    for top-k logprobs, which do not sum to one, use
    `truncated_entropy_bits`.
    """
    rows = np.atleast_2d(np.asarray(logprobs, dtype=np.float64))
    if rows.shape[0] == 0 or rows.shape[1] == 0:
        raise EmptySequence("entropy")
    probabilities = np.exp(rows)
    totals = probabilities.sum(axis=1)
    if not np.allclose(totals, 1.0, atol=NORMALIZATION_TOLERANCE):
        raise ValueError(
            "entropy_bits needs complete distributions whose probabilities sum to 1; "
            f"got sums from {totals.min():.4f} to {totals.max():.4f}. "
            "For top-k logprobs, use truncated_entropy_bits."
        )
    # A probability of zero contributes nothing (the limit of p log p), but
    # exp(-inf) * -inf is NaN, so those terms are never multiplied at all.
    terms = np.multiply(-probabilities, rows, out=np.zeros_like(rows), where=probabilities > 0)
    return terms.sum(axis=1) / LN2


def truncated_entropy_bits(top_logprobs: Sequence[float]) -> float:
    """A lower bound on entropy, from only the top-k logprobs.

    The probability the top k leave over is counted as one more outcome.
    Merging outcomes can only lower entropy, so the true value is at least
    this - which is the honest reading of a truncated distribution, where
    renormalizing the top k would claim a confidence the model never had.
    """
    if not top_logprobs:
        raise EmptySequence("truncated entropy")
    probabilities = np.exp(np.asarray(top_logprobs, dtype=np.float64))
    # Endpoints round, so the top k can sum to a hair over one.
    remainder = max(0.0, 1.0 - float(probabilities.sum()))
    terms = [-p * math.log(p) for p in [*probabilities.tolist(), remainder] if p > 0]
    return math.fsum(terms) / LN2
