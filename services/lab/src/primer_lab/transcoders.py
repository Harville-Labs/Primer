"""Transcoders for the resident model: fetching and checking, never loading.

A transcoder stands in for one layer's MLP with sparse features, and a set
of them - one per layer - is what an attribution graph is traced through.
Lab never opens the weights itself: the graph builder does, in its own
process (see `graphs`). What Lab does is decide which files the builder may
read, so every check that can refuse a set happens here, before a 60 GB
download and long before a graph.

The set is read in the layout circuit-tracer publishes: a `config.yaml`
naming the hooks the transcoders were trained on, and `layer_{i}.safetensors`
for every layer. Only safetensors are read; a set published as pickles is
refused for the same reason a model would be.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import yaml
from primer_contracts.lab import HubModel, TranscoderStatus
from primer_contracts.lab_graphs import TRANSCODER_INPUT_HOOK, TRANSCODER_OUTPUT_HOOK
from pydantic import BaseModel, ConfigDict, ValidationError
from safetensors import SafetensorError, safe_open

from primer_lab.config import Settings
from primer_lab.sources import Hub, check_allowed, check_size, hub_errors
from primer_lab.weights import WeightsRefused

TRANSCODER_CONFIG = "config.yaml"

#: A config is a few lines. Bounded so a repository that names something
#: else `config.yaml` is not read into memory to find that out.
MAX_CONFIG_BYTES = 1024 * 1024

#: The tensors circuit-tracer reads from a layer. The threshold is a
#: JumpReLU's, and the skip connection is optional in the published format.
REQUIRED = frozenset({"W_enc", "W_dec", "b_enc", "b_dec"})
OPTIONAL = frozenset({"activation_function.threshold", "W_skip"})


def layer_file(layer: int) -> str:
    return f"layer_{layer}.safetensors"


class TranscoderConfig(BaseModel):
    """The parts of a transcoder set's `config.yaml` that decide how it is read."""

    model_config = ConfigDict(extra="ignore", frozen=True)

    model_kind: Literal["transcoder_set"]
    feature_input_hook: str
    feature_output_hook: str


@dataclass(frozen=True, slots=True)
class TranscoderSet:
    repository: str
    commit: str
    #: One per layer, in layer order, inside the fetch cache.
    files: list[Path]
    width: int

    def status(self) -> TranscoderStatus:
        return TranscoderStatus(
            repository=self.repository, commit=self.commit, state="ready", width=self.width
        )

    @property
    def scan(self) -> str:
        """How the builder names this set in a graph."""
        return f"{self.repository}@{self.commit}"


def fetch_transcoders(
    settings: Settings, repository: str, hub: Hub, *, hidden_size: int, num_layers: int
) -> TranscoderSet:
    """Check a transcoder repository against the model it is for, then fetch it.

    Held to the operator's repository patterns before anything is asked of
    the hub. Then, in order of cost: the file list, the config, the size,
    and only then the weights, whose headers are read to check every
    layer's shape without loading a tensor.
    """
    check_allowed(settings, HubModel(repository=repository))
    with hub_errors(repository, None):
        listing = hub.describe(repository, None)
        config_size = listing.files.get(TRANSCODER_CONFIG)
        if config_size is None or config_size > MAX_CONFIG_BYTES:
            raise WeightsRefused(f"it has no {TRANSCODER_CONFIG} describing a transcoder set.")
        read_config(hub.download(repository, listing.commit, [TRANSCODER_CONFIG]))
        files = [layer_file(layer) for layer in range(num_layers)]
        missing = [name for name in files if name not in listing.files]
        if missing:
            raise WeightsRefused(
                f"it has no {missing[0]}. A graph needs a transcoder for each of this model's "
                f"{num_layers} layers."
            )
        if layer_file(num_layers) in listing.files:
            raise WeightsRefused(
                f"it has more layers than this model's {num_layers}. It was trained for a "
                "different model."
            )
        check_size(repository, listing, files, limit=settings.lab_max_transcoder_bytes)
        directory = hub.download(repository, listing.commit, files)
        widths = {check_layer(directory / name, hidden_size) for name in files}
        if len(widths) != 1:
            raise WeightsRefused(f"its layers are different widths: {sorted(widths)}.")
    return TranscoderSet(
        repository=repository,
        commit=listing.commit,
        files=[directory / name for name in files],
        width=widths.pop(),
    )


def read_config(directory: Path) -> TranscoderConfig:
    """Refuse a set trained on other hooks than the builder traces through.

    Such a set replaces another part of the layer, and a graph traced
    through it would attribute to the wrong computation without anything
    looking wrong.
    """
    try:
        raw = yaml.safe_load((directory / TRANSCODER_CONFIG).read_bytes())
        config = TranscoderConfig.model_validate(raw)
    except yaml.YAMLError:
        raise WeightsRefused(f"its {TRANSCODER_CONFIG} is not YAML.") from None
    except ValidationError as error:
        problem = error.errors()[0]
        field = ".".join(map(str, problem["loc"])) or TRANSCODER_CONFIG
        raise WeightsRefused(
            "it is not a set of MLP transcoders, which is the only kind Lab builds graphs "
            f"from ({field}: {problem['msg']})."
        ) from None
    hooks = (config.feature_input_hook, config.feature_output_hook)
    if hooks != (TRANSCODER_INPUT_HOOK, TRANSCODER_OUTPUT_HOOK):
        raise WeightsRefused(
            f"its transcoders read {hooks[0]} and write {hooks[1]}. Lab builds graphs through "
            f"transcoders on {TRANSCODER_INPUT_HOOK} and {TRANSCODER_OUTPUT_HOOK} only."
        )
    return config


def check_layer(path: Path, hidden_size: int) -> int:
    """One layer's width, read from its header after checking its shapes."""
    try:
        with safe_open(str(path), framework="pt") as weights:
            shapes = {name: tuple(weights.get_slice(name).get_shape()) for name in weights.keys()}  # noqa: SIM118
    except (SafetensorError, OSError):
        raise WeightsRefused(f"{path.name} is not a safetensors file.") from None
    held = set(shapes)
    if not REQUIRED <= held <= REQUIRED | OPTIONAL:
        raise WeightsRefused(f"{path.name} holds {sorted(held)}, not a transcoder's tensors.")
    width = shapes["b_enc"][0] if len(shapes["b_enc"]) == 1 else 0
    expected = {
        "W_enc": (width, hidden_size),
        "W_dec": (width, hidden_size),
        "b_enc": (width,),
        "b_dec": (hidden_size,),
        # Per feature in the published sets; one shared value is also valid.
        "activation_function.threshold": (width,),
        "W_skip": (hidden_size, hidden_size),
    }
    for name in held:
        if name == "activation_function.threshold" and shapes[name] == ():
            continue
        if width < 1 or shapes[name] != expected[name]:
            raise WeightsRefused(
                f"{path.name}: {name} is {shapes[name]}, and a transcoder for this model's "
                f"{hidden_size}-wide stream would be {expected[name]}."
            )
    return width
