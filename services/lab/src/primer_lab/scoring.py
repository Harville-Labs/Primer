"""Scoring the resident model's surprise at each token, for the Anomalies tab.

Chat scores an operator's endpoint from the top-k logprobs it returns. Here
the whole vocabulary is in hand, so entropy is exact rather than a lower
bound; the arithmetic is `primer_anomaly`'s either way.

Two detectors read the residual stream at the scored tokens in the same
forward pass: how far each is from ordinary text's (`primer_lab.reference`)
and what one of the caller's probes makes of it (`primer_lab.probes`).
"""

from __future__ import annotations

from collections.abc import Collection
from dataclasses import dataclass
from typing import Any, Literal

import torch
from fastapi import APIRouter, Request
from primer_anomaly import SequenceScore, entropy_bits, score_exact
from primer_contracts.lab import LabModelStatus, Probe, PromptInput
from pydantic import BaseModel, ConfigDict, Field

from primer_lab.config import Settings
from primer_lab.host import Resident
from primer_lab.identity import CurrentPrincipal
from primer_lab.inspection import read_site, scored_positions, special_ids
from primer_lab.interventions import generate
from primer_lab.probes import Fitted, owned_probe, probability
from primer_lab.reference import distances
from primer_lab.routes import (
    check_layer,
    check_new_tokens,
    current_resident,
    read_prompt,
    refused,
    run_queued,
)

router = APIRouter(prefix="/api/v1/lab", tags=["lab"])

#: Positions measured at once. Each is a float64 row over the vocabulary -
#: about a megabyte at 150 thousand tokens - so this bounds what scoring
#: holds beyond the logits the forward pass produced anyway.
BLOCK = 64

#: `generate` scores what the model writes in answer to `prompt`; `text`
#: scores `prompt` itself. The same words as Chat's, so the tab sends one
#: request shape whichever source it is scoring.
ScoreMode = Literal["generate", "text"]


class LabScoreRequest(PromptInput):
    mode: ScoreMode
    #: How long an answer to generate. Ignored when scoring the text itself.
    max_new_tokens: int = Field(default=64, ge=1, le=2048)
    #: Measure each token's distance from ordinary text at this layer.
    outlier_layer: int | None = Field(default=None, ge=0)
    #: Apply this probe of the caller's to each token.
    probe_id: str | None = Field(default=None, max_length=64)


