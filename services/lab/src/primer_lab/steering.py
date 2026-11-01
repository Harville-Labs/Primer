"""Steering vectors made from two sets of prompts, and held for their owner.

A vector is a direction in one model's residual stream, meaningless in any
other's, so the store belongs to the resident model and goes when it does.
They are held in memory rather than saved: a vector is a few kilobytes
that took a few seconds to make, and a saved one would have to record which
model and commit it was made on to be safe to use again. Saving them comes
with saved experiments (#174).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from uuid import UUID, uuid4

import torch
from primer_contracts.lab import SteeringVector

from primer_lab.inspection import read_site

#: Per owner. The oldest goes when a new one would pass this, so a
#: session that makes vectors in a loop cannot grow the process without end.
MAX_VECTORS_PER_OWNER = 32


def difference_of_means(positive: torch.Tensor, negative: torch.Tensor) -> torch.Tensor:
    """The mean of the positive rows minus the mean of the negative ones."""
    return positive.float().mean(dim=0) - negative.float().mean(dim=0)


def last_token_residuals(model: Any, prompts: list[list[int]], layer: int) -> torch.Tensor:
    """The residual stream after `layer` at each prompt's last token, one row each.

    The last token because it is where the model has read the whole prompt
    and is about to answer it, which is where contrastive activation
    addition and the refusal-direction work read their means. One pass per
    prompt rather than a padded batch, so no prompt's reading depends on how
    long the others were.
    """
    rows: list[Any] = []
    for ids in prompts:
        with torch.no_grad(), model.trace(torch.tensor([ids])):
            rows.append(read_site(model, "resid_post", layer)[0, -1])
    return torch.stack([row.float().cpu() for row in rows])


@dataclass(frozen=True, slots=True)
class _Held:
    owner: UUID
    summary: SteeringVector
    vector: torch.Tensor


class SteeringVectors:
    """Vectors by id, each visible only to the person who made it.

    The prompts a vector was made from may be private, and the vector is
    computed from nothing else, so it is treated as theirs.
    """

    def __init__(self) -> None:
        self._held: dict[str, _Held] = {}

    def add(
        self,
        owner: UUID,
        vector: torch.Tensor,
        *,
        label: str,
        layer: int,
        positive: int,
        negative: int,
    ) -> SteeringVector:
        mine = [key for key, held in self._held.items() if held.owner == owner]
        for key in mine[: max(0, len(mine) - MAX_VECTORS_PER_OWNER + 1)]:
            del self._held[key]
        summary = SteeringVector(
            id=uuid4().hex,
            label=label,
            layer=layer,
            norm=float(vector.norm()),
            positive=positive,
            negative=negative,
        )
        self._held[summary.id] = _Held(owner=owner, summary=summary, vector=vector)
        return summary

    def listed(self, owner: UUID) -> list[SteeringVector]:
        return [held.summary for held in self._held.values() if held.owner == owner]

    def owned_by(self, owner: UUID) -> dict[str, torch.Tensor]:
        return {key: held.vector for key, held in self._held.items() if held.owner == owner}
