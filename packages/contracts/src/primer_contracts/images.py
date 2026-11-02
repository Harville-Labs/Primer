"""Making images through the same providers that answer questions.

An image endpoint is reached the way a chat endpoint is: a URL, perhaps a
key, added on the settings page or configured in the chart. A hosted API
usually serves both from one account, and a key entered twice is a key that
is rotated once. So an image backend is a provider like any other, which
declares that it also makes images, and what it can do with them. An
endpoint that only makes images (a vLLM-Omni beside a chat vLLM) is a
provider that does not serve chat.

**Everything here is declared by the operator, never inferred.** Neither
API reports reliably which operations a model behind it supports, which
sizes it allows, or how many images it will return. A guess that is wrong
shows a user a control that fails after they have waited for it, so what is
not declared is not offered, and a request for it is refused before any
backend is called.
"""

from __future__ import annotations

import json
from typing import Annotated, Any, Literal, Self
from uuid import UUID

from pydantic import Base64Bytes, Field, field_validator, model_validator

from primer_contracts.base import WireModel

#: How Primer talks to the backend. `openai` is `/images/generations` and
#: `/images/edits`, which vLLM-Omni, LocalAI and hosted APIs serve.
#: `comfyui` runs workflow templates the operator registered.
ImageApi = Literal["openai", "comfyui"]

#: `edit` changes a whole image by instruction; `inpaint` changes only the
#: part a mask marks. They are separate because many servers that edit do
#: not take a mask.
ImageOperation = Literal["generate", "edit", "inpaint"]

#: Width by height, as the OpenAI API spells it.
ImageSize = Annotated[str, Field(pattern=r"^[1-9][0-9]{1,4}x[1-9][0-9]{1,4}$")]

ImageModel = Annotated[str, Field(min_length=1, max_length=200)]

#: The most images any request may ask for, whatever a provider declares.
MAX_IMAGES_PER_REQUEST = 10

#: An input image or mask, decoded. Bounded here because Chat holds the
#: whole request in memory; Control's own upload limit is lower.
MAX_INPUT_BYTES = 25 * 1024 * 1024


#: A ComfyUI node's key in an API-format workflow, usually a number.
NodeId = Annotated[str, Field(min_length=1, max_length=64)]

#: Bounds on a stored template. Chat copies one per request and the
#: settings page shows it, and nothing an operator exports is near these.
MAX_WORKFLOW_NODES = 500
MAX_WORKFLOW_BYTES = 1024 * 1024


class NodeInput(WireModel):
    """One input of one node in a workflow, which Primer fills."""

    node: NodeId
    input: str = Field(min_length=1, max_length=200)


class ComfyBindings(WireModel):
    """Where in a template each value a user chooses goes.

    Anything not bound keeps the value the template was exported with. A
    template with no seed binding makes the same image every time, which an
    operator may want; one with no size binding makes the size it makes,
    so declare only that size for it.
    """

    prompt: NodeInput
    negative_prompt: NodeInput | None = None
    seed: NodeInput | None = None
    width: NodeInput | None = None
    height: NodeInput | None = None
    steps: NodeInput | None = None
    image: NodeInput | None = None
    mask: NodeInput | None = None
    #: The node whose images are the result. Others (previews, a saved
    #: intermediate) are ignored.
    output: NodeId


