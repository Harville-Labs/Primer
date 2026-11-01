"""Read what a prompt does inside the resident model, in one forward pass.

Named for what it does rather than `inspect`, which would shadow the
standard library module of that name for anything importing from here.

Everything is reduced inside the trace. The logit lens alone is a
vocabulary-wide distribution at every layer and position - for Qwen3 about
150 thousand floats per cell - and carrying that out of the pass to take a
top five would hold gigabytes for a response that needs kilobytes.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import torch
from primer_contracts.lab import LENS_TOP, ActivationSite, InspectedToken, LayerReadings

from primer_lab.saes import SaeSet


@dataclass(frozen=True, slots=True)
class Prompt:
    """A prompt as the model will read it."""

    ids: list[int]
    chat_template: bool


@dataclass(frozen=True, slots=True)
class Reading:
    tokens: list[InspectedToken]
    vocabulary: dict[int, str]
    layers: list[LayerReadings]


def tokenize(tokenizer: Any, prompt: str, *, chat: bool) -> Prompt:
    """Turn a prompt into token ids, through the chat template if asked and present.

    The template is rendered to text and then encoded, rather than asking
    for ids directly, because what `apply_chat_template(tokenize=True)`
    returns has changed shape between `transformers` releases. A template
    writes its own special tokens, so the encode adds none of its own.
    """
    if chat and getattr(tokenizer, "chat_template", None):
        text = tokenizer.apply_chat_template(
            [{"role": "user", "content": prompt}], tokenize=False, add_generation_prompt=True
        )
        return Prompt(ids=tokenizer.encode(text, add_special_tokens=False), chat_template=True)
    return Prompt(ids=tokenizer.encode(prompt, add_special_tokens=True), chat_template=False)


def special_ids(tokenizer: Any) -> set[int]:
    # A chat template's markers (`<|im_start|>`) are added tokens flagged
    # special, and not always among the tokenizer's named special ones.
    added = {i for i, token in tokenizer.added_tokens_decoder.items() if token.special}
    return set(tokenizer.all_special_ids) | added


def inspect_prompt(
    model: Any, prompt: Prompt, *, neurons: int, features: int, saes: SaeSet | None
) -> Reading:
    """Residual norm, logit lens, top neurons and SAE features at every layer and position."""
    norms: list[Any] = []
    lens: list[Any] = []
    top: list[tuple[Any, Any]] = []
    residuals: dict[int, Any] = {}
    covered = saes.layers if saes else {}
    # nnsight requires modules to be read in the order they run. Within a
    # layer the MLP's input to its down projection comes before the layer's
    # output, so the neurons are read first.
    with torch.no_grad(), model.trace(torch.tensor([prompt.ids])):
        for layer in range(model.num_layers):
            activations = model.mlps[layer].down_proj.input[0]
            strongest = activations.abs().topk(neurons, dim=-1).indices
            top.append((strongest, activations.gather(-1, strongest)))
            residual = model.layers_output[layer][0]
            norms.append(residual.norm(dim=-1))
            # Encoded after the pass rather than in it: the SAE's matrices
            # are memory-mapped files, and nothing in the trace needs them.
            if layer in covered:
                residuals[layer] = residual
            lens.append(model.project_on_vocab(residual).softmax(dim=-1).topk(LENS_TOP, dim=-1))

    tokenizer = model.tokenizer
    special = special_ids(tokenizer)
    encoded = {
        layer: saes.encode(layer, residual, features)
        for layer, residual in residuals.items()
        if saes is not None
    }
    layers = [
        LayerReadings(
            residual_norm=norm.float().tolist(),
            lens_tokens=read.indices.tolist(),
            lens_probabilities=read.values.float().tolist(),
            neuron_indices=indices.tolist(),
            neuron_activations=values.float().tolist(),
            feature_indices=encoded[layer].indices.tolist() if layer in encoded else None,
            feature_activations=encoded[layer].values.tolist() if layer in encoded else None,
        )
        for layer, (norm, read, (indices, values)) in enumerate(zip(norms, lens, top, strict=True))
    ]
    named = {i for layer in layers for row in layer.lens_tokens for i in row}
    return Reading(
        tokens=describe(tokenizer, prompt.ids, special),
        vocabulary={i: tokenizer.decode([i]) for i in sorted(named)},
        layers=layers,
    )


def describe(tokenizer: Any, ids: list[int], special: set[int]) -> list[InspectedToken]:
    return [InspectedToken(id=i, text=tokenizer.decode([i]), special=i in special) for i in ids]


def read_activation(
    model: Any, prompt: Prompt, *, layer: int, token: int, site: ActivationSite
) -> list[float]:
    """The whole activation vector at one site, layer and position."""
    saved: list[Any] = []
    with torch.no_grad(), model.trace(torch.tensor([prompt.ids])):
        saved.append(read_site(model, site, layer)[0, token])
    return saved[0].float().tolist()


def read_site(model: Any, site: ActivationSite, layer: int) -> Any:
    """The (batch, positions, width) activation at a site, inside a trace.

    The MLP's neurons are read where they enter its down projection: after
    the gate and the activation function, which is what a neuron's value is.
    """
    if site == "attn_out":
        return model.attentions_output[layer]
    if site == "mlp_neuron":
        return model.mlps[layer].down_proj.input
    if site == "mlp_out":
        return model.mlps_output[layer]
    return model.layers_output[layer]


def write_site(model: Any, site: ActivationSite, layer: int, value: Any) -> None:
    """Replace the activation at a site, inside a trace, for what runs after it."""
    if site == "attn_out":
        model.attentions_output[layer] = value
    elif site == "mlp_neuron":
        model.mlps[layer].down_proj.input = value
    elif site == "mlp_out":
        model.mlps_output[layer] = value
    else:
        model.layers_output[layer] = value
