"""How Lab reads its own configuration from the environment."""

from __future__ import annotations

import pytest
from primer_lab.config import HubModel, Settings

#: Every variable the Compose profile passes, each as Compose passes an unset one.
COMPOSE_VARIABLES = (
    "PRIMER_LAB_MODEL",
    "PRIMER_LAB_MODEL_CHOICE",
    "PRIMER_LAB_REPOSITORY_PATTERNS",
    "PRIMER_LAB_MAX_MODEL_BYTES",
    "PRIMER_LAB_HUB_ENDPOINT",
    "PRIMER_LAB_DEVICE",
    "PRIMER_LAB_DTYPE",
)


def test_an_empty_variable_means_the_default(monkeypatch: pytest.MonkeyPatch) -> None:
    """Compose passes an unset optional setting as "" rather than leaving it out.

    Read literally, an empty model is invalid JSON and an empty device is not
    a device, so a stack started with the Lab profile and nothing else set
    would refuse to start instead of loading the default model.
    """
    for variable in COMPOSE_VARIABLES:
        monkeypatch.setenv(variable, "")

    settings = Settings()

    assert settings.lab_model == HubModel(repository="Qwen/Qwen3-1.7B")
    assert settings.lab_model_choice == "users"
    assert settings.lab_repository_patterns == ("*/*",)
    assert settings.lab_device == "auto"
    assert settings.lab_hub_endpoint is None
