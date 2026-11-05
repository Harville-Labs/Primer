"""What the published images promise.

These read the workflows rather than the registry: the assertions are about
what will be published next time, which is the part a change can break. That
every architecture actually arrived is checked by the release workflow
itself, against the registry, after it has named them.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
WORKFLOWS = REPO_ROOT / ".github" / "workflows"

#: Every build CI publishes, as image and tag suffix. Named here so that
#: adding one has to be a deliberate edit to a test about what gets built.
BUILDS = {
    ("primer-api", ""),
    ("primer-worker", ""),
    ("primer-web", ""),
    ("primer-lab", ""),
    ("primer-lab", "-cuda"),
    ("primer-lab", "-graphs"),
}


def workflow(name: str) -> dict[str, Any]:
    return yaml.safe_load((WORKFLOWS / name).read_text())


def ci_jobs() -> dict[str, Any]:
    return workflow("ci.yml")["jobs"]


def steps(job: dict[str, Any]) -> list[dict[str, Any]]:
    steps = job.get("steps") or []
    assert steps, "this job has no steps, so nothing here checks it"
    return steps


def step_using(job: dict[str, Any], action: str) -> dict[str, Any]:
    return next(step for step in steps(job) if action in str(step.get("uses", "")))


def scripts(job: dict[str, Any]) -> str:
    return "\n".join(step.get("run", "") for step in steps(job))


def test_a_pull_request_builds_no_image() -> None:
    """The arm64 builds cost more than every other check, and could not be published."""
    condition = ci_jobs()["images"]["if"]

    assert "refs/heads/main" in condition
    assert "github.event_name == 'push'" in condition


def test_both_architectures_are_built_on_runners_of_their_own() -> None:
    """Not emulated: torch and Docling under QEMU are too slow to keep."""
    runners = {
        entry["platform"]: entry["runner"]
        for entry in ci_jobs()["images"]["strategy"]["matrix"]["include"]
        if "runner" in entry
    }

    assert set(runners) == {"amd64", "arm64"}
    assert "arm" in runners["arm64"]
    assert "arm" not in runners["amd64"]
    assert ci_jobs()["images"]["runs-on"] == "${{ matrix.runner }}"


def test_every_build_is_published_under_its_own_tags() -> None:
    manifest = ci_jobs()["manifest"]["strategy"]["matrix"]["include"]

    assert {(entry["image"], entry.get("suffix", "")) for entry in manifest} == BUILDS


def test_a_build_carries_its_sbom_and_provenance_and_no_tag() -> None:
    """Pushed by digest, so a tag cannot be claimed by one architecture alone."""
    build = step_using(ci_jobs()["images"], "build-push-action")["with"]

    assert build["sbom"] is True
    assert build["provenance"] == "mode=max"
    assert "tags" not in build
    assert "push-by-digest=true" in build["outputs"]


def test_nothing_is_tagged_unless_every_build_succeeded() -> None:
    """`needs` holds the manifest back until every architecture of every image exists."""
    manifest = ci_jobs()["manifest"]

    assert manifest["needs"] == "images"
    assert "imagetools create" in scripts(manifest)


def test_an_image_is_scanned_before_it_is_tagged() -> None:
    """A scan after tagging would leave `latest` on an image the policy refused."""
    manifest = ci_jobs()["manifest"]
    order = [str(step.get("uses", "")) + step.get("run", "") for step in steps(manifest)]
    scan = next(i for i, step in enumerate(order) if "trivy-action" in step)
    tag = next(i for i, step in enumerate(order) if "imagetools create" in step)

    assert scan < tag


def test_the_scan_threshold_is_the_one_written_down() -> None:
    """Changing it means changing docs/operations/images.md, which is the point."""
    trivy = step_using(ci_jobs()["manifest"], "trivy-action")["with"]

    assert trivy["exit-code"] == 1
    assert set(trivy["severity"].split(",")) == {"CRITICAL", "HIGH"}
    assert trivy["ignore-unfixed"] is True
    assert trivy["trivyignores"] == ".trivyignore"

    policy = (REPO_ROOT / "docs" / "operations" / "images.md").read_text()
    assert "`CRITICAL` or `HIGH`" in policy
    assert ".trivyignore" in policy


def test_the_scan_can_read_its_waivers() -> None:
    """Trivy reads .trivyignore from the workspace, which is empty without a checkout."""
    step_using(ci_jobs()["manifest"], "actions/checkout")


def test_a_release_names_ci_builds_rather_than_building_its_own() -> None:
    """A second build would ship images nobody scanned."""
    images = workflow("release.yml")["jobs"]["images"]
    script = scripts(images)

    assert not any("build-push-action" in str(step.get("uses", "")) for step in steps(images))
    assert ":sha-${COMMIT}${SUFFIX}" in script
    assert "imagetools create" in script


def test_a_release_names_every_build_ci_publishes() -> None:
    builds = workflow("release.yml")["jobs"]["images"]["strategy"]["matrix"]["include"]

    assert {(entry["image"], entry.get("suffix", "")) for entry in builds} == BUILDS


def test_a_release_checks_every_architecture_in_the_registry() -> None:
    """Against the registry, not against what the workflow believes it did."""
    images = workflow("release.yml")["jobs"]["images"]
    script = scripts(images)
    platforms = {
        (entry["image"], entry.get("suffix", "")): set(entry["platforms"].split())
        for entry in images["strategy"]["matrix"]["include"]
    }

    assert "imagetools inspect" in script
    assert 'grep -q "linux/${platform}"' in script
    assert platforms[("primer-api", "")] == {"amd64", "arm64"}
    assert platforms[("primer-lab", "-cuda")] == {"amd64"}


def test_the_chart_is_published_after_the_images_are_named() -> None:
    """Its appVersion picks an image tag, so the tags have to exist first."""
    assert workflow("release.yml")["jobs"]["chart"]["needs"] == ["version", "images"]


def test_a_waiver_says_why_and_when_it_ends() -> None:
    """An entry with no reason and no expiry is a permanent, silent exception.

    The header is the first block and holds no waivers; each waiver after it
    is a block of its own, a comment saying why, then the entry. Checking
    only for a comment line above would accept an entry appended under the
    header's own text.
    """
    text = (REPO_ROOT / ".trivyignore").read_text()
    header, *waivers = [block.splitlines() for block in text.split("\n\n") if block.strip()]

    assert any("docs/operations/images.md" in line for line in header)
    assert all(line.startswith("#") for line in header), "a waiver inside the header"
    for block in waivers:
        reason = [line for line in block if line.startswith("#")]
        entries = [line for line in block if line.strip() and not line.startswith("#")]
        assert reason, f"waiver without a reason: {entries}"
        assert entries and all("exp:" in entry for entry in entries), (
            f"waiver without an expiry: {block}"
        )
