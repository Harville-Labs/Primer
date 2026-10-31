"""Policy checks on Lab, the optional service that runs a model of its own.

Lab is the one part of Primer that loads code shaped by a user's choice of
model, so what these guard is how little it is given: no shared secrets,
nothing writable beyond its weights, and nobody but the web app to talk to.
"""

from __future__ import annotations

from typing import Any

from test_chart import containers, env_of, named, of_kind, render
from test_chart_qdrant import admitted

ENABLED = ("lab.enabled=true",)


def lab(rendered: list[dict[str, Any]]) -> dict[str, Any]:
    return named(rendered, "Deployment", "-lab")


def test_lab_is_off_unless_it_is_turned_on() -> None:
    """It needs a machine sized for a model, which a default install is not."""
    rendered = render()
    assert rendered
    for doc in rendered:
        labels = doc["metadata"].get("labels", {})
        assert "lab" not in doc["metadata"]["name"], doc["metadata"]["name"]
        assert labels.get("app.kubernetes.io/component") != "lab"


def test_lab_runs_unprivileged() -> None:
    deployment = lab(render(*ENABLED))
    pod = deployment["spec"]["template"]["spec"]

    assert pod["securityContext"]["runAsNonRoot"] is True
    security = containers(deployment)[0]["securityContext"]
    assert security["allowPrivilegeEscalation"] is False
    assert security["readOnlyRootFilesystem"] is True
    assert security["capabilities"]["drop"] == ["ALL"]


def test_lab_is_replaced_rather_than_rolled() -> None:
    """A second pod would need the volume and a second copy of the model."""
    spec = lab(render(*ENABLED))["spec"]

    assert spec["replicas"] == 1
    assert spec["strategy"] == {"type": "Recreate"}


def test_only_the_web_app_may_reach_lab() -> None:
    """Anything else in the cluster could otherwise use it as free compute."""
    policy = named(render(*ENABLED), "NetworkPolicy", "-allow-lab")
    assert admitted(policy) == {"web"}


def test_lab_writes_only_its_weights_and_scratch() -> None:
    rendered = render(*ENABLED)
    deployment = lab(rendered)
    mounts = {m["mountPath"]: m["name"] for m in containers(deployment)[0]["volumeMounts"]}

    assert set(mounts) == {"/models", "/tmp", "/home/primer"}  # noqa: S108
    volumes = {v["name"]: v for v in deployment["spec"]["template"]["spec"]["volumes"]}
    claim = volumes[mounts["/models"]]["persistentVolumeClaim"]["claimName"]
    assert named(rendered, "PersistentVolumeClaim", "-lab-models")["metadata"]["name"] == claim


def test_lab_is_given_none_of_the_shared_secrets() -> None:
    """A compromise of Lab should reach nothing beyond its own volume."""
    container = containers(lab(render(*ENABLED)))[0]

    assert "envFrom" not in container
    for entry in container["env"]:
        assert "valueFrom" not in entry, entry["name"]
        assert not entry["name"].startswith(("PRIMER_DATABASE", "PRIMER_INTERNAL")), entry["name"]


def test_who_may_choose_a_model_reaches_lab() -> None:
    env = env_of(
        lab(render(*ENABLED, "lab.modelChoice=admins", "lab.repositoryPatterns[0]=Qwen/*"))
    )

    assert env["PRIMER_LAB_MODEL_CHOICE"] == "admins"
    assert env["PRIMER_LAB_REPOSITORY_PATTERNS"] == '["Qwen/*"]'
    assert env["PRIMER_LAB_MODEL"] == '{"repository":"Qwen/Qwen3-1.7B","source":"hub"}'


def test_a_gpu_switches_to_the_cuda_image_and_requests_the_device() -> None:
    container = containers(lab(render(*ENABLED, "lab.gpu=1")))[0]

    assert container["image"].endswith("-cuda")
    assert container["resources"]["limits"]["nvidia.com/gpu"] == 1
    assert container["resources"]["limits"]["memory"] == "12Gi"


def test_without_a_gpu_lab_asks_for_none() -> None:
    container = containers(lab(render(*ENABLED)))[0]

    assert not container["image"].endswith("-cuda")
    assert "nvidia.com/gpu" not in container["resources"]["limits"]


def test_lab_has_its_own_service_account() -> None:
    accounts = [doc["metadata"]["name"] for doc in of_kind(render(*ENABLED), "ServiceAccount")]
    assert any(name.endswith("-lab") for name in accounts)
