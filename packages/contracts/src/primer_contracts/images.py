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

from typing import Annotated, Literal, Self
from uuid import UUID

from pydantic import Base64Bytes, Field, field_validator, model_validator

from primer_contracts.base import WireModel

#: How Primer talks to the backend. Only the OpenAI images API for now:
#: `/images/generations` and `/images/edits`, which vLLM-Omni, LocalAI and
#: hosted APIs serve.
ImageApi = Literal["openai"]

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

    @field_validator("models", "operations", "sizes")
    @classmethod
    def _unique(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        # Order kept: it is the order they are offered in.
        return tuple(dict.fromkeys(value))


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


class ImageProvider(WireModel):
    """A provider that makes images, as any signed-in user may see it.

    No URL and nothing about keys: that is for the settings page. What a
    user needs is what to pick and what each choice allows.
    """

    provider_id: UUID
    provider_name: str
    capabilities: ImageCapabilities
