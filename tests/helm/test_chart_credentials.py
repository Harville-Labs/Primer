"""Which workload holds which of the release's internal credentials.

Each credential admits its holder to different routes, so the set a pod is
given is the set of things anyone who compromises that pod can do. The
ingestion workers parse whatever users upload, which makes them the likeliest
pod to be compromised and the one that must hold the least.
"""

from __future__ import annotations

from typing import Any

import pytest
from test_chart import containers, render, workloads


def _is_store_password(key: str) -> bool:
    """A datastore's login, which test_chart_database covers, rather than a token."""
    return key == "graph-password" or (key.startswith("db-") and key.endswith("-password"))


def internal_credentials(workload: dict[str, Any]) -> dict[str, str]:
    """Variable name to key, for every variable read from the internal Secret."""
    found: dict[str, str] = {}
    for container in containers(workload):
        for entry in container.get("env") or []:
            ref = (entry.get("valueFrom") or {}).get("secretKeyRef") or {}
            key = ref.get("key", "")
            if ref.get("name", "").endswith("-internal") and not _is_store_password(key):
                found[entry["name"]] = ref["key"]
    return found


EXPECTED = {
    "-control": {
        "PRIMER_INTERNAL_API_TOKEN": "internal-token",
        "PRIMER_WORKER_TOKEN": "worker-token",
        "PRIMER_WEB_TOKEN": "web-token",
    },
    "-retrieval": {
        "PRIMER_INTERNAL_API_TOKEN": "internal-token",
        "PRIMER_WORKER_TOKEN": "worker-token",
    },
    "-chat": {
        "PRIMER_SERVICE_TOKEN": "internal-token",
        "PRIMER_WEB_TOKEN": "web-token",
        "PRIMER_SETTINGS_ENCRYPTION_KEY": "settings-key",
    },
    "-worker-parse": {"PRIMER_SERVICE_TOKEN": "worker-token"},
    "-worker-index": {"PRIMER_SERVICE_TOKEN": "worker-token"},
    "-web": {"PRIMER_WEB_TOKEN": "web-token"},
}


@pytest.mark.parametrize(("suffix", "expected"), EXPECTED.items())
def test_each_workload_holds_only_the_credentials_it_presents_or_checks(
    suffix: str, expected: dict[str, str]
) -> None:
    """A worker holding Chat's token could ask Control what any user may read."""
    matching = [
        w
        for name, w in workloads(render()).items()
        if name.endswith(suffix) and w["kind"] == "Deployment"
    ]
    assert len(matching) == 1, suffix

    assert internal_credentials(matching[0]) == expected


def test_migrations_hold_no_internal_credential() -> None:
    """They change the schema and call nothing."""
    jobs = [w for name, w in workloads(render()).items() if "migrat" in name]
    assert jobs

    for job in jobs:
        assert internal_credentials(job) == {}


def test_every_credential_a_workload_reads_is_generated() -> None:
    """A key named in a template and missing from the Secret fails only at pod start."""
    rendered = render()
    secret = next(
        doc
        for doc in rendered
        if doc.get("kind") == "Secret" and doc["metadata"]["name"].endswith("-internal")
    )
    read = {key for w in workloads(rendered).values() for key in internal_credentials(w).values()}
    assert read

    assert read <= set(secret["data"])
