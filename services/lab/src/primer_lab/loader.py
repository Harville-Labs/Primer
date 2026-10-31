"""Load a model, and only in the one way Lab allows.

nnterp's `StandardizedTransformer` wraps nnsight around a `transformers`
model and gives every architecture the same names - `layers_output[i]`,
`mlps_output[i]` - so the inspection code is written once rather than per
model family. That matters more because users choose the model.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import torch
from nnterp import StandardizedTransformer

from primer_lab.config import Settings
from primer_lab.sources import Resolved

logger = logging.getLogger(__name__)

DTYPES = {"float32": torch.float32, "bfloat16": torch.bfloat16, "float16": torch.float16}


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
    num_layers: int


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


def load_model(plan: LoadPlan, factory: ModelFactory = StandardizedTransformer) -> LoadedModel:
    logger.info(
        "loading %s model %s on %s as %s",
        plan.architecture,
        plan.directory.name,
        plan.device,
        plan.dtype,
    )
    model = factory(str(plan.directory), **load_options(plan))
    return LoadedModel(plan=plan, model=model, num_layers=int(model.num_layers))
