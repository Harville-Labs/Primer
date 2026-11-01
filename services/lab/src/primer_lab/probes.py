"""Linear probes: telling two sets of prompts apart from the residual stream.

A probe is trained on last-token residuals, the same reading a steering
vector is made from, and is measured before it is trusted: its accuracy is
cross-validated on prompts held out of each fold's training, and set beside
what always answering the larger set would get. A probe on a handful of
prompts can fit anything, so the comparison is the part worth reading.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from uuid import UUID, uuid4

import numpy as np
import torch
from fastapi import APIRouter, Request, status
from primer_contracts.lab import Probe, ProbeRequest, PromptInput
from sklearn.linear_model import LogisticRegression
from sklearn.model_selection import StratifiedKFold, cross_val_score

from primer_lab.host import Resident
from primer_lab.identity import CurrentPrincipal
from primer_lab.routes import current_resident, read_prompt, refused, run_queued
from primer_lab.steering import last_token_residuals

router = APIRouter(prefix="/api/v1/lab", tags=["lab"])

#: At most; fewer when a set is smaller, so every fold holds out one of each.
FOLDS = 5


@dataclass(frozen=True, slots=True)
class Fitted:
    regression: LogisticRegression
    accuracy: float
    folds: int
    chance: float
    beats_chance: bool


def fit_probe(rows: np.ndarray, labels: np.ndarray) -> Fitted:
    """Fit a probe to `rows`, labelled 1 or 0, and measure it on held-out rows.

    The residuals are not standardised, though a few of their dimensions are
    far larger than the rest. With thousands of dimensions and a few dozen
    prompts, scaling each to unit variance gives every dimension of noise the
    same weight as the one that carries the difference, and held-out accuracy
    on cleanly separable rows falls to two in three.
    """
    positive = int(labels.sum())
    folds = min(FOLDS, positive, len(labels) - positive)
    regression = LogisticRegression(max_iter=1000)
    # A fixed shuffle, so the same prompts give the same accuracy twice.
    split = StratifiedKFold(n_splits=folds, shuffle=True, random_state=0)
    accuracy = float(cross_val_score(regression, rows, labels, cv=split).mean())
    regression.fit(rows, labels)
    chance = max(positive, len(labels) - positive) / len(labels)
    # Two standard errors of a coin weighted to `chance` over this many prompts.
    margin = 2 * math.sqrt(chance * (1 - chance) / len(labels))
    return Fitted(regression, accuracy, folds, chance, accuracy > chance + margin)


def probability(fitted: Fitted, rows: np.ndarray) -> list[float]:
    """The probe's probability that each row reads like the positive set."""
    return fitted.regression.predict_proba(rows)[:, 1].tolist()


@router.post(
    "/probes",
    summary="Train a linear probe on two sets of prompts",
    status_code=status.HTTP_201_CREATED,
    description=(
        "Logistic regression on the residual stream after the layer, at each prompt's last "
        "token, telling the positive prompts from the negative ones. The answer says how "
        "accurate it was on prompts held out of training, beside chance. Lab holds it for "
        "the person who trained it until the model changes; apply it when scoring."
    ),
)
async def train_probe(request: Request, body: ProbeRequest, principal: CurrentPrincipal) -> Probe:
    resident = current_resident(request)
    if body.layer >= resident.loaded.num_layers:
        raise refused(
            "No such layer",
            f"This model's layers are 0 to {resident.loaded.num_layers - 1}.",
        )

    def ids(prompts: list[str]) -> list[list[int]]:
        return [
            read_prompt(request, resident, PromptInput(prompt=text, chat=body.chat)).ids
            for text in prompts
        ]

    positive, negative = ids(body.positive), ids(body.negative)
    model = resident.loaded.model

    def work() -> Fitted:
        rows = torch.cat(
            [
                last_token_residuals(model, positive, body.layer),
                last_token_residuals(model, negative, body.layer),
            ]
        )
        labels = np.array([1] * len(positive) + [0] * len(negative))
        return fit_probe(rows.double().numpy(), labels)

    fitted = await run_queued(request, resident, work)
    summary = Probe(
        id=uuid4().hex,
        label=body.label,
        layer=body.layer,
        positive=len(positive),
        negative=len(negative),
        accuracy=fitted.accuracy,
        folds=fitted.folds,
        chance=fitted.chance,
        beats_chance=fitted.beats_chance,
    )
    return resident.probes.add(principal.user_id, summary, fitted)


@router.get("/probes", summary="The probes you have trained on this model")
async def list_probes(request: Request, principal: CurrentPrincipal) -> list[Probe]:
    return current_resident(request).probes.listed(principal.user_id)


def owned_probe(resident: Resident, owner: UUID, probe_id: str) -> tuple[Probe, Fitted]:
    """The caller's probe, or a refusal that does not say whether anyone else has one."""
    fitted = resident.probes.owned_by(owner).get(probe_id)
    summary = next((p for p in resident.probes.listed(owner) if p.id == probe_id), None)
    if fitted is None or summary is None:
        raise refused(
            "No such probe",
            "You have no probe with that id on this model. Probes are kept until the model "
            "changes; train it again.",
        )
    return summary, fitted
