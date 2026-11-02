"""Studio's images, as Control keeps them and the web app shows them.

Chat makes images and Control keeps them. These are Control's half: a
stored image with the provenance that says how it was made, the request a
person sends to make one, and what an administrator may see about use.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal, Self
from uuid import UUID

from pydantic import Base64Bytes, Field, model_validator

from primer_contracts.base import WireModel
from primer_contracts.images import (
    MAX_IMAGES_PER_REQUEST,
    MAX_INPUT_BYTES,
    ImageModel,
    ImageOperation,
    ImageSize,
    ImageStreamEvent,
)

#: An image a person uploaded to edit, or one a provider made.
StudioOrigin = Literal["upload", "generated"]

#: Everything Control stores is re-encoded to this, whatever came in.
STUDIO_MEDIA_TYPE = "image/png"


class StudioProvenance(WireModel):
    """How a generated image was made, kept so it can be made again.

    The seed is the one the provider used when it said, which is not always
    the one asked for: Primer chooses one for a template that binds a seed.
    """

    provider_id: UUID
    model: ImageModel
    operation: ImageOperation
    prompt: str
    negative_prompt: str | None = None
    size: ImageSize
    seed: int | None = None
    steps: int | None = None


class StudioImage(WireModel):
    id: UUID
    origin: StudioOrigin
    media_type: str
    width: int = Field(ge=1)
    height: int = Field(ge=1)
    byte_size: int = Field(ge=0)
    created_at: datetime
    #: The image an edit was made from. Null for an upload, a generation from
    #: a prompt, or an edit whose source has since been deleted.
    parent_id: UUID | None = None
    #: Null for an upload, which has no recipe.
    provenance: StudioProvenance | None = None


class StudioGeneration(WireModel):
    """One request to make images, as a person sends it.

    The same fields as Chat's `ImageRequest`, except that the image to edit
    is named by id rather than sent: it is one of the person's own Studio
    images, which is what lets the result record where it came from.
    """

    provider_id: UUID
    model: ImageModel
    operation: ImageOperation
    prompt: str = Field(min_length=1, max_length=4000)
    negative_prompt: str | None = Field(default=None, max_length=4000)
    size: ImageSize
    count: int = Field(default=1, ge=1, le=MAX_IMAGES_PER_REQUEST)
    seed: int | None = Field(default=None, ge=0, le=2**32 - 1)
    steps: int | None = Field(default=None, ge=1, le=200)
    source_id: UUID | None = None
    #: White where the image may change. Sent rather than stored: a mask is
    #: a working tool for one request, not an image anyone wants in a gallery.
    mask: Base64Bytes | None = None

    @model_validator(mode="after")
    def _inputs_match_operation(self) -> Self:
        if self.operation == "generate" and (self.source_id or self.mask):
            raise ValueError("generating from a prompt takes no source image or mask")
        if self.operation != "generate" and self.source_id is None:
            raise ValueError(f"{self.operation} needs the image to change")
        if self.operation == "edit" and self.mask:
            raise ValueError("a mask is for inpaint; edit changes the whole image")
        if self.operation == "inpaint" and not self.mask:
            raise ValueError("inpaint needs a mask marking what may change")
        if self.mask is not None and len(self.mask) > MAX_INPUT_BYTES:
            raise ValueError(f"the mask is larger than {MAX_INPUT_BYTES} bytes")
        return self


class StudioProgress(ImageStreamEvent):
    type: Literal["progress"] = "progress"
    value: int = Field(ge=0)
    max: int = Field(ge=1)


class StudioDone(ImageStreamEvent):
    """The images, stored. Sent only once they are saved."""

    type: Literal["done"] = "done"
    images: tuple[StudioImage, ...] = Field(min_length=1)


class StudioFailed(ImageStreamEvent):
    """Shaped like Chat's `ImageFailed`, so the web app reads one kind."""

    type: Literal["failed"] = "failed"
    code: str = Field(min_length=1, max_length=64)
    title: str = Field(min_length=1, max_length=200)
    detail: str | None = Field(default=None, max_length=2000)
    status: int = Field(ge=400, le=599)


class StudioUsage(WireModel):
    """One person's use of Studio, as an administrator may see it.

    Counts and sizes only. An administrator decides limits and needs to know
    who is near them; nothing about that needs the images or the prompts.
    """

    user_id: UUID
    email: str | None = None
    image_count: int = Field(ge=0)
    byte_size: int = Field(ge=0)
