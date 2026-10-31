"""What Lab says about itself, and how a user names a model for it.

Lab keeps one model resident and lets users choose which, so the web app
needs three things from it: which model is loaded, whether a switch to
another one is under way, and what this deployment lets a user choose.
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import Field

from primer_contracts.base import WireModel

#: A repository id as the hub writes it, `owner/name`. Held to the hub's own
#: character set so a name can never carry a path separator past the owner.
REPOSITORY_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._-]{0,95}/[A-Za-z0-9][A-Za-z0-9._-]{0,95}$"


class HubModel(WireModel):
    """A model on the Hugging Face hub, or the operator's mirror of it."""

    source: Literal["hub"] = "hub"
    repository: str = Field(pattern=REPOSITORY_PATTERN, examples=["Qwen/Qwen3-4B"])
    revision: str | None = Field(
        default=None,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,127}$",
        description="Branch, tag, or commit; the repository's default branch when unset",
    )


class LocalModel(WireModel):
    """A directory the operator placed in Lab's weights volume."""

    source: Literal["local"] = "local"
    path: str = Field(
        min_length=1,
        max_length=255,
        description="Relative to the weights volume; it cannot leave it",
        examples=["my-finetune"],
    )


ModelReference = Annotated[HubModel | LocalModel, Field(discriminator="source")]


class LoadModelRequest(WireModel):
    model: ModelReference


class LabModelStatus(WireModel):
    """The resident model, as a user choosing what to inspect needs it."""

    reference: ModelReference
    #: The commit a hub revision resolved to when it was fetched, so two
    #: people looking at `main` a week apart can tell they saw the same one.
    commit: str | None = None
    #: The architecture its config names - `qwen3`, `llama` - which says
    #: more about what can be inspected than the repository's name does.
    architecture: str
    device: Literal["cpu", "cuda"]
    dtype: str
    num_layers: int = Field(ge=1)


class ModelSwitch(WireModel):
    """A change of model that has been asked for and has not finished."""

    reference: ModelReference
    state: Literal["fetching", "loading", "failed"]
    #: Why a switch failed, in words the person who asked can act on. The
    #: previous model stays resident when one does.
    error: str | None = Field(default=None, max_length=2000)


class LabStatus(WireModel):
    #: Whether a model is resident and runs can be made against it.
    ready: bool
    model: LabModelStatus | None
    switch: ModelSwitch | None
    #: Whether this caller may ask for a different model.
    can_choose_model: bool
    #: The largest model, in bytes of weights, this deployment will fetch.
    max_model_bytes: int = Field(ge=1)
    #: Runs waiting or in progress. One runs at a time, so this is how long
    #: a new run should expect to wait, in runs.
    queue_depth: int = Field(ge=0)
    max_prompt_tokens: int = Field(ge=1)
    max_new_tokens: int = Field(ge=1)
