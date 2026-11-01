"""Editing the forward pass, and generating with the edits and without.

A comparison is two greedy generations from the same prompt: the model as
it is, then the model with the edits applied at every step. Greedy, so the
only thing that can make the two differ is the edit. The baseline goes
through the same code with no edits rather than a plain `generate`, so an
empty list of edits is the same run twice by construction.

Decoding keeps a KV cache. An edit at a prompt position changes that
position's keys and values, and the cache carries the edited ones forward,
which is what re-running the whole sequence with the edit at each step
would compute, at a fraction of the cost.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from itertools import groupby
from typing import Any, Literal

import torch
from primer_contracts.lab import (
    ActivationSite,
    Intervention,
    InterventionOp,
    InterventionPositions,
    InterventionSite,
)
from transformers import DynamicCache

from primer_lab.inspection import read_site, write_site
from primer_lab.saes import SaeLayer, SaeSet, feature_activation

#: The order a layer runs its sites in. nnsight requires a trace to touch
#: modules in the order they run, so edits are applied in this order
#: whatever order they were asked for in. A feature is read from the
#: residual stream, so it shares that site's slot.
_ORDER: dict[InterventionSite, int] = {
    "attn_out": 0,
    "mlp_neuron": 1,
    "mlp_out": 2,
    "resid_post": 3,
    "sae_feature": 3,
}
_UNIT = {"mlp_neuron": "neuron", "sae_feature": "feature"}


class InterventionRefused(ValueError):
    """An edit that names something this model or its SAEs do not have."""


@dataclass(frozen=True, slots=True)
class Edit:
    """An intervention resolved against the resident model."""

    site: InterventionSite
    layer: int
    index: int | None
    op: InterventionOp
    value: float
    positions: InterventionPositions
    vector: torch.Tensor | None = None
    sae: SaeLayer | None = None
    #: How many features the SAE fires, which decides whether this one does.
    k: int = 0

    @property
    def reads(self) -> ActivationSite:
        return "resid_post" if self.site == "sae_feature" else self.site


def resolve(
    interventions: list[Intervention],
    *,
    num_layers: int,
    hidden_size: int,
    mlp_width: int,
    saes: SaeSet | None,
    vectors: Mapping[str, torch.Tensor],
) -> list[Edit]:
    """Check each intervention against the model, naming the valid range when it is out of it.

    Checked here rather than left to fail inside the pass, where an index
    past the end is an IndexError from deep in torch - or on a GPU, an
    asynchronous device assertion that poisons the process for every user.
    """
    edits = []
    for number, item in enumerate(interventions, start=1):
        where = f"Edit {number}"
        if item.layer >= num_layers:
            raise InterventionRefused(
                f"{where}: there is no layer {item.layer}. This model's layers are "
                f"0 to {num_layers - 1}."
            )
        sae, k = None, 0
        if item.site == "sae_feature":
            if saes is None:
                raise InterventionRefused(
                    f"{where}: this model has no SAEs ready, so it has no features to edit."
                )
            if item.layer not in saes.layers:
                covered = ", ".join(map(str, sorted(saes.layers)))
                raise InterventionRefused(
                    f"{where}: there is no SAE at layer {item.layer}. This model's SAEs are at "
                    f"layers {covered}."
                )
            sae, k, width = saes.layers[item.layer], saes.config.k, saes.config.d_sae
        else:
            width = mlp_width if item.site == "mlp_neuron" else hidden_size
        if item.index is not None and item.index >= width:
            unit = _UNIT.get(item.site, "component")
            raise InterventionRefused(
                f"{where}: there is no {unit} {item.index} at {item.site}. They are 0 to "
                f"{width - 1}."
            )
        vector = None
        if item.vector_id is not None:
            vector = vectors.get(item.vector_id)
            if vector is None:
                raise InterventionRefused(
                    f"{where}: there is no steering vector {item.vector_id}. Vectors last until "
                    "the model changes; make it again."
                )
        edits.append(
            Edit(
                site=item.site,
                layer=item.layer,
                index=item.index,
                op=item.op,
                value=item.value,
                positions=item.positions,
                vector=vector,
                sae=sae,
                k=k,
            )
        )
    return edits


def selected(
    positions: InterventionPositions,
    *,
    position: int,
    token: int,
    prompt_length: int,
    special: set[int],
) -> bool:
    """Whether an edit with these positions applies to the token at `position`."""
    match positions:
        case "all":
            return token not in special
        case "prompt":
            return position < prompt_length and token not in special
        case "generated":
            return position >= prompt_length
        case (start, end):
            return start <= position < end


@dataclass(frozen=True, slots=True)
class _Context:
    """What every step of one generation shares."""

    groups: list[tuple[tuple[int, ActivationSite], list[tuple[int, Edit]]]]
    #: Each `mean` edit's value, by its place in the list.
    means: dict[int, torch.Tensor]
    prompt_length: int
    special: set[int]


def _grouped(edits: list[Edit]) -> list[tuple[tuple[int, ActivationSite], list[tuple[int, Edit]]]]:
    """Edits by the site they read, in the order the model runs those sites.

    The sort is stable, so edits at one site keep the order they were given.
    """
    ordered = sorted(enumerate(edits), key=lambda item: (item[1].layer, _ORDER[item[1].site]))
    return [
        (key, list(group))
        for key, group in groupby(ordered, key=lambda item: (item[1].layer, item[1].reads))
    ]


def _component(edit: Edit, rows: torch.Tensor) -> torch.Tensor:
    """The part of `rows`, shaped (positions, width), that `edit` changes."""
    if edit.sae is not None and edit.index is not None:
        return feature_activation(edit.sae, edit.k, rows, edit.index)
    return rows if edit.index is None else rows[:, edit.index]


def _changed(edit: Edit, current: torch.Tensor, mean: torch.Tensor | None) -> torch.Tensor:
    match edit.op:
        case "zero":
            return torch.zeros_like(current)
        case "mean":
            if mean is None:
                raise ValueError("A mean edit needs its value from prompt_means.")
            return mean.to(current).expand_as(current)
        case "scale":
            return current * edit.value
        case "clamp":
            return torch.full_like(current, edit.value)
        case "add":
            if edit.vector is not None:
                return current + edit.value * edit.vector.to(current)
            return current + edit.value


def apply(edit: Edit, rows: torch.Tensor, mean: torch.Tensor | None) -> torch.Tensor:
    """`rows`, shaped (positions, width), with `edit` made to them.

    A feature is edited through its SAE: the feature's activation is read,
    changed, and the change times its decoder column added to the stream.
    Everything the SAE does not reconstruct, its error term, is left as it
    was - replacing the stream with the SAE's reconstruction would edit
    every token, not only the feature.
    """
    current = _component(edit, rows)
    new = _changed(edit, current, mean)
    if edit.sae is not None and edit.index is not None:
        direction = edit.sae.decoder[:, edit.index].to(rows.device)
        return rows + ((new - current)[:, None] * direction).to(rows.dtype)
    if edit.index is None:
        return new.to(rows.dtype)
    edited = rows.clone()
    edited[:, edit.index] = new
    return edited


def prompt_means(
    model: Any, prompt: list[int], edits: list[Edit], special: set[int]
) -> dict[int, torch.Tensor]:
    """Each `mean` edit's value: what it changes, averaged over the prompt's text tokens.

    Read from a pass with no edits, so a mean is the model's own and not
    one that earlier edits have already moved.
    """
    wanted = [edit if edit.op == "mean" else None for edit in edits]
    if not any(wanted):
        return {}
    text = torch.tensor([token not in special for token in prompt])
    # A prompt that is all template is averaged over what there is.
    keep = text if bool(text.any()) else torch.ones(len(prompt), dtype=torch.bool)
    means: dict[int, torch.Tensor] = {}
    groups = _grouped([edit for edit in edits if edit.op == "mean"])
    numbered = [n for n, edit in enumerate(edits) if edit.op == "mean"]
    with torch.no_grad(), model.trace(torch.tensor([prompt])):
        for (layer, site), group in groups:
            rows = read_site(model, site, layer)[0][keep]
            for n, edit in group:
                means[numbered[n]] = _component(edit, rows).float().mean(dim=0)
    return means


def _step(model: Any, chunk: list[int], offset: int, cache: Any, context: _Context) -> Any:
    """One forward pass over `chunk`, at absolute positions from `offset`, edited."""
    masks = {
        n: torch.tensor(
            [
                selected(
                    edit.positions,
                    position=offset + i,
                    token=token,
                    prompt_length=context.prompt_length,
                    special=context.special,
                )
                for i, token in enumerate(chunk)
            ]
        )
        for _, group in context.groups
        for n, edit in group
    }
    active = [
        (key, [(n, edit) for n, edit in group if bool(masks[n].any())])
        for key, group in context.groups
    ]
    active = [(key, group) for key, group in active if group]
    logits: list[Any] = []
    with torch.no_grad(), model.trace(torch.tensor([chunk]), past_key_values=cache, use_cache=True):
        for (layer, site), group in active:
            value = read_site(model, site, layer).clone()
            for n, edit in group:
                value[0, masks[n]] = apply(edit, value[0, masks[n]], context.means.get(n))
            write_site(model, site, layer, value)
        logits.append(model.output.logits[0, -1])
    return logits[0]


def stop_tokens(model: Any) -> set[int]:
    configured = model.config.eos_token_id
    ids = set(configured if isinstance(configured, list) else [configured])
    return {i for i in ids | {model.tokenizer.eos_token_id} if i is not None}


@dataclass(frozen=True, slots=True)
class Generation:
    tokens: list[int]
    #: The logits for the first answer token, read before anything was written.
    first_logits: torch.Tensor


def generate(
    model: Any,
    prompt: list[int],
    edits: list[Edit],
    *,
    max_new_tokens: int,
    special: set[int],
    cancelled: Callable[[], bool],
    on_token: Callable[[int, int], None],
) -> Generation:
    """Greedy decoding with `edits` applied at every step.

    Stops at an end-of-sequence token, after `max_new_tokens`, or when
    `cancelled` says nobody is waiting any more - checked between steps,
    because a pass in progress cannot be interrupted, and a client that
    has gone should not hold the queue for the rest of a long answer.
    """
    context = _Context(
        groups=_grouped(edits),
        means=prompt_means(model, prompt, edits, special),
        prompt_length=len(prompt),
        special=special,
    )
    stops = stop_tokens(model)
    cache = DynamicCache()
    chunk, offset = list(prompt), 0
    tokens: list[int] = []
    first: torch.Tensor | None = None
    for index in range(max_new_tokens):
        if index and cancelled():
            break
        logits = _step(model, chunk, offset, cache, context)
        if first is None:
            first = logits.float().cpu()
        token = int(logits.argmax())
        tokens.append(token)
        on_token(index, token)
        if token in stops:
            break
        offset += len(chunk)
        chunk = [token]
    if first is None:
        raise ValueError("max_new_tokens must be at least 1.")
    return Generation(tokens=tokens, first_logits=first)


Run = Literal["baseline", "edited"]


@dataclass(frozen=True, slots=True)
class Comparison:
    baseline: Generation
    edited: Generation


def compare(
    model: Any,
    prompt: list[int],
    edits: list[Edit],
    *,
    max_new_tokens: int,
    special: set[int],
    cancelled: Callable[[], bool],
    on_token: Callable[[Run, int, int], None],
) -> Comparison:
    def run(name: Run, applied: list[Edit]) -> Generation:
        return generate(
            model,
            prompt,
            applied,
            max_new_tokens=max_new_tokens,
            special=special,
            cancelled=cancelled,
            on_token=lambda index, token: on_token(name, index, token),
        )

    return Comparison(baseline=run("baseline", []), edited=run("edited", edits))


def first_divergence(baseline: list[int], edited: list[int]) -> int | None:
    for index, (before, after) in enumerate(zip(baseline, edited, strict=False)):
        if before != after:
            return index
    return None if len(baseline) == len(edited) else min(len(baseline), len(edited))
