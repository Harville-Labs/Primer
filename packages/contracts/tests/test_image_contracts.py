"""What an image request may carry, and what a provider must declare."""

from __future__ import annotations

import base64
from uuid import uuid4

import pytest
from primer_contracts.images import ComfyTemplate, ImageCapabilities, ImageRequest
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


def comfy_template(**changes: object) -> dict[str, object]:
    template: dict[str, object] = {
        "name": "sdxl",
        "operations": ["generate"],
        "workflow": {
            "6": {"class_type": "CLIPTextEncode", "inputs": {"text": ""}},
            "9": {"class_type": "SaveImage", "inputs": {"images": ["6", 0]}},
        },
        "bindings": {"prompt": {"node": "6", "input": "text"}, "output": "9"},
    }
    return {**template, **changes}


class TestComfyTemplates:
    def test_a_binding_to_a_missing_node_is_refused_naming_the_node(self) -> None:
        bindings = {
            "prompt": {"node": "6", "input": "text"},
            "seed": {"node": "42", "input": "seed"},
        }
        with pytest.raises(ValidationError, match="seed binding names node '42'"):
            ComfyTemplate.model_validate(comfy_template(bindings={**bindings, "output": "9"}))

    def test_a_binding_to_a_missing_input_is_refused(self) -> None:
        bindings = {"prompt": {"node": "6", "input": "prompt"}, "output": "9"}
        with pytest.raises(ValidationError, match="input 'prompt' of node '6'"):
            ComfyTemplate.model_validate(comfy_template(bindings=bindings))

    def test_a_workflow_in_the_editors_format_is_refused(self) -> None:
        """The editor saves `nodes` and `links`, which `/prompt` does not take."""
        with pytest.raises(ValidationError, match="API format"):
            ComfyTemplate.model_validate(comfy_template(workflow={"6": {"type": "CLIPTextEncode"}}))

    def test_an_editing_template_needs_somewhere_to_put_the_image(self) -> None:
        with pytest.raises(ValidationError, match="image binding"):
            ComfyTemplate.model_validate(comfy_template(operations=["edit"]))

    def test_a_comfyui_providers_models_are_its_templates(self) -> None:
        declared = ImageCapabilities.model_validate(
            {"api": "comfyui", "sizes": ["1024x1024"], "templates": [comfy_template()]}
        )

        assert declared.models == ("sdxl",)
        assert declared.operations == ("generate",)

    def test_templates_are_refused_for_the_openai_api(self) -> None:
        with pytest.raises(ValidationError, match="templates are for ComfyUI"):
            ImageCapabilities.model_validate(
                {
                    "models": ["sdxl"],
                    "operations": ["generate"],
                    "sizes": ["1024x1024"],
                    "templates": [comfy_template()],
                }
            )
