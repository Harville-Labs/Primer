"""Where the rendered chart keeps things: uploads, and each database's disk.

docs/operations/storage.md is the operator's side of these, and
docs/adr/0016 says why uploads go only to `file://` or `s3://`.
"""

from __future__ import annotations

from typing import Any

import pytest
from test_chart import named, of_kind, refusal, render

ON_A_VOLUME = ("sourceStore.url=file:///var/lib/primer/sources", "sourceStore.volume.enabled=true")
#: Every optional workload that opens the store, so "nowhere else" is tested
#: against a render that has the most places the mount could leak into.
EVERYTHING = (
    "studio.enabled=true",
    "studio.retentionDays=30",
    "inference.images.baseUrl=http://comfy.example:8188",
    "inference.images.capabilities.api=comfyui",
    "inference.images.capabilities.sizes={1024x1024}",
    "lab.enabled=true",
    "postgresql.bundled.enabled=true",
    "graph.neo4j.enabled=true",
    "graph.existingSecret=primer-neo4j",
)
#: The workloads given the store's credentials, by name suffix: the mount
#: follows the same rule, so a volume reaches exactly what an s3:// key would.
OPENS_THE_STORE = {"-control", "-worker-parse", "-worker-index", "-studio-retention"}

DATABASES = [
    ("postgresql.bundled", "-postgresql"),
    ("vectorStore.qdrant", "-qdrant"),
    ("graph.neo4j", "-neo4j"),
]


def pod_spec(workload: dict[str, Any]) -> dict[str, Any]:
    spec = workload["spec"]
    if workload["kind"] == "CronJob":
        spec = spec["jobTemplate"]["spec"]
    return spec["template"]["spec"]


def pod_bearing(rendered: list[dict[str, Any]]) -> list[dict[str, Any]]:
    kinds = ("Deployment", "StatefulSet", "Job", "CronJob")
    return [doc for doc in rendered if doc.get("kind") in kinds]


def mounted_sources(workload: dict[str, Any]) -> tuple[set[str], set[str]]:
    """The claims behind a `sources` volume, and where any container mounts it."""
    spec = pod_spec(workload)
    claims = {
        volume["persistentVolumeClaim"]["claimName"]
        for volume in spec.get("volumes") or []
        if volume["name"] == "sources"
    }
    paths = {
        mount["mountPath"]
        for container in [*spec.get("initContainers", []), *spec["containers"]]
        for mount in container.get("volumeMounts") or []
        if mount["name"] == "sources"
    }
    return claims, paths


def test_a_directory_with_no_volume_is_refused() -> None:
    """Each pod would write uploads to its own disk, and lose them on restart."""
    message = refusal("sourceStore.url=file:///var/lib/primer/sources")

    assert "sourceStore.volume" in message


def test_a_relative_directory_is_refused() -> None:
    """`file://sources` names a host, not a directory, and mounts nowhere useful."""
    message = refusal("sourceStore.url=file://sources", "sourceStore.volume.enabled=true")

    assert "file:///" in message


def test_a_volume_an_object_store_would_not_use_is_refused() -> None:
    """Otherwise a claim is made and mounted that nothing ever writes to."""
    message = refusal("sourceStore.volume.enabled=true")

    assert "s3://primer-sources" in message


def test_the_same_claim_is_mounted_where_uploads_are_opened_and_nowhere_else() -> None:
    rendered = render(*ON_A_VOLUME, *EVERYTHING)
    workloads = pod_bearing(rendered)
    assert len(workloads) > len(OPENS_THE_STORE)

    found = {}
    for workload in workloads:
        claims, paths = mounted_sources(workload)
        if claims or paths:
            found[workload["metadata"]["name"]] = (claims, paths)

    assert {name.removeprefix("primer-primer") for name in found} == OPENS_THE_STORE
    for claims, paths in found.values():
        assert claims == {"primer-primer-sources"}
        assert paths == {"/var/lib/primer/sources"}


def test_the_chart_makes_the_source_claim_and_keeps_it_on_uninstall() -> None:
    """Uninstalling a release should not delete everyone's documents."""
    claim = named(render(*ON_A_VOLUME), "PersistentVolumeClaim", "-sources")

    assert claim["metadata"]["annotations"]["helm.sh/resource-policy"] == "keep"
    assert claim["spec"]["accessModes"] == ["ReadWriteMany"]


def test_a_source_claim_you_made_is_mounted_instead_of_one_made_here() -> None:
    rendered = render(*ON_A_VOLUME, "sourceStore.volume.existingClaim=nas-primer")

    assert not [
        doc
        for doc in of_kind(rendered, "PersistentVolumeClaim")
        if "sources" in doc["metadata"]["name"]
    ]
    claims, _ = mounted_sources(named(rendered, "Deployment", "-control"))
    assert claims == {"nas-primer"}


def test_an_object_store_mounts_nothing() -> None:
    rendered = render()

    assert not [doc for doc in rendered if doc["metadata"]["name"].endswith("-sources")]
    assert all(mounted_sources(workload) == (set(), set()) for workload in pod_bearing(rendered))


@pytest.mark.parametrize(("prefix", "suffix"), DATABASES)
def test_a_database_claim_you_made_replaces_the_claim_template(prefix: str, suffix: str) -> None:
    """A StatefulSet cannot adopt a claim through its template, so the
    template goes and the claim is an ordinary volume under the same name."""
    rendered = render(*EVERYTHING, f"{prefix}.persistence.existingClaim=restored")
    statefulset = named(rendered, "StatefulSet", suffix)

    assert "volumeClaimTemplates" not in statefulset["spec"]
    volumes = {volume["name"]: volume for volume in pod_spec(statefulset)["volumes"]}
    assert volumes["data"]["persistentVolumeClaim"]["claimName"] == "restored"


@pytest.mark.parametrize(("prefix", "suffix"), DATABASES)
def test_a_database_claim_has_the_access_mode_asked_for(prefix: str, suffix: str) -> None:
    rendered = render(*EVERYTHING, f"{prefix}.persistence.accessMode=ReadWriteOncePod")
    template = named(rendered, "StatefulSet", suffix)["spec"]["volumeClaimTemplates"][0]

    assert template["metadata"]["name"] == "data"
    assert template["spec"]["accessModes"] == ["ReadWriteOncePod"]


def test_a_lab_claim_you_made_replaces_the_one_made_here() -> None:
    rendered = render("lab.enabled=true", "lab.persistence.existingClaim=models")

    assert not [
        doc
        for doc in of_kind(rendered, "PersistentVolumeClaim")
        if "lab" in doc["metadata"]["name"]
    ]
    volumes = pod_spec(named(rendered, "Deployment", "-lab"))["volumes"]
    assert "models" in {
        volume["persistentVolumeClaim"]["claimName"]
        for volume in volumes
        if "persistentVolumeClaim" in volume
    }
