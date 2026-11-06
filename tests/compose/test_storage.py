"""Where the Compose stack keeps uploads and each database's files.

Kept apart from `test_config.py`, which is about hardening and wiring;
these are about what an operator can point at their own disks.
"""

from __future__ import annotations

import shutil
from typing import Any

import pytest
from test_config import BASE, resolve

#: The services that open the source store, and so the only ones given the
#: object store's credentials or the uploads directory.
OPENS_THE_STORE = {"control", "worker-parse", "worker-index"}
#: init-volumes also mounts the directory, to give it to the services' user.
MOUNTS_THE_DIRECTORY = {*OPENS_THE_STORE, "init-volumes"}

DIRECTORIES = {
    "PRIMER_SOURCE_DIR": "/mnt/nas/primer/sources",
    "PRIMER_POSTGRES_DIR": "/srv/primer/postgres",
    "PRIMER_QDRANT_DIR": "/srv/primer/qdrant",
    "PRIMER_GRAPH_DIR": "/srv/primer/graph",
}
OBJECT_STORE = {
    "PRIMER_SOURCE_STORE_URL": "s3://primer-sources",
    "AWS_ACCESS_KEY_ID": "resolve-only",
    "FSSPEC_S3_ENDPOINT_URL": "http://minio.example.internal:9000",
}


@pytest.fixture(autouse=True)
def needs_docker() -> None:
    if shutil.which("docker") is None:
        pytest.skip("docker is not installed")


def binds(config: dict[str, Any]) -> dict[str, set[tuple[str, str]]]:
    found: dict[str, set[tuple[str, str]]] = {}
    for name, service in config["services"].items():
        for volume in service.get("volumes", []):
            if volume.get("type") == "bind":
                found.setdefault(name, set()).add((volume["source"], volume["target"]))
    return found


def test_each_directory_is_bound_where_its_volume_was() -> None:
    config = resolve(BASE, env=DIRECTORIES)
    found = binds(config)

    sources = ("/mnt/nas/primer/sources", "/var/lib/primer/sources")
    assert {name for name, mounts in found.items() if sources in mounts} == MOUNTS_THE_DIRECTORY
    assert found["postgres"] == {("/srv/primer/postgres", "/var/lib/postgresql/data")}
    assert found["qdrant"] == {("/srv/primer/qdrant", "/qdrant/storage")}
    assert found["neo4j"] == {("/srv/primer/graph", "/data")}


def test_an_object_store_is_given_only_to_the_services_that_open_it() -> None:
    """Chat and Retrieval never read uploads, so they hold no key to them."""
    services = resolve(BASE, env=OBJECT_STORE)["services"]

    holding = {
        name
        for name, service in services.items()
        if "AWS_ACCESS_KEY_ID" in (service.get("environment") or {})
    }
    assert holding == OPENS_THE_STORE
    for name in OPENS_THE_STORE:
        environment = services[name]["environment"]
        assert environment["PRIMER_SOURCE_STORE_URL"] == "s3://primer-sources"
        assert environment["FSSPEC_S3_ENDPOINT_URL"] == "http://minio.example.internal:9000"


def test_an_unset_object_store_setting_is_not_passed_as_empty() -> None:
    """An empty endpoint is still a URL, and s3fs would try to reach it."""
    control = resolve(BASE)["services"]["control"]["environment"]

    assert control["PRIMER_SOURCE_STORE_URL"] == "file:///var/lib/primer/sources"
    assert control.get("FSSPEC_S3_ENDPOINT_URL") is None
