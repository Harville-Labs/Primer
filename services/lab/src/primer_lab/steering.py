"""Steering vectors made from two sets of prompts.

A vector is a direction in one model's residual stream, meaningless in any
other's, so the resident model holds them and they go when it does.
"""

from __future__ import annotations

from typing import Any
from uuid import uuid4

import torch
from primer_contracts.lab import SteeringVector

from primer_lab.inspection import read_site


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


def made_vector(
    vector: torch.Tensor, *, label: str, layer: int, positive: int, negative: int
) -> SteeringVector:
    """The summary a user sees of a vector, under a fresh id."""
    return SteeringVector(
        id=uuid4().hex,
        label=label,
        layer=layer,
        norm=float(vector.norm()),
        positive=positive,
        negative=negative,
    )
