"""How a deployment's image endpoint is configured, refused at startup if wrong."""

from __future__ import annotations

import pytest
from primer_chat.config import Settings
from primer_chat.providers_store import deployment_image_provider, deployment_provider
from pydantic import ValidationError

DECLARED = {"models": ["flux"], "operations": ["generate"], "sizes": ["1024x1024"]}


def test_a_url_with_nothing_declared_is_refused() -> None:
    with pytest.raises(ValidationError, match="PRIMER_IMAGES_CAPABILITIES is not"):
        Settings(images_base_url="http://omni/v1")


def test_a_declaration_with_no_endpoint_is_refused() -> None:
    with pytest.raises(ValidationError, match="no endpoint to make images with"):
        Settings(images_capabilities=DECLARED)


def test_the_same_url_as_chat_is_one_provider() -> None:
    settings = Settings(
        chat_base_url="http://both/v1",
        images_base_url="http://both/v1",
        images_capabilities=DECLARED,
    )
    chat = deployment_provider(settings)

    assert deployment_image_provider(settings) is None
    assert chat is not None and chat.images is not None


def test_a_url_of_its_own_serves_no_chat() -> None:
    settings = Settings(
        chat_base_url="http://chat/v1",
        images_base_url="http://omni/v1",
        images_capabilities=DECLARED,
    )
    chat = deployment_provider(settings)
    images = deployment_image_provider(settings)

    assert chat is not None and chat.images is None
    assert images is not None and not images.serves_chat
    assert images.id != chat.id
