"""What Lab says about itself, and how a user names a model for it.

Lab keeps one model resident and lets users choose which, so the web app
needs three things from it: which model is loaded, whether a switch to
another one is under way, and what this deployment lets a user choose.
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import ConfigDict, Field, model_validator

from primer_contracts.base import WireModel
from primer_contracts.errors import ProblemDetail

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


class SaeStatus(WireModel):
    """The sparse autoencoders attached to the resident model, if any.

    Fetched after the model loads, so the model is usable while they
    download; until they are ready an inspection reads no features.
    """

    repository: str = Field(pattern=REPOSITORY_PATTERN)
    commit: str | None = None
    state: Literal["fetching", "ready", "failed"]
    error: str | None = Field(default=None, max_length=2000)
    #: Features per SAE, and how many fire at each position.
    width: int | None = Field(default=None, ge=1)
    k: int | None = Field(default=None, ge=1)
    #: The layers an SAE was trained for. Every other layer has none.
    layers: list[int] = Field(default_factory=list)


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
    #: None when this deployment has no SAEs for the model.
    saes: SaeStatus | None = None


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
    #: How long a saved experiment is kept. None when this Lab has no
    #: database, and so cannot save experiments at all.
    experiment_retention_days: int | None = Field(default=None, ge=1)


#: How many tokens the logit lens reports at each layer and position.
LENS_TOP = 5
#: The most MLP neurons a reading may ask for at each layer and position.
MAX_NEURONS = 32
#: The most SAE features a reading may ask for at each layer and position.
MAX_FEATURES = 32

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
    features: int = Field(default=8, ge=1, le=MAX_FEATURES)


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
    #: The SAE features that fire most strongly on the residual stream after
    #: this layer. None where there is no SAE for the layer, or none ready -
    #: which is not the same as no feature firing.
    feature_indices: list[list[int]] | None = None
    feature_activations: list[list[float]] | None = None


class InspectResponse(WireModel):
    """What a prompt does inside the resident model.

    Bounded by its shape: num_layers x tokens x (LENS_TOP + neurons + features)
    values, with tokens at most the deployment's max_prompt_tokens, neurons
    at most MAX_NEURONS, and features at most MAX_FEATURES.
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


#: Where an intervention edits a layer. The activation sites, plus an SAE
#: feature, which is read from and written back to the residual stream
#: after the layer through the SAE trained there.
InterventionSite = Literal["resid_post", "mlp_neuron", "mlp_out", "attn_out", "sae_feature"]
#: zero and mean replace the value; mean is its mean over the prompt's own
#: tokens. scale multiplies by `value`, clamp fixes it at `value`, and add
#: adds `value` - or, with a steering vector, `value` times that vector.
InterventionOp = Literal["zero", "mean", "scale", "clamp", "add"]
#: Which tokens an edit applies to. "all" and "prompt" skip the special
#: tokens a tokenizer or chat template added, which a model reads very
#: differently from text and which an edit meant for the prompt would
#: otherwise swamp. "generated" is every token the model writes, from the
#: first one read back in. A [start, end) span is exactly those positions,
#: special or not.
InterventionPositions = Literal["all", "prompt", "generated"] | tuple[int, int]

#: The sites whose vectors are as wide as the residual stream, which is
#: what a steering vector is.
RESIDUAL_WIDTH_SITES = ("resid_post", "mlp_out", "attn_out")


class Intervention(WireModel):
    """One edit to the forward pass, applied at every step of a generation.

    What can be checked without the model is checked here; the layer and
    index bounds depend on the model resident, and Lab checks those.
    """

    site: InterventionSite
    layer: int = Field(ge=0)
    #: A neuron, a residual component, or a feature. None edits the whole
    #: vector at the site.
    index: int | None = Field(default=None, ge=0)
    op: InterventionOp
    value: float = Field(default=0.0, allow_inf_nan=False)
    vector_id: str | None = Field(default=None, pattern=r"^[0-9a-f]{32}$")
    positions: InterventionPositions = "all"

    @model_validator(mode="after")
    def _well_formed(self) -> Intervention:
        if self.site == "sae_feature" and self.index is None:
            raise ValueError("an sae_feature intervention names its feature in index")
        if self.op == "clamp" and self.index is None:
            raise ValueError("clamp fixes one component at a value, so it needs an index")
        if self.vector_id is not None:
            if self.op != "add" or self.index is not None:
                raise ValueError("a steering vector is added to a whole vector: op add, no index")
            if self.site not in RESIDUAL_WIDTH_SITES:
                raise ValueError(
                    f"a steering vector is residual-width, so it goes at "
                    f"{', '.join(RESIDUAL_WIDTH_SITES)}"
                )
        elif self.op == "add" and self.index is None:
            raise ValueError("adding to a whole vector needs a vector_id to add")
        if not isinstance(self.positions, str):
            start, end = self.positions
            if not 0 <= start < end:
                raise ValueError("a span of positions is [start, end), with 0 <= start < end")
        return self


