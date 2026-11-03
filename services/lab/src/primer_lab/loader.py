"""Load a model, and only in the one way Lab allows.

nnterp's `StandardizedTransformer` wraps nnsight around a `transformers`
model and gives every architecture the same names - `layers_output[i]`,
`mlps_output[i]` - so the inspection code is written once rather than per
model family. That matters more because users choose the model.

A multimodal checkpoint is loaded whole, as the image-text model it is
published as. nnsight refuses one through `AutoModelForCausalLM`, so Lab
names `AutoModelForImageTextToText` for it instead, and reads its language
model's sizes from the text config. See ADR 0013.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import torch
from nnterp import StandardizedTransformer
from nnterp.rename_utils import RenameConfig, RenamingError
from transformers import AutoConfig, AutoModelForImageTextToText, PretrainedConfig
from transformers.models.auto.modeling_auto import MODEL_FOR_IMAGE_TEXT_TO_TEXT_MAPPING_NAMES

from primer_lab.config import Settings
from primer_lab.sources import ModelUnavailable, Resolved

logger = logging.getLogger(__name__)

DTYPES = {"float32": torch.float32, "bfloat16": torch.bfloat16, "float16": torch.float16}

#: The `layer_types` entry `transformers` gives a layer that mixes tokens
#: with a recurrent linear attention instead of softmax attention, as
#: three in four of Qwen3.5's do. Such a layer has no `self_attn`.
LINEAR_ATTENTION = "linear_attention"


class DeviceUnavailable(RuntimeError):
    """The configured device is not one this process can use."""


@dataclass(frozen=True, slots=True)
class LoadPlan:
    """Everything decided before weights are read, so it fails fast."""

    directory: Path
    architecture: str
    commit: str | None
    device: Literal["cpu", "cuda"]
    dtype: str


@dataclass(frozen=True, slots=True)
class LoadedModel:
    plan: LoadPlan
    model: Any
    #: The language model's config: the model's own for a text model, its
    #: text part for a multimodal one, whose top-level config has no
    #: `hidden_size` or `intermediate_size`.
    text_config: PretrainedConfig
    num_layers: int
    #: The layers with a softmax attention whose output can be read: all of
    #: them, except in a hybrid model.
    attention_layers: frozenset[int]

    def without_attention(self, layer: int) -> str | None:
        """Why `layer` has no attention output to read or edit, or None if it has one."""
        if layer in self.attention_layers:
            return None
        listed = ", ".join(map(str, sorted(self.attention_layers))) or "none"
        return (
            f"Layer {layer} mixes tokens with linear attention, so it has no attention output. "
            f"This model's attention layers are {listed}."
        )


#: How a model is constructed from a path and options. Injected so a test
#: can see exactly what Lab asked for without loading anything.
ModelFactory = Callable[..., Any]


def resolve_device(
    settings: Settings, cuda_available: Callable[[], bool] | None = None
) -> Literal["cpu", "cuda"]:
    """The device to load onto, refusing one this process cannot use.

    Run at startup as well as on every load, so a deployment configured for
    a GPU it was not given fails where its operator is looking.
    """
    has_cuda = (cuda_available or torch.cuda.is_available)()
    if settings.lab_device == "cuda" and not has_cuda:
        raise DeviceUnavailable(
            "PRIMER_LAB_DEVICE=cuda, but torch can see no CUDA device. Run the CUDA build of "
            "the Lab image on a node with a GPU, or set PRIMER_LAB_DEVICE=cpu (or auto)."
        )
    if settings.lab_device == "auto":
        return "cuda" if has_cuda else "cpu"
    return settings.lab_device


def plan_load(
    settings: Settings, resolved: Resolved, *, cuda_available: Callable[[], bool] | None = None
) -> LoadPlan:
    """Resolve device and dtype for checked weights, without loading them."""
    device = resolve_device(settings, cuda_available)
    dtype = settings.lab_dtype
    if dtype == "auto":
        # bfloat16 halves memory on a GPU; on CPU most kernels are slower in
        # it than in float32, so the saving costs more than it is worth.
        dtype = "bfloat16" if device == "cuda" else "float32"
    return LoadPlan(
        directory=resolved.directory,
        architecture=resolved.architecture,
        commit=resolved.commit,
        device=device,
        dtype=dtype,
    )


def load_options(plan: LoadPlan) -> dict[str, Any]:
    """The keyword arguments every load passes, and the only place they are set.

    `trust_remote_code=False` is already nnterp's default and is stated
    anyway: it is the one argument whose value is the security boundary,
    and a default can change under an upgrade. `use_safetensors` makes
    `transformers` itself refuse a pickle checkpoint, behind the directory
    check that already did. `local_files_only` means a missing file is an
    error rather than a download, whatever `HF_HUB_OFFLINE` says.
    """
    return {
        "trust_remote_code": False,
        "use_safetensors": True,
        "local_files_only": True,
        "device_map": plan.device,
        "dtype": DTYPES[plan.dtype],
    }


def checkpoint_config(plan: LoadPlan) -> PretrainedConfig:
    """The config in `plan`'s directory, read before the weights are."""
    return AutoConfig.from_pretrained(
        plan.directory, trust_remote_code=False, local_files_only=True
    )


def automodel_options(config: PretrainedConfig) -> dict[str, Any]:
    """Which `transformers` auto class builds the model, when it is not nnsight's default.

    nnsight loads through `AutoModelForCausalLM` and refuses an image-text
    `model_type` there, even one like Qwen3.5 that `transformers` could
    also load as text alone. Forcing the text config through instead would
    work for those, but not for one with no text-only class, and would
    leave Lab reading a different model from the one that was published.
    """
    if config.model_type in MODEL_FOR_IMAGE_TEXT_TO_TEXT_MAPPING_NAMES:
        return {"automodel": AutoModelForImageTextToText}
    return {}


def rename_config(config: PretrainedConfig) -> RenameConfig | None:
    """How nnterp is told about a hybrid model, whose first layer has no attention.

    nnterp checks for `self_attn` on layer 0 only, and refuses the model
    when it is missing. Telling it to skip that check leaves `self_attn`
    readable on the layers that have one; `LoadedModel.attention_layers`
    is what keeps Lab from asking for it on the others.
    """
    if LINEAR_ATTENTION in (getattr(config, "layer_types", None) or ()):
        return RenameConfig(ignore_attn=True)
    return None


def load_model(plan: LoadPlan, factory: ModelFactory = StandardizedTransformer) -> LoadedModel:
    logger.info(
        "loading %s model %s on %s as %s",
        plan.architecture,
        plan.directory.name,
        plan.device,
        plan.dtype,
    )
    config = checkpoint_config(plan)
    text = config.get_text_config()
    try:
        model = factory(
            str(plan.directory),
            rename_config=rename_config(text),
            **automodel_options(config),
            **load_options(plan),
        )
    except RenamingError as error:
        # nnterp's own message tells a developer which rename argument to
        # pass; the person who chose the model can only choose another.
        logger.warning("nnterp cannot map %s: %s", plan.architecture, error)
        raise ModelUnavailable(
            f"Lab cannot inspect {plan.architecture} models yet: it could not find the "
            "layers, attention and MLP it reads in them. Choose a model of another "
            "architecture."
        ) from None
    num_layers = int(model.num_layers)
    return LoadedModel(
        plan=plan,
        model=model,
        text_config=text,
        num_layers=num_layers,
        attention_layers=frozenset(
            layer for layer in range(num_layers) if hasattr(model.layers[layer], "self_attn")
        ),
    )
