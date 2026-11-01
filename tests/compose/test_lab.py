"""Static policy checks on Lab in the Compose deployment.

Lab sits behind a profile, because it needs a machine sized for a model and
most single-user stacks are not one.
"""

from __future__ import annotations

import shutil
from typing import Any

import pytest
from test_config import BASE, COMPOSE_DIR, resolve

LAB = {"COMPOSE_PROFILES": "lab"}


@pytest.fixture(scope="module")
def with_lab() -> dict[str, Any]:
    if shutil.which("docker") is None:
        pytest.skip("docker is not installed")
    return resolve(BASE, env=LAB)


def test_lab_is_off_unless_its_profile_is_on() -> None:
    if shutil.which("docker") is None:
        pytest.skip("docker is not installed")
    assert "lab" not in resolve(BASE)["services"]


def test_lab_runs_hardened(with_lab: dict[str, Any]) -> None:
    service = with_lab["services"]["lab"]

    assert service["read_only"] is True
    assert service["security_opt"] == ["no-new-privileges:true"]
    assert service["cap_drop"] == ["ALL"]


def test_lab_keeps_its_weights_in_a_volume(with_lab: dict[str, Any]) -> None:
    mounts = [m for m in with_lab["services"]["lab"]["volumes"] if m["type"] == "volume"]

    assert [(m["source"], m["target"]) for m in mounts] == [("lab-models", "/models")]


def test_lab_is_not_published(with_lab: dict[str, Any]) -> None:
    """The web app reaches it on the Compose network; nothing else should."""
    assert not with_lab["services"]["lab"].get("ports")


def test_an_unset_lab_variable_reaches_lab_empty(with_lab: dict[str, Any]) -> None:
    """Lab reads an empty variable as its default, so nothing here picks one."""
    environment = with_lab["services"]["lab"]["environment"]

    assert environment["PRIMER_LAB_MODEL"] == ""
    assert environment["PRIMER_AUTH_MODE"] == "disabled"


def test_the_gpu_override_builds_cuda_and_reserves_the_gpu() -> None:
    if shutil.which("docker") is None:
        pytest.skip("docker is not installed")
    service = resolve(BASE, COMPOSE_DIR / "compose.gpu.yaml", env=LAB)["services"]["lab"]

    assert service["build"]["target"] == "cuda"
    devices = service["deploy"]["resources"]["reservations"]["devices"]
    assert devices == [{"driver": "nvidia", "count": 1, "capabilities": ["gpu"]}]


def test_the_web_app_knows_where_lab_is_and_control_waits_to_be_told() -> None:
    """Control cannot see which profiles are on, so the operator sets its URL with the profile."""
    if shutil.which("docker") is None:
        pytest.skip("docker is not installed")
    services = resolve(BASE)["services"]
    assert services["web"]["environment"]["PRIMER_LAB_URL"] == "http://lab:8000"
    assert services["control"]["environment"]["PRIMER_LAB_SERVICE_URL"] == ""
    told = resolve(BASE, env=LAB | {"PRIMER_LAB_SERVICE_URL": "http://lab:8000"})
    assert told["services"]["control"]["environment"]["PRIMER_LAB_SERVICE_URL"] == "http://lab:8000"


def test_lab_saves_to_a_schema_migrated_before_it_starts(with_lab: dict[str, Any]) -> None:
    """In Lab's own image, since no other holds Lab's migrations."""
    services = with_lab["services"]
    migrate = services["migrate-lab"]

    assert migrate["working_dir"] == "/app/services/lab"
    assert migrate["command"] == ["alembic", "upgrade", "head"]
    assert migrate["build"] == services["lab"]["build"]
    assert services["lab"]["depends_on"]["migrate-lab"]["condition"] == (
        "service_completed_successfully"
    )
    assert (
        services["lab"]["environment"]["PRIMER_DATABASE_URL"]
        == (migrate["environment"]["PRIMER_DATABASE_URL"])
    )


def test_the_gpu_override_migrates_from_the_image_lab_runs() -> None:
    if shutil.which("docker") is None:
        pytest.skip("docker is not installed")
    services = resolve(BASE, COMPOSE_DIR / "compose.gpu.yaml", env=LAB)["services"]

    assert services["migrate-lab"]["build"] == services["lab"]["build"]
