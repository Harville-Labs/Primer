"""What an image request may carry, and what a provider must declare."""

from __future__ import annotations

import base64
from uuid import uuid4

import pytest
from primer_contracts.images import ImageCapabilities, ImageRequest
from primer_contracts.providers import ProviderCreate
from pydantic import ValidationError

IMAGE = base64.b64encode(b"png").decode()


def request(**fields: object) -> ImageRequest:
    return ImageRequest.model_validate(
        {
            "provider_id": uuid4(),
            "model": "flux",
            "operation": "generate",
            "prompt": "A heron",
            "size": "1024x1024",
            **fields,
        }
    )


def test_a_generation_from_a_prompt_alone_is_accepted() -> None:
    assert request().image is None


@pytest.mark.parametrize(
    "fields",
    [
        {"image": IMAGE},
        {"operation": "edit"},
        {"operation": "edit", "image": IMAGE, "mask": IMAGE},
        {"operation": "inpaint", "image": IMAGE},
        {"size": "big"},
        {"count": 11},
    ],
)
def test_inputs_that_do_not_fit_the_operation_are_refused(fields: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        request(**fields)


def test_an_inpaint_with_image_and_mask_is_accepted() -> None:
    assert request(operation="inpaint", image=IMAGE, mask=IMAGE).mask == b"png"


def test_repeated_declarations_are_kept_once_in_order() -> None:
    declared = ImageCapabilities(
        models=("b", "a", "b"), operations=("generate",), sizes=("512x512",)
    )
    assert declared.models == ("b", "a")


def test_a_provider_must_serve_something() -> None:
    with pytest.raises(ValidationError, match="chat, make images, or both"):
        ProviderCreate(name="x", base_url="http://x/v1", serves_chat=False)