class ComfyTemplate(WireModel):
    """An operator's ComfyUI workflow, and what a user may change in it.

    Users never submit workflows. ComfyUI custom nodes are arbitrary Python,
    so an arbitrary workflow is arbitrary code on the operator's GPU host;
    what a user sends fills the bound inputs of one of these and nothing
    else.
    """

    #: What a user picks, in the place a model name goes for other APIs.
    name: ImageModel
    operations: tuple[ImageOperation, ...] = Field(min_length=1, max_length=3)
    #: In ComfyUI's API format: node id to `class_type` and `inputs`, as
    #: "Export (API)" writes it. Not the format the editor saves.
    workflow: dict[NodeId, dict[str, Any]] = Field(min_length=1, max_length=MAX_WORKFLOW_NODES)
    bindings: ComfyBindings

    @model_validator(mode="after")
    def _bindings_exist(self) -> Self:
        # Checked on save, because a binding that names nothing fails only
        # when someone runs it, and ComfyUI's answer then names the node
        # without saying it was the binding that was wrong.
        if len(json.dumps(self.workflow)) > MAX_WORKFLOW_BYTES:
            raise ValueError(f"the workflow is larger than {MAX_WORKFLOW_BYTES} bytes")
        for node_id, node in self.workflow.items():
            if not isinstance(node.get("class_type"), str) or not isinstance(
                node.get("inputs"), dict
            ):
                raise ValueError(
                    f"node {node_id!r} has no class_type and inputs. "
                    "Export the workflow in API format."
                )
        for field in ComfyBindings.model_fields:
            bound = getattr(self.bindings, field)
            if not isinstance(bound, NodeInput):
                continue
            node = self.workflow.get(bound.node)
            if node is None:
                raise ValueError(
                    f"the {field} binding names node {bound.node!r}, which is not in the workflow"
                )
            if bound.input not in node["inputs"]:
                raise ValueError(
                    f"the {field} binding names input {bound.input!r} of node {bound.node!r}, "
                    "which that node does not have"
                )
        if self.bindings.output not in self.workflow:
            raise ValueError(
                f"the output is node {self.bindings.output!r}, which is not in the workflow"
            )
        if set(self.operations) & {"edit", "inpaint"} and self.bindings.image is None:
            raise ValueError("a template that edits needs an image binding")
        if "inpaint" in self.operations and self.bindings.mask is None:
            raise ValueError("a template that inpaints needs a mask binding")
        return self


