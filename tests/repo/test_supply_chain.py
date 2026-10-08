"""What the published images promise.

These read the workflows rather than the registry: the assertions are about
what will be published next time, which is the part a change can break. That
both architectures actually arrived is checked by the release workflow
itself, against the registry, after it has pushed them.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
WORKFLOWS = REPO_ROOT / ".github" / "workflows"

#: Every image this project publishes. Named here so that adding a fourth
#: has to be a deliberate edit to a test about what gets built.
IMAGES = {"primer-api", "primer-worker", "primer-web"}

PLATFORMS = {"linux/amd64", "linux/arm64"}


def workflow(name: str) -> dict[str, Any]:
    return yaml.safe_load((WORKFLOWS / name).read_text())


def steps(job: dict[str, Any]) -> list[dict[str, Any]]:
    return job.get("steps") or []


def build_steps(job: dict[str, Any]) -> list[dict[str, Any]]:
    return [step for step in steps(job) if "build-push-action" in str(step.get("uses", ""))]


@pytest.fixture(params=["ci.yml", "release.yml"])
def images_job(request: pytest.FixtureRequest) -> dict[str, Any]:
    """The image-building job of each workflow that builds images."""
    return workflow(request.param)["jobs"]["images"]


def test_both_architectures_are_built(images_job: dict[str, Any]) -> None:
    """Apple Silicon and Graviton, natively, or they are not supported."""
    platforms = {entry["id"] for entry in images_job["strategy"]["matrix"]["platform"]}

    assert platforms == PLATFORMS


def test_each_architecture_is_built_on_its_own_runner(images_job: dict[str, Any]) -> None:
    """Not emulated.

    The worker image carries torch and Docling. Cross-building those under
    QEMU is slow enough that it would be dropped, and a cross-build that
    quietly falls back to compiling from source is worse than no image.
    """
    runners = {
        entry["id"]: entry["runner"] for entry in images_job["strategy"]["matrix"]["platform"]
    }

    assert "arm" in runners["linux/arm64"]
    assert "arm" not in runners["linux/amd64"]
    assert images_job["runs-on"] == "${{ matrix.platform.runner }}"


def test_every_image_is_built(images_job: dict[str, Any]) -> None:
    assert {entry["name"] for entry in images_job["strategy"]["matrix"]["image"]} == IMAGES


def test_a_published_build_carries_an_sbom_and_its_provenance(
    images_job: dict[str, Any],
) -> None:
    """What is in an image, answerable from the registry without pulling it."""
    published = [step for step in build_steps(images_job) if "push" in step["name"].lower()]
    assert published, "nothing in this job publishes an image"

    for step in published:
        assert step["with"]["sbom"] is True, step["name"]
        assert step["with"]["provenance"] == "mode=max", step["name"]


def test_nothing_is_tagged_before_both_architectures_exist(
    images_job: dict[str, Any],
) -> None:
    """Two builds push by digest; one later job names them together.

    A tag applied by the build itself would be claimed by whichever
    architecture finished last, and would serve that one alone.
    """
    for step in build_steps(images_job):
        assert "tags" not in step["with"], step["name"]
        if "outputs" in step["with"]:
            assert "push-by-digest=true" in step["with"]["outputs"]


@pytest.mark.parametrize("name", ["ci.yml", "release.yml"])
def test_the_manifest_refuses_a_half_built_tag(name: str) -> None:
    """Both digests, or the tag is not created at all."""
    manifest = workflow(name)["jobs"]["manifest"]
    script = " ".join(step.get("run", "") for step in steps(manifest))

    assert "images" in manifest["needs"]
    assert "wc -l" in script and "-eq 2" in script
    assert "imagetools create" in script


def test_a_release_verifies_what_it_published() -> None:
    """Against the registry, not against what the workflow believes it did."""
    manifest = workflow("release.yml")["jobs"]["manifest"]
    script = " ".join(step.get("run", "") for step in steps(manifest))

    assert "imagetools inspect" in script
    for architecture in ("amd64", "arm64"):
        assert architecture in script


def test_the_chart_is_published_after_the_images_are_named() -> None:
    """Its appVersion picks an image tag, so the tags have to exist first."""
    assert workflow("release.yml")["jobs"]["chart"]["needs"] == ["version", "manifest"]


def test_every_image_is_scanned_on_every_change() -> None:
    """Before it is published, so a fix happens while the change is being made."""
    scan = workflow("ci.yml")["jobs"]["scan"]

    assert {entry["name"] for entry in scan["strategy"]["matrix"]["include"]} == IMAGES

    trivy = next(step for step in steps(scan) if "trivy" in str(step.get("uses", "")))
    with_ = trivy["with"]
    # The written-down threshold. Changing it means changing
    # docs/operations/images.md, which is the point of asserting it here.
    assert with_["exit-code"] == 1
    assert set(with_["severity"].split(",")) == {"CRITICAL", "HIGH"}
    assert with_["ignore-unfixed"] is True


def test_the_scan_policy_is_written_down_where_it_can_be_disagreed_with() -> None:
    """A policy nobody agreed to is one that gets waived the first time it fires."""
    policy = (REPO_ROOT / "docs" / "operations" / "images.md").read_text()

    assert "CRITICAL" in policy and "HIGH" in policy
    assert ".trivyignore" in policy
    # Who may waive it, which is the half people forget to write.
    assert "pull request" in policy


def test_waivers_explain_themselves() -> None:
    """An entry with no reason and no expiry is a permanent silent exception."""
    waivers = (REPO_ROOT / ".trivyignore").read_text()

    assert "docs/operations/images.md" in waivers
    for line in waivers.splitlines():
        if line.strip() and not line.startswith("#"):
            pytest.fail(f"waiver without an explanation: {line}")
