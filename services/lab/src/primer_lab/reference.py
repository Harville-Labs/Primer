"""Activation outliers: how far each token's residual is from ordinary text's.

The reference is the residual stream after a layer at every token of a
bundled set of plain, neutral paragraphs. Most of them fit a mean and a
Ledoit-Wolf covariance; the rest are held out and set the threshold, at the
99th percentile of their distances, so the threshold is one that ordinary
text the fit never saw crosses one time in a hundred.

The covariance is shrunk because there are a few thousand reference tokens
and as many dimensions: the sample covariance alone would be singular, and
its inverse - which the distance needs - would not exist.

A reference is built the first time a layer is asked for and kept on the
resident model, so a different model builds its own rather than silently
measuring itself against statistics from a residual stream that is not its.
"""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
from importlib.resources import files
from typing import Any

import numpy as np
import torch
from sklearn.covariance import LedoitWolf

from primer_lab.inspection import read_site, scored_positions, tokenize

TEXTS = tuple(
    paragraph.strip()
    for paragraph in files("primer_lab").joinpath("neutral.txt").read_text().split("\n\n")
    if paragraph.strip()
)
#: Every fifth paragraph is held out of the fit and sets the threshold.
HELD_OUT_EVERY = 5
QUANTILE = 0.99
#: Layers whose reference is kept at once. Each holds a square precision
#: matrix the width of the residual stream: 64 MB at 4,096 in float32.
KEPT = 4


@dataclass(frozen=True, slots=True)
class Reference:
    location: torch.Tensor
    precision: torch.Tensor
    threshold: float
    #: How many reference tokens the statistics were fitted to.
    tokens: int


def distances(location: torch.Tensor, precision: torch.Tensor, rows: torch.Tensor) -> list[float]:
    """The Mahalanobis distance of each row from `location`."""
    centred = rows.float() - location
    squared = ((centred @ precision) * centred).sum(-1)
    # Rounding can leave a row at the mean a hair below zero.
    return squared.clamp(min=0).sqrt().tolist()


def text_residuals(model: Any, text: str, layer: int, special: set[int]) -> torch.Tensor:
    """The residual after `layer` at each position of `text` that scoring would score."""
    ids = tokenize(model.tokenizer, text, chat=False).ids
    read: list[Any] = []
    with torch.no_grad(), model.trace(torch.tensor([ids])):
        read.append(read_site(model, "resid_post", layer)[0])
    return read[0][scored_positions(ids, 1, special)].float().cpu()


def build_reference(model: Any, layer: int, special: set[int]) -> Reference:
    fit: list[torch.Tensor] = []
    held: list[torch.Tensor] = []
    for index, text in enumerate(TEXTS):
        rows = text_residuals(model, text, layer, special)
        (held if index % HELD_OUT_EVERY == HELD_OUT_EVERY - 1 else fit).append(rows)
    rows = torch.cat(fit)
    fitted = LedoitWolf().fit(rows.double().numpy())
    location = torch.from_numpy(fitted.location_).float()
    precision = torch.from_numpy(fitted.get_precision()).float()
    threshold = float(np.quantile(distances(location, precision, torch.cat(held)), QUANTILE))
    return Reference(location, precision, threshold, tokens=len(rows))


class References:
    """The reference at each layer asked for lately, built on first use.

    Only ever used from the run queue's thread, which runs one thing at a
    time, so two requests cannot both build the same layer.
    """

    def __init__(self) -> None:
        self._kept: OrderedDict[int, Reference] = OrderedDict()

    def at(self, model: Any, layer: int, special: set[int]) -> Reference:
        if layer in self._kept:
            self._kept.move_to_end(layer)
            return self._kept[layer]
        built = build_reference(model, layer, special)
        self._kept[layer] = built
        while len(self._kept) > KEPT:
            self._kept.popitem(last=False)
        return built
