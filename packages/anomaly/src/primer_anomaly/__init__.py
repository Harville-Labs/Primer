"""How surprised a model was by each token, and where that stands out.

Two callers score text with this: Chat, from the logprobs an operator's
endpoint returns, and Lab, from the full logits of the model it loads. The
maths is the same for both, so it lives here once. What differs is how much
of the distribution each one can see, and every result says which.

Pure functions over numbers. Nothing here reads a file, a model, or the
network.
"""

from primer_anomaly.measures import (
    EmptySequence,
    entropy_bits,
    perplexity,
    surprisal_bits,
    truncated_entropy_bits,
)
from primer_anomaly.models import EntropyEstimate, SequenceScore, Span, SpanKind, TokenScore
from primer_anomaly.scoring import TopLogprobs, score_exact, score_full, score_truncated
from primer_anomaly.spans import find_spans, robust_z_scores

__all__ = [
    "EmptySequence",
    "EntropyEstimate",
    "SequenceScore",
    "Span",
    "SpanKind",
    "TokenScore",
    "TopLogprobs",
    "entropy_bits",
    "find_spans",
    "perplexity",
    "robust_z_scores",
    "score_exact",
    "score_full",
    "score_truncated",
    "surprisal_bits",
    "truncated_entropy_bits",
]
