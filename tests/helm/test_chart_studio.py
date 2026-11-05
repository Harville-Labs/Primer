"""Studio in the chart: off unless asked for, and only ever pointed at a server.

The chart runs no image model. It tells Chat where one is and what it
serves, and gives Control the limits and the retention job.
"""

from __future__ import annotations

from typing import Any

from test_chart import containers, env_of, named, of_kind, refusal, render
from test_chart_qdrant import admitted

STUDIO = ("studio.enabled=true",)
DECLARED = (
    "inference.images.baseUrl=http://comfy.example:8188",
    "inference.images.capabilities.api=comfyui",
    "inference.images.capabilities.sizes={1024x1024}",
)


def env_names(deployment: dict[str, Any]) -> set[str]:
    return {entry["name"] for entry in containers(deployment)[0]["env"]}


def test_by_default_nothing_about_studio_renders() -> None:
    manifests = render(*DECLARED)

    for suffix in ("-chat", "-control"):
        names = env_names(named(manifests, "Deployment", suffix))
        assert names, suffix
        assert not {n for n in names if n.startswith(("PRIMER_STUDIO_", "PRIMER_IMAGES_"))}
    assert not of_kind(manifests, "CronJob")


def test_enabled_studio_tells_chat_where_the_image_server_is() -> None:
    chat = env_of(named(render(*STUDIO, *DECLARED), "Deployment", "-chat"))

    assert chat["PRIMER_IMAGES_BASE_URL"] == "http://comfy.example:8188"
    assert '"api":"comfyui"' in chat["PRIMER_IMAGES_CAPABILITIES"]
    assert chat["PRIMER_IMAGES_TIMEOUT_SECONDS"] == "300"


def test_enabled_studio_gives_control_its_limits() -> None:
    control = env_of(named(render(*STUDIO, *DECLARED), "Deployment", "-control"))

    assert control["PRIMER_STUDIO_ENABLED"] == "true"
    assert control["PRIMER_STUDIO_GENERATIONS_PER_HOUR"] == "30"
    assert control["PRIMER_STUDIO_QUOTA_BYTES"] == "1073741824"


def test_the_image_key_comes_only_from_a_secret() -> None:
    chat = named(
        render(*STUDIO, *DECLARED, "inference.images.existingSecret=primer-images"),
        "Deployment",
        "-chat",
    )
    [entry] = [e for e in containers(chat)[0]["env"] if e["name"] == "PRIMER_IMAGES_API_KEY"]

    assert "value" not in entry
    assert entry["valueFrom"]["secretKeyRef"] == {"name": "primer-images", "key": "images-api-key"}


def test_a_server_with_nothing_declared_is_refused() -> None:
    """Chat would refuse to start; the chart says so before anything is deployed."""
    message = refusal(*STUDIO, "inference.images.baseUrl=http://comfy.example:8188")

    assert "capabilities" in message


def test_retention_runs_only_when_a_period_is_set() -> None:
    assert not of_kind(render(*STUDIO, *DECLARED), "CronJob")

    job = named(
        render(*STUDIO, *DECLARED, "studio.retentionDays=30"), "CronJob", "-studio-retention"
    )
    pod = job["spec"]["jobTemplate"]["spec"]["template"]["spec"]
    [container] = pod["containers"]
    assert container["command"] == ["python", "-m", "primer_control.studio.retention"]
    env = {e["name"]: e.get("value") for e in container["env"]}
    assert env["PRIMER_STUDIO_RETENTION_DAYS"] == "30"


def test_the_retention_job_may_reach_the_bundled_database() -> None:
    """Ingress policies list who may connect; a job left off cannot delete anything."""
    manifests = render(
        *STUDIO, *DECLARED, "studio.retentionDays=30", "postgresql.bundled.enabled=true"
    )
    [policy] = [
        p
        for p in of_kind(manifests, "NetworkPolicy")
        if p["metadata"]["name"].endswith("-allow-postgresql")
    ]

    assert "studio-retention" in admitted(policy)


VISION = ("inference.vision.baseUrl=http://vision.example/v1", "inference.vision.model=qwen-vl")


def test_a_vision_model_lets_studio_images_into_libraries() -> None:
    """Control offers the action and the parse worker reads the image, together."""
    manifests = render(*STUDIO, *VISION)
    parse = env_of(named(manifests, "Deployment", "-worker-parse"))

    assert env_of(named(manifests, "Deployment", "-control"))["PRIMER_VISION_ENABLED"] == "true"
    assert parse["PRIMER_VISION_BASE_URL"] == "http://vision.example/v1"
    assert parse["PRIMER_VISION_MODEL"] == "qwen-vl"


def test_a_vision_model_without_studio_describes_figures_but_offers_no_images() -> None:
    """Figures need no Studio; adding a Studio image to a library does.

    The worker gets the model either way, and Control is told so it can
    report it on the settings page. Control's own Studio gate is what keeps
    the library action away; `test_studio_library` pins that side.
    """
    manifests = render(*VISION)
    control = env_of(named(manifests, "Deployment", "-control"))

    assert control["PRIMER_VISION_ENABLED"] == "true"
    assert "PRIMER_STUDIO_ENABLED" not in control
    parse = env_of(named(manifests, "Deployment", "-worker-parse"))
    assert parse["PRIMER_VISION_MODEL"] == "qwen-vl"
    assert parse["PRIMER_DESCRIBE_FIGURES"] == "true"


def test_figures_can_be_kept_from_the_vision_model() -> None:
    parse = env_of(
        named(render(*VISION, "ingestion.describeFigures=false"), "Deployment", "-worker-parse")
    )

    assert parse["PRIMER_DESCRIBE_FIGURES"] == "false"
    assert parse["PRIMER_VISION_MODEL"] == "qwen-vl"


def test_without_a_vision_model_the_worker_is_told_nothing_about_one() -> None:
    parse = env_names(named(render(), "Deployment", "-worker-parse"))

    assert parse
    assert not {n for n in parse if n.startswith("PRIMER_VISION_")}
    assert "PRIMER_DESCRIBE_FIGURES" not in parse


def test_the_vision_key_comes_only_from_a_secret() -> None:
    parse = named(
        render(*STUDIO, *VISION, "inference.vision.existingSecret=primer-vision"),
        "Deployment",
        "-worker-parse",
    )
    [entry] = [e for e in containers(parse)[0]["env"] if e["name"] == "PRIMER_VISION_API_KEY"]

    assert entry["valueFrom"]["secretKeyRef"] == {"name": "primer-vision", "key": "vision-api-key"}


def test_a_vision_endpoint_without_a_model_is_refused() -> None:
    message = refusal(*STUDIO, "inference.vision.baseUrl=http://vision.example/v1")

    assert "inference.vision.model is required" in message
