"""Which tokens stand out from the rest of their own sequence.

A token is unusual relative to its neighbours, not to a fixed threshold:
ten bits of surprisal is ordinary in a list of names and remarkable in
boilerplate. So values are scored against the sequence's own median, with a
spread that one outlier cannot drag along with it.
"""

from __future__ import annotations

from collections.abc import Sequence

import numpy as np

from primer_anomaly.measures import EmptySequence
from primer_anomaly.models import Span, SpanKind

#: Scales the median absolute deviation to the standard deviation of a
#: normal distribution, so a robust z-score reads like an ordinary one.
MAD_SCALE = 1.4826

#: The same, for the mean absolute deviation, used when the MAD is zero.
MEAN_AD_SCALE = 1.2533

#: The conventional cut-off for a modified z-score (Iglewicz and Hoaglin).
SPIKE_THRESHOLD = 3.5


def robust_z_scores(values: Sequence[float]) -> list[float]:
    """How many robust deviations each value sits above the median.

    The spread is the median absolute deviation, which a few outliers do
    not move, unlike the standard deviation. When more than half the values
    are identical the MAD is zero - and that is exactly the case of a single
    spike in a flat sequence, the one this has to catch - so the spread
    falls back to the mean absolute deviation. If that is zero too, every
    value equals the median and every score is zero.
    """
    if not values:
        raise EmptySequence("robust z-scores")
    array = np.asarray(values, dtype=np.float64)
    median = np.median(array)
    deviations = np.abs(array - median)
    spread = MAD_SCALE * float(np.median(deviations))
    if spread == 0:
        spread = MEAN_AD_SCALE * float(deviations.mean())
    if spread == 0:
        return [0.0] * len(values)
    return ((array - median) / spread).tolist()


def find_spans(
    values: Sequence[float],
    kind: SpanKind,
    *,
    threshold: float = SPIKE_THRESHOLD,
    min_length: int = 1,
) -> list[Span]:
    """Runs of consecutive tokens whose robust z-score reaches `threshold`.

    Only the high side counts: a token far less surprising than its
    neighbours is not an anomaly anyone is looking for. `min_length` drops
    runs shorter than it, which is how a low-confidence stretch is told
    apart from one hesitant token.
    """
    scores = robust_z_scores(values)
    spans: list[Span] = []
    start: int | None = None
    for index, score in enumerate([*scores, float("-inf")]):
        if score >= threshold:
            if start is None:
                start = index
            continue
        if start is not None and index - start >= min_length:
            run = scores[start:index]
            peak = start + run.index(max(run))
            spans.append(
                Span(kind=kind, start=start, end=index, peak_index=peak, peak_z=scores[peak])
            )
        start = None
    return spans
