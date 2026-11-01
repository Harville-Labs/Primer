"""Sparse autoencoders for the resident model: fetching, checking, encoding.

An SAE rewrites a layer's residual stream as a few active features out of
tens of thousands, which is a reading a person can follow where a raw
neuron mostly is not. Lab attaches the SAEs the operator mapped to a model
(`PRIMER_LAB_SAES`) once that model has loaded, so the model is usable
during a download that can run to fifteen gigabytes.

Qwen-Scope, the set this reads, publishes each layer as a `torch.save`d
dict of four tensors. That makes this the one place Lab opens a pickle,
and it does so only through torch's weights-only loader, which rebuilds
tensors and plain containers and refuses every other global rather than
importing it. Converting to safetensors ahead of time would keep pickles
out of the process entirely, but it is a step every operator would have to
run before Lab could use a set it can name. See ADR 0007.

The encode is written out here rather than taken from sae-lens. It is two
lines, and sae-lens would bring thirty-odd packages - TransformerLens,
datasets, wandb, plotly - into an image whose size is already its cost.
"""

from __future__ import annotations

import logging
import pickle
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import torch
from primer_contracts.lab import SaeStatus
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from primer_lab.config import Settings
from primer_lab.sources import Hub, check_size, hub_errors
from primer_lab.weights import WeightsRefused

logger = logging.getLogger(__name__)

SAE_CONFIG = "config.json"


def layer_file(layer: int) -> str:
    return f"layer{layer}.sae.pt"


class SaeConfig(BaseModel):
    """The parts of a Qwen-Scope `config.json` that decide how it is read.

    Narrow on purpose. Another kind of SAE - one with a ReLU, one that
    subtracts the decoder bias first, one hooked elsewhere in the layer -
    encodes differently, and reading it as this one would show features
    that are confidently wrong rather than visibly missing.
    """

    model_config = ConfigDict(extra="ignore", frozen=True)

    model_type: Literal["topk_sae"]
    hook_point: Literal["resid_post"]
    d_model: int = Field(ge=1)
    d_sae: int = Field(ge=1)
    k: int = Field(ge=1)
    layers: list[int] = Field(min_length=1)


@dataclass(frozen=True, slots=True)
class SaeLayer:
    #: (d_sae, d_model), memory-mapped from the file rather than read in.
    encoder: torch.Tensor
    #: (d_sae,)
    bias: torch.Tensor
    #: (d_model, d_sae): column f is what feature f writes into the stream.
    decoder: torch.Tensor


@dataclass(frozen=True, slots=True)
class SaeSet:
    repository: str
    commit: str
    config: SaeConfig
    layers: dict[int, SaeLayer]

    def status(self) -> SaeStatus:
        return SaeStatus(
            repository=self.repository,
            commit=self.commit,
            state="ready",
            width=self.config.d_sae,
            k=self.config.k,
            layers=sorted(self.layers),
        )

    def encode(self, layer: int, residual: torch.Tensor, top: int) -> torch.return_types.topk:
        """The `top` strongest features at each position, for a layer this set covers."""
        return encode(self.layers[layer], residual, min(top, self.config.k))


def feature_activation(sae: SaeLayer, k: int, residual: torch.Tensor, feature: int) -> torch.Tensor:
    """What `feature` reads at each position: its pre-activation where it is
    among the k that fire, and zero where it is not."""
    pre = pre_activations(sae, residual)
    fires = (pre.topk(k, dim=-1).indices == feature).any(dim=-1)
    return torch.where(fires, pre[..., feature], torch.zeros((), device=pre.device))


def encode(sae: SaeLayer, residual: torch.Tensor, top: int) -> torch.return_types.topk:
    """A TopK SAE's strongest features for `residual`, shaped (positions, d_model).

    Qwen-Scope's own encode: an affine map, then the k largest. No ReLU and
    no decoder bias subtracted first, which is what makes it different from
    the SAEs most tutorials describe. Taking fewer than k of the k largest
    is the same as taking the top few of the pre-activations, so the full
    k never needs to be materialised.
    """
    return pre_activations(sae, residual).topk(top, dim=-1)


def pre_activations(sae: SaeLayer, residual: torch.Tensor) -> torch.Tensor:
    """Every feature's pre-activation. On the residual's device, in float32,
    so a bfloat16 model does not round away the smaller features."""
    device = residual.device
    return residual.float() @ sae.encoder.to(device).T + sae.bias.to(device)


