"""Which Compose service holds which of Primer's internal credentials.

Kept apart from `test_config.py`, which is already past the size a file
should be.
"""

from __future__ import annotations

import shutil

import pytest
from test_config import BASE, RESOLVE_ENV, WORKERS, resolve


@pytest.fixture(autouse=True)
def needs_docker() -> None:
    if shutil.which("docker") is None:
        pytest.skip("docker is not installed")


def test_the_workers_hold_only_the_worker_credential() -> None:
    """A worker parses whatever is uploaded, so it is the likeliest thing compromised.

    With Chat's token it could ask Control what any user may read; with the
    worker token it reaches only the job API and Retrieval's writes.
    """
    services = resolve(BASE)["services"]
    for name in WORKERS:
        environment = services[name]["environment"]
        assert environment["PRIMER_SERVICE_TOKEN"] == RESOLVE_ENV["PRIMER_WORKER_TOKEN"], name
        assert "PRIMER_INTERNAL_API_TOKEN" not in environment, name
    assert (
        services["chat"]["environment"]["PRIMER_SERVICE_TOKEN"]
        == (RESOLVE_ENV["PRIMER_INTERNAL_API_TOKEN"])
    )
    for name in ("control", "retrieval"):
        environment = services[name]["environment"]
        assert environment["PRIMER_WORKER_TOKEN"] == RESOLVE_ENV["PRIMER_WORKER_TOKEN"], name


def test_the_workers_hold_no_database_credential() -> None:
    """They reach jobs through Control's API, so the database is not theirs to open."""
    services = resolve(BASE)["services"]
    for name in WORKERS:
        environment = services[name]["environment"]
        assert "PRIMER_DATABASE_URL" not in environment, name
        assert environment["PRIMER_SOURCE_STORE_URL"].startswith("file://"), name
