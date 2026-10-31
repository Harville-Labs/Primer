import math

import numpy as np
import pytest
from primer_anomaly import (
    EmptySequence,
    entropy_bits,
    perplexity,
    surprisal_bits,
    truncated_entropy_bits,
)


def test_a_uniform_distribution_has_log2_of_its_size_in_bits() -> None:
    """The maximum entropy over n outcomes; anything else here is a units bug."""
    rows = np.full((1, 8), math.log(1 / 8))
    assert entropy_bits(rows).tolist() == pytest.approx([3.0])


def test_a_one_hot_distribution_has_zero_entropy_rather_than_nan() -> None:
    """exp(-inf) * -inf is NaN, and a certain prediction must still read 0."""
    row = [0.0, -math.inf, -math.inf, -math.inf]
    result = entropy_bits(row)
    assert result.tolist() == [0.0]


def test_entropy_scores_each_row_of_a_batch() -> None:
    rows = [[math.log(0.5)] * 2, [0.0, -math.inf]]
    assert entropy_bits(rows).tolist() == pytest.approx([1.0, 0.0])


def test_entropy_refuses_a_top_k_distribution() -> None:
    """Top-k logprobs do not sum to one; scoring them as full overstates certainty."""
    with pytest.raises(ValueError, match="truncated_entropy_bits"):
        entropy_bits([math.log(0.5), math.log(0.2)])


def test_truncated_entropy_counts_the_missing_mass_as_one_more_outcome() -> None:
    """Top two of 0.5 and 0.25 leave 0.25 over: the same as three outcomes."""
    expected = -(0.5 * math.log2(0.5) + 0.25 * math.log2(0.25) + 0.25 * math.log2(0.25))
    assert truncated_entropy_bits([math.log(0.5), math.log(0.25)]) == pytest.approx(expected)


def test_truncated_entropy_never_exceeds_the_full_entropy_it_bounds() -> None:
    full = np.log(np.array([0.4, 0.2, 0.1, 0.1, 0.1, 0.1]))
    exact = float(entropy_bits(full)[0])
    assert truncated_entropy_bits(full[:2].tolist()) <= exact
    assert truncated_entropy_bits(full.tolist()) == pytest.approx(exact)


def test_truncated_entropy_tolerates_an_endpoint_that_rounds_past_one() -> None:
    assert truncated_entropy_bits([math.log(0.6), math.log(0.41)]) > 0


def test_surprisal_of_a_coin_flip_is_one_bit() -> None:
    assert surprisal_bits(math.log(0.5)) == pytest.approx(1.0)


def test_perplexity_of_uniform_choices_is_the_number_of_choices() -> None:
    assert perplexity([math.log(1 / 4)] * 5) == pytest.approx(4.0)


@pytest.mark.parametrize(
    "measure",
    [perplexity, truncated_entropy_bits, entropy_bits, lambda _: entropy_bits(np.empty((1, 0)))],
)
def test_an_empty_sequence_is_an_error_rather_than_nan(measure) -> None:
    """NaN survives JSON and comparisons silently; an empty input is a bug upstream."""
    with pytest.raises(EmptySequence):
        measure([])
