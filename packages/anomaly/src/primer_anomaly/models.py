"""What a scored sequence looks like to whoever displays it.

Full and truncated entropy are separate fields, never one field with a flag
beside it. A reader that ignores the flag - a chart, an export, a script
someone writes next year - would otherwise compare a lower bound over the
top 20 tokens with an exact value over the whole vocabulary and draw a
conclusion from the difference.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, model_validator


class EntropyEstimate(StrEnum):
    """How much of each next-token distribution the score could see."""

    FULL = "full"
    """Every token's probability, from local logits."""

    TRUNCATED = "truncated"
    """Only the top k, from an endpoint. Entropy is a lower bound."""


class SpanKind(StrEnum):
    SURPRISAL_SPIKE = "surprisal_spike"
    LOW_CONFIDENCE = "low_confidence"


class _Frozen(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class TokenScore(_Frozen):
    index: int = Field(ge=0)
    token: str
    logprob: float = Field(le=0)
    surprisal_bits: float = Field(ge=0)
    entropy_bits: float | None = Field(
        default=None, ge=0, description="Exact entropy over the full vocabulary."
    )
    truncated_entropy_bits: float | None = Field(
        default=None,
        ge=0,
        description="A lower bound on entropy, from only the top-k logprobs.",
    )

    @model_validator(mode="after")
    def _exactly_one_entropy(self) -> Self:
        if (self.entropy_bits is None) == (self.truncated_entropy_bits is None):
            raise ValueError("a token carries exactly one of entropy_bits, truncated_entropy_bits")
        return self


class Span(_Frozen):
    """Consecutive tokens that stand out, `start` inclusive, `end` exclusive."""

    kind: SpanKind
    start: int = Field(ge=0)
    end: int = Field(gt=0)
    peak_index: int = Field(ge=0)
    peak_z: float


class SequenceScore(_Frozen):
    estimate: EntropyEstimate
    top_k: int | None = Field(
        default=None, gt=0, description="How many logprobs each truncated entropy saw."
    )
    perplexity: float = Field(ge=1)
    tokens: list[TokenScore]
    spans: list[Span]

    @model_validator(mode="after")
    def _entropy_matches_estimate(self) -> Self:
        truncated = self.estimate is EntropyEstimate.TRUNCATED
        if truncated != (self.top_k is not None):
            raise ValueError("top_k is set exactly when the estimate is truncated")
        field = "truncated_entropy_bits" if truncated else "entropy_bits"
        if any(getattr(token, field) is None for token in self.tokens):
            raise ValueError(f"a {self.estimate} score carries {field} on every token")
        return self