class ImageCapabilities(WireModel):
    """What a provider has been declared to do with images."""

    api: ImageApi = "openai"
    #: Which of the models it serves make images. Named rather than found
    #: through `/models`, which lists chat and image models alike and does
    #: not say which is which.
    models: tuple[ImageModel, ...] = Field(min_length=1, max_length=20)
    operations: tuple[ImageOperation, ...] = Field(min_length=1, max_length=3)
    sizes: tuple[ImageSize, ...] = Field(min_length=1, max_length=20)
    max_images: int = Field(default=1, ge=1, le=MAX_IMAGES_PER_REQUEST)
    #: Send `response_format=b64_json`. DALL-E models and LocalAI return
    #: links unless asked for the image itself; GPT Image models always
    #: return it and refuse the parameter. Primer never follows a link a
    #: backend returns, so a server that sends links needs this set.
    ask_for_base64: bool = False
    #: For `comfyui` only. Each is offered as a model, named after it.
    templates: tuple[ComfyTemplate, ...] = Field(default=(), max_length=20)

    @model_validator(mode="before")
    @classmethod
    def _models_from_templates(cls, data: Any) -> Any:
        # A ComfyUI provider's models and operations are its templates'.
        # Derived rather than asked for twice, so the two cannot disagree.
        if isinstance(data, dict) and data.get("api") == "comfyui":
            templates = data.get("templates") or ()
            names = [t.name if isinstance(t, ComfyTemplate) else t.get("name") for t in templates]
            operations = [
                operation
                for t in templates
                for operation in (
                    t.operations if isinstance(t, ComfyTemplate) else t.get("operations", ())
                )
            ]
            data = {"models": names, "operations": operations, **data}
        return data

    @field_validator("models", "operations", "sizes")
    @classmethod
    def _unique(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        # Order kept: it is the order they are offered in.
        return tuple(dict.fromkeys(value))

    @model_validator(mode="after")
    def _templates_match_api(self) -> Self:
        if self.api == "openai" and self.templates:
            raise ValueError("templates are for ComfyUI; set api to comfyui")
        if self.api == "comfyui":
            names = [template.name for template in self.templates]
            if not names:
                raise ValueError("a ComfyUI provider needs at least one template")
            if len(set(names)) != len(names):
                raise ValueError("two templates share a name; a user could not tell them apart")
            if set(self.models) != set(names):
                raise ValueError("a ComfyUI provider's models are its templates' names")
            if self.max_images != 1:
                # One run makes what the workflow makes. Counting would
                # need a batch binding, and nothing asks for it yet.
                raise ValueError("a ComfyUI provider makes one image per request")
        return self

    def template(self, name: str) -> ComfyTemplate | None:
        return next((t for t in self.templates if t.name == name), None)


class ImageRequest(WireModel):
    """One generation or edit, as Control asks Chat for it."""

    provider_id: UUID
    model: ImageModel
    operation: ImageOperation
    prompt: str = Field(min_length=1, max_length=4000)
    #: Sent as an extension field. Servers that do not know it ignore it.
    negative_prompt: str | None = Field(default=None, max_length=4000)
    size: ImageSize
    count: int = Field(default=1, ge=1, le=MAX_IMAGES_PER_REQUEST)
    #: Extension fields too, which vLLM-Omni and LocalAI read.
    seed: int | None = Field(default=None, ge=0, le=2**32 - 1)
    steps: int | None = Field(default=None, ge=1, le=200)
    #: The image being edited, and for inpainting the mask: white where the
    #: image may change.
    image: Base64Bytes | None = None
    mask: Base64Bytes | None = None

    @model_validator(mode="after")
    def _inputs_match_operation(self) -> Self:
        if self.operation == "generate" and (self.image or self.mask):
            raise ValueError("generating from a prompt takes no image or mask")
        if self.operation != "generate" and not self.image:
            raise ValueError(f"{self.operation} needs the image to change")
        if self.operation == "edit" and self.mask:
            raise ValueError("a mask is for inpaint; edit changes the whole image")
        if self.operation == "inpaint" and not self.mask:
            raise ValueError("inpaint needs a mask marking what may change")
        for name, data in (("image", self.image), ("mask", self.mask)):
            if data is not None and len(data) > MAX_INPUT_BYTES:
                raise ValueError(f"the {name} is larger than {MAX_INPUT_BYTES} bytes")
        return self


class ImageResult(WireModel):
    """The images a backend returned, as it returned them.

    Not yet trusted. Control decodes and re-encodes each before storing it,
    because the backend is the operator's choice but still outside Primer.
    """

    provider_id: UUID
    model: ImageModel
    images: tuple[Base64Bytes, ...] = Field(min_length=1, max_length=MAX_IMAGES_PER_REQUEST)
    #: The seed the images were made with, when known: the one asked for,
    #: or the one Primer chose for a template that binds a seed. Part of an
    #: image's provenance, and what makes it reproducible.
    seed: int | None = Field(default=None, ge=0, le=2**32 - 1)


class ImageProvider(WireModel):
    """A provider that makes images, as any signed-in user may see it.

    No URL and nothing about keys: that is for the settings page. What a
    user needs is what to pick and what each choice allows.
    """

    provider_id: UUID
    provider_name: str
    capabilities: ImageCapabilities


class ImageStreamEvent(WireModel):
    """Base for what `/images/stream` sends, over SSE like a chat answer."""

    id: int = Field(ge=0)


class ImageProgress(ImageStreamEvent):
    """Steps done of the node running now, as ComfyUI reports them.

    Per node rather than for the run: ComfyUI counts each sampler's steps
    on its own and does not say how many nodes are left.
    """

    type: Literal["progress"] = "progress"
    value: int = Field(ge=0)
    max: int = Field(ge=1)


class ImageDone(ImageStreamEvent):
    type: Literal["done"] = "done"
    result: ImageResult


class ImageFailed(ImageStreamEvent):
    """A failure after the stream began, carrying what a problem body would.

    The status is kept because it is how a caller knows whether to try
    again: 503 and 504 are worth it, 502 and 422 are not.
    """

    type: Literal["failed"] = "failed"
    code: str = Field(min_length=1, max_length=64)
    title: str = Field(min_length=1, max_length=200)
    detail: str | None = Field(default=None, max_length=2000)
    status: int = Field(ge=400, le=599)