class Attachment:
    """The SAEs for one resident model, as they are fetched.

    Held by the resident rather than the host, so a model replaced while
    its SAEs were downloading takes them with it, and they can never be
    applied to the model that came after.
    """

    def __init__(self, repository: str) -> None:
        self.status = SaeStatus(repository=repository, state="fetching")
        self.ready: SaeSet | None = None

    def attached(self, saes: SaeSet) -> None:
        self.ready = saes
        self.status = saes.status()

    def failed(self, message: str) -> None:
        self.status = SaeStatus(repository=self.status.repository, state="failed", error=message)


def fetch_saes(
    settings: Settings, repository: str, hub: Hub, *, hidden_size: int, num_layers: int
) -> SaeSet:
    """Check an SAE repository against the model it is for, then fetch and open it.

    Only `config.json` and the layer files it names are downloaded - never
    the rest of the repository, which for Qwen-Scope includes a demo
    application. Every layer is opened and its shapes checked here, so a
    set that is ready can be encoded with no further checks.
    """
    with hub_errors(repository, None):
        listing = hub.describe(repository, None)
        config = read_config(
            hub.download(repository, listing.commit, [SAE_CONFIG]) / SAE_CONFIG,
            hidden_size=hidden_size,
            num_layers=num_layers,
        )
        files = [layer_file(layer) for layer in config.layers]
        missing = [name for name in files if name not in listing.files]
        if missing:
            raise WeightsRefused(f"its config names layers it does not publish: {missing[0]}.")
        check_size(settings, repository, listing, files)
        directory = hub.download(repository, listing.commit, files)
        layers = {
            layer: load_layer(directory / layer_file(layer), config) for layer in config.layers
        }
    return SaeSet(repository=repository, commit=listing.commit, config=config, layers=layers)


def read_config(path: Path, *, hidden_size: int, num_layers: int) -> SaeConfig:
    try:
        config = SaeConfig.model_validate_json(path.read_bytes())
    except ValidationError as error:
        problem = error.errors()[0]
        field = ".".join(map(str, problem["loc"])) or "config.json"
        raise WeightsRefused(
            f"it is not a TopK SAE on the residual stream, which is the only kind Lab reads "
            f"({field}: {problem['msg']})."
        ) from None
    if config.d_model != hidden_size:
        raise WeightsRefused(
            f"its SAEs read a {config.d_model}-wide residual stream, and this model's is "
            f"{hidden_size}. They were trained for a different model."
        )
    outside = [layer for layer in config.layers if not 0 <= layer < num_layers]
    if outside:
        raise WeightsRefused(f"it names layer {outside[0]}, and this model has {num_layers}.")
    return config


def load_layer(path: Path, config: SaeConfig) -> SaeLayer:
    """Open one layer's SAE, refusing anything but the four tensors it should hold."""
    try:
        state = torch.load(path, map_location="cpu", weights_only=True, mmap=True)
    except (pickle.UnpicklingError, RuntimeError) as error:
        # The loader's message names the global it refused, which is for
        # whoever investigates, not for the user reading the status.
        logger.warning("refused SAE file %s: %s", path.name, error)
        raise WeightsRefused(
            f"{path.name} is not a plain set of tensors, so Lab will not open it."
        ) from None
    expected = {
        "W_enc": (config.d_sae, config.d_model),
        "W_dec": (config.d_model, config.d_sae),
        "b_enc": (config.d_sae,),
        "b_dec": (config.d_model,),
    }
    if not isinstance(state, dict) or set(state) != set(expected):
        held = sorted(map(str, state)) if isinstance(state, dict) else type(state).__name__
        raise WeightsRefused(f"{path.name} holds {held}, not {sorted(expected)}.")
    for name, shape in expected.items():
        tensor = state[name]
        if not isinstance(tensor, torch.Tensor) or not tensor.is_floating_point():
            raise WeightsRefused(f"{path.name}: {name} is not a floating-point tensor.")
        if tuple(tensor.shape) != shape:
            raise WeightsRefused(
                f"{path.name}: {name} is {tuple(tensor.shape)}, and the config says {shape}."
            )
    # The decoder bias is checked and not kept. A feature edit adds the
    # change in one feature times its decoder column; the bias is the same
    # on both sides of that change, so it cancels.
    return SaeLayer(encoder=state["W_enc"], bias=state["b_enc"], decoder=state["W_dec"])
