"""What Lab says about itself, and how a user names a model for it.

Lab keeps one model resident and lets users choose which, so the web app
needs three things from it: which model is loaded, whether a switch to
another one is under way, and what this deployment lets a user choose.
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import ConfigDict, Field

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


#: How many tokens the logit lens reports at each layer and position.
LENS_TOP = 5
#: The most MLP neurons a reading may ask for at each layer and position.
MAX_NEURONS = 32

#: Whitespace is part of what is being inspected - " world" and "world" are
#: different tokens - so these models keep strings as they were sent,
#: rather than stripping them as every other wire model does.
VERBATIM = ConfigDict(str_strip_whitespace=False)


class PromptInput(WireModel):
    model_config = VERBATIM

    prompt: str = Field(min_length=1, max_length=100_000)
    #: Wrap the prompt in the model's chat template, as a chat turn from the
    #: user, when its tokenizer has one. A base model has none, and reads
    #: the prompt as it is either way.
    chat: bool = True


class InspectRequest(PromptInput):
    neurons: int = Field(default=8, ge=1, le=MAX_NEURONS)


class InspectedToken(WireModel):
    model_config = VERBATIM

    id: int = Field(ge=0)
    text: str
    #: Added by the tokenizer or the chat template rather than written by
    #: the user, so a reader can tell the prompt from its wrapping.
    special: bool


class LayerReadings(WireModel):
    """One layer's summary at every position, as parallel arrays.

    Indexed by token position first. Columns rather than an object per
    position, because a full-length prompt on a 36-layer model is about
    eighteen thousand of them, and naming every field in every one roughly
    doubles a response that is already the largest Lab sends.
    """

    #: The L2 norm of the residual stream after this layer.
    residual_norm: list[float]
    #: The logit lens: the LENS_TOP likeliest next tokens if the model
    #: stopped here, read through its final norm and unembedding.
    lens_tokens: list[list[int]]
    lens_probabilities: list[list[float]]
    #: The MLP neurons with the largest activation by magnitude, read where
    #: they enter the down projection. Activations keep their sign.
    neuron_indices: list[list[int]]
    neuron_activations: list[list[float]]


class InspectResponse(WireModel):
    """What a prompt does inside the resident model.

    Bounded by its shape: num_layers x tokens x (LENS_TOP + neurons) values,
    with tokens at most the deployment's max_prompt_tokens and neurons at
    most MAX_NEURONS.
    """

    model_config = VERBATIM

    model: LabModelStatus
    chat_template: bool = Field(description="Whether the chat template was applied")
    tokens: list[InspectedToken]
    #: The text of every token the lens names, once each.
    vocabulary: dict[int, str]
    hidden_size: int = Field(ge=1)
    mlp_width: int = Field(ge=1)
    #: Indexed by layer, first layer first.
    layers: list[LayerReadings]


#: Where in a layer an activation vector is read. resid_post is the
#: residual stream after the layer; mlp_neuron the MLP's neurons, its
#: intermediate width; mlp_out and attn_out what each block adds.
ActivationSite = Literal["resid_post", "mlp_neuron", "mlp_out", "attn_out"]


class ActivationRequest(PromptInput):
    layer: int = Field(ge=0)
    token: int = Field(ge=0, description="Position in the inspected tokens")
    site: ActivationSite


class ActivationVector(WireModel):
    model: LabModelStatus
    layer: int
    token: int
    site: ActivationSite
    values: list[float]
