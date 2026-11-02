"""Studio in Compose: pointed at an image server, never running one."""

from __future__ import annotations

import shutil

import pytest
from test_config import BASE, resolve


def test_studio_is_off_and_points_at_nothing_by_default() -> None:
    if shutil.which("docker") is None:
        pytest.skip("docker is not installed")
    services = resolve(BASE)["services"]

    assert services["control"]["environment"]["PRIMER_STUDIO_ENABLED"] == "false"
    assert services["chat"]["environment"]["PRIMER_IMAGES_BASE_URL"] == ""
    assert services["chat"]["environment"]["PRIMER_IMAGES_CAPABILITIES"] == ""


def test_the_image_server_is_the_operators_own() -> None:
    """Primer calls an image model; it does not bundle one."""
    if shutil.which("docker") is None:
        pytest.skip("docker is not installed")
    theirs = "http://host.docker.internal:8188"
    services = resolve(
        BASE, env={"PRIMER_STUDIO_ENABLED": "true", "PRIMER_IMAGES_BASE_URL": theirs}
    )["services"]

    assert services["chat"]["environment"]["PRIMER_IMAGES_BASE_URL"] == theirs
    assert not [name for name in services if "comfy" in name or "image" in name]