class Outliers(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    layer: int
    #: Per scored token, in the order of `score.tokens`: the Mahalanobis
    #: distance of its residual from the reference text's.
    distances: list[float]
    #: The 99th percentile of the distances of reference text held out of
    #: the fit. Ordinary text crosses it about one token in a hundred.
    threshold: float
    #: How many tokens of reference text the statistics were fitted to.
    reference_tokens: int


class ProbeReading(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    probe: Probe
    #: Per scored token, the probe's probability that it reads like the
    #: positive set. The probe was trained on last tokens only, so at any
    #: other position this is indicative rather than measured.
    probabilities: list[float]


class LabScored(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    model: LabModelStatus
    mode: ScoreMode
    #: The answer that was generated, or the text as supplied.
    text: str
    #: Always exact: `estimate` is `full`, and every token has `entropy_bits`.
    score: SequenceScore
    outliers: Outliers | None = None
    probe: ProbeReading | None = None


@dataclass(frozen=True, slots=True)
class Scored:
    score: SequenceScore
    #: The residual after each layer asked for, one row per scored token.
    residuals: dict[int, torch.Tensor]


def score_from(
    model: Any, ids: list[int], start: int, special: set[int], layers: Collection[int] = ()
) -> Scored | None:
    """Score `ids[start:]` as the model predicted each from what came before it.

    None when nothing is left to score.
    """
    targets = scored_positions(ids, start, special)
    if not targets:
        return None
    # Ascending, and before the logits: a trace reads modules in the order
    # the forward pass reaches them.
    read: list[Any] = []
    with torch.no_grad(), model.trace(torch.tensor([ids])):
        for layer in sorted(layers):
            read.append(read_site(model, "resid_post", layer)[0])
        read.append(model.output.logits[0])
    *residuals, logits = read
    logprobs: list[float] = []
    entropies: list[float] = []
    for block in (targets[i : i + BLOCK] for i in range(0, len(targets), BLOCK)):
        # The logits at position i - 1 are the prediction of token i.
        rows = logits[[i - 1 for i in block]].double().log_softmax(dim=-1)
        came = torch.tensor([ids[i] for i in block]).unsqueeze(1)
        logprobs += rows.gather(1, came).squeeze(1).tolist()
        entropies += entropy_bits(rows.cpu().numpy()).tolist()
    tokens = [model.tokenizer.decode([ids[i]]) for i in targets]
    return Scored(
        score_exact(tokens, logprobs, entropies),
        {
            layer: rows[targets].float().cpu()
            for layer, rows in zip(sorted(layers), residuals, strict=True)
        },
    )


type Measured = tuple[Scored, Outliers | None, ProbeReading | None]


@dataclass(frozen=True, slots=True)
class Detectors:
    """What the caller asked to have read beside the score, already checked."""

    outlier_layer: int | None
    probe: tuple[Probe, Fitted] | None

    @property
    def layers(self) -> set[int]:
        wanted = {self.outlier_layer, self.probe[0].layer if self.probe else None}
        return {layer for layer in wanted if layer is not None}

    def read(
        self, resident: Resident, scored: Scored, special: set[int]
    ) -> tuple[Outliers | None, ProbeReading | None]:
        """On the run queue's thread: a reference may have to be built first."""
        outliers = None
        if self.outlier_layer is not None:
            layer = self.outlier_layer
            reference = resident.references.at(resident.loaded.model, layer, special)
            outliers = Outliers(
                layer=layer,
                distances=distances(
                    reference.location, reference.precision, scored.residuals[layer]
                ),
                threshold=reference.threshold,
                reference_tokens=reference.tokens,
            )
        reading = None
        if self.probe is not None:
            summary, fitted = self.probe
            rows = scored.residuals[summary.layer].double().numpy()
            reading = ProbeReading(probe=summary, probabilities=probability(fitted, rows))
        return outliers, reading


@router.post(
    "/score",
    summary="Score the resident model's surprise at each token",
    description=(
        "Either generates a greedy answer to the prompt and scores it, or scores the prompt "
        "itself as plain text, without the chat template. Entropy is over the whole "
        "vocabulary. The first token of a text is not scored: nothing came before it. "
        "Optionally, each scored token's distance from ordinary text at a layer, and what "
        "one of your probes makes of it."
    ),
)
async def score(request: Request, body: LabScoreRequest, principal: CurrentPrincipal) -> LabScored:
    settings: Settings = request.app.state.settings
    resident = current_resident(request)
    model = resident.loaded.model
    special = special_ids(model.tokenizer)
    if body.outlier_layer is not None:
        check_layer(resident, body.outlier_layer)
    detectors = Detectors(
        outlier_layer=body.outlier_layer,
        probe=owned_probe(resident, principal.user_id, body.probe_id) if body.probe_id else None,
    )

    def measured(ids: list[int], start: int) -> Measured | None:
        scored = score_from(model, ids, start, special, detectors.layers)
        return None if scored is None else (scored, *detectors.read(resident, scored, special))

    if body.mode == "text":
        # Scored as written. Through the template, the template's own text
        # would be scored as though the user had typed it.
        prompt = read_prompt(request, resident, body.model_copy(update={"chat": False}))
        if not any(i not in special for i in prompt.ids[1:]):
            raise refused(
                "Nothing to score",
                "The text is one token, and the first token of a text has nothing before it "
                "to be predicted from. Score a longer text.",
            )

        def work() -> Measured | None:
            return measured(prompt.ids, 1)

        measurement = await run_queued(request, resident, work)
        text = body.prompt
    else:
        check_new_tokens(settings, body.max_new_tokens)
        prompt = read_prompt(request, resident, body)
        answer: list[int] = []

        def work() -> Measured | None:
            answer.extend(
                generate(
                    model,
                    prompt.ids,
                    [],
                    max_new_tokens=body.max_new_tokens,
                    special=special,
                    # Not streamed, so there is no client to notice leaving;
                    # the run timeout is what bounds it.
                    cancelled=lambda: False,
                    on_token=lambda index, token: None,
                ).tokens
            )
            return measured(prompt.ids + answer, len(prompt.ids))

        measurement = await run_queued(request, resident, work)
        text = model.tokenizer.decode(answer, skip_special_tokens=True)
    if measurement is None:
        raise refused(
            "Nothing to score",
            "The model ended its answer before writing anything. Try another prompt.",
        )
    scored, outliers, probe = measurement
    return LabScored(
        model=resident.status(),
        mode=body.mode,
        text=text,
        score=scored.score,
        outliers=outliers,
        probe=probe,
    )