class GenerateRequest(PromptInput):
    #: Applied in the order the model runs them, by layer and then by site;
    #: edits at the same site apply in the order given here.
    interventions: list[Intervention] = Field(default_factory=list, max_length=256)
    max_new_tokens: int = Field(default=32, ge=1, le=2048)
    #: A token to watch: how likely the model thinks it is as the first
    #: token of the answer, before and after the edits. It must be one
    #: token in this model's vocabulary.
    target: str | None = Field(default=None, min_length=1, max_length=200)


#: One prompt of a set a steering vector or a probe is made from. Each is a
#: forward pass, so the count and the length are both bounded.
SetPrompt = Annotated[str, Field(min_length=1, max_length=20_000)]
MAX_SET_PROMPTS = 64
#: Per set, so every fold of the cross-validation holds out at least one of each.
MIN_PROBE_PROMPTS = 4


class SteeringVectorRequest(WireModel):
    """A difference of means between two sets of prompts at one layer.

    Contrastive activation addition: the mean residual stream after
    `layer`, at each prompt's last token, over the positive prompts minus
    the same over the negative ones. Added back in, it pushes the model
    towards whatever separates the two sets.
    """

    model_config = VERBATIM

    label: str = Field(min_length=1, max_length=120)
    layer: int = Field(ge=0)
    positive: list[SetPrompt] = Field(min_length=1, max_length=MAX_SET_PROMPTS)
    negative: list[SetPrompt] = Field(min_length=1, max_length=MAX_SET_PROMPTS)
    chat: bool = True


class SteeringVector(WireModel):
    """A steering vector Lab holds for its owner until the model changes."""

    id: str
    label: str
    layer: int
    #: Its L2 norm, so a reader can choose a coefficient on the same scale
    #: as the residual stream's own norm.
    norm: float
    positive: int = Field(ge=1)
    negative: int = Field(ge=1)


class ProbeRequest(WireModel):
    """A linear probe trained to tell two sets of prompts apart at one layer.

    Logistic regression on the residual stream after `layer`, at each
    prompt's last token - the same reading a steering vector is made from.
    Each set needs a few prompts, so that some can be held out to measure
    the probe on prompts it was not trained on.
    """

    model_config = VERBATIM

    label: str = Field(min_length=1, max_length=120)
    layer: int = Field(ge=0)
    positive: list[SetPrompt] = Field(min_length=MIN_PROBE_PROMPTS, max_length=MAX_SET_PROMPTS)
    negative: list[SetPrompt] = Field(min_length=MIN_PROBE_PROMPTS, max_length=MAX_SET_PROMPTS)
    chat: bool = True


class Probe(WireModel):
    """A probe Lab holds for its owner until the model changes, with how well it did."""

    id: str
    label: str
    layer: int
    positive: int = Field(ge=1)
    negative: int = Field(ge=1)
    #: The share of prompts labelled correctly while held out of training,
    #: averaged over `folds` folds of cross-validation.
    accuracy: float = Field(ge=0, le=1)
    folds: int = Field(ge=2)
    #: The share a probe would get by always answering the larger set.
    chance: float = Field(ge=0, le=1)
    #: Whether `accuracy` is more than two standard errors above `chance`.
    #: A probe that is not has learned nothing to trust.
    beats_chance: bool


class TargetChange(WireModel):
    """The watched token at the first answer position, before and after."""

    token: InspectedToken
    baseline_logit: float
    edited_logit: float
    baseline_probability: float
    edited_probability: float


class GenerationEvent(WireModel):
    #: Monotonic within a stream.
    id: int = Field(ge=0)


class GenerationQueued(GenerationEvent):
    type: Literal["queued"] = "queued"
    #: Runs ahead of this one. Sent each time it changes, and not at all
    #: when the run starts at once.
    ahead: int = Field(ge=1)


class GeneratedToken(GenerationEvent):
    """One token of the baseline or the edited answer, as it is written.

    The baseline is written in full before the edited answer starts.
    """

    type: Literal["token"] = "token"
    run: Literal["baseline", "edited"]
    index: int = Field(ge=0)
    token: InspectedToken


class GenerationCompleted(GenerationEvent):
    model_config = VERBATIM

    type: Literal["completed"] = "completed"
    model: LabModelStatus
    prompt: list[InspectedToken]
    baseline: list[InspectedToken]
    edited: list[InspectedToken]
    #: The first position at which the two answers differ, or None when
    #: they are the same. One being a prefix of the other differs where the
    #: shorter one ends.
    diverged_at: int | None = Field(default=None, ge=0)
    target: TargetChange | None = None


class GenerationFailed(GenerationEvent):
    """A terminal failure, as the problem a plain request would have answered."""

    type: Literal["error"] = "error"
    problem: ProblemDetail


GenerationStreamEvent = Annotated[
    GenerationQueued | GeneratedToken | GenerationCompleted | GenerationFailed,
    Field(discriminator="type"),
]
