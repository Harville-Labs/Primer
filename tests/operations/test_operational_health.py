"""What health endpoints say, and what they must not say.

Named test_operational_health rather than test_health: pytest puts every
test directory on sys.path, so a second file called test_health.py shadows
the Control API's and breaks collection. That is the third time this repo
has hit it.

Readiness has to be specific enough for an operator to act on and vague
enough to hand to a load balancer that anyone can reach. The line between
those is that it names *which dependency* failed and never *why* in the
words of the underlying error - a connection failure message routinely
contains a host, a port, a username, and sometimes a password.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient
from primer_control.app import create_app as control_app
from primer_control.config import Settings as ControlSettings
from primer_retrieval.app import create_app as retrieval_app
from primer_retrieval.config import Settings as RetrievalSettings
from primer_service.db import Database

SECRET_URL = "postgresql://primer:hunter2@db.internal:5432/primer"  # noqa: S105 - a sentinel
#: Any non-empty value; the credential check itself is tested elsewhere.
TEST_TOKEN = "health-test"  # noqa: S105 - a fixture value


class UnreachableDatabase(Database):
    """A database that fails the way a real outage does."""

    async def check(self) -> bool:
        return False


class ReachableDatabase(Database):
    async def check(self) -> bool:
        return True


class UnreachableStore:
    def count_documents(self) -> int:
        raise ConnectionError(f"could not connect using {SECRET_URL}")


class ReachableStore:
    def count_documents_by_filter(self, filters: object) -> int:
        return 0


class Unused:
    """Stands in for a dependency health must never touch.

    Raises on contact rather than being a bare object, so a probe that
    started reaching for an embedder would fail loudly instead of passing.
    """

    def __getattr__(self, name: str) -> Any:
        def explode(*args: Any, **kwargs: Any) -> Any:
            raise AssertionError(f"health touched {name}")

        return explode


def control(database: Database) -> TestClient:
    return TestClient(control_app(ControlSettings(database_url=SECRET_URL), database=database))


def retrieval(store: Any) -> TestClient:
    return TestClient(
        retrieval_app(
            RetrievalSettings(qdrant_url=SECRET_URL, internal_api_token=TEST_TOKEN),
            store=store,
            document_embedder=Unused(),  # ty: ignore[invalid-argument-type]
            text_embedder=Unused(),  # ty: ignore[invalid-argument-type]
            retriever=Unused(),
        )
    )


def test_liveness_stays_healthy_while_a_dependency_is_down() -> None:
    """Restarting a pod because PostgreSQL blipped is when it helps least."""
    client = control(UnreachableDatabase(SECRET_URL))

    assert client.get("/health/live").status_code == 200


def test_readiness_reports_a_database_it_cannot_reach() -> None:
    """Unready takes the pod out of the load balancer without killing it."""
    client = control(UnreachableDatabase(SECRET_URL))

    response = client.get("/health/ready")

    assert response.status_code == 503
    # Named per dependency, so an operator knows which one to look at.
    assert response.json()["checks"]["database"] is False


def test_readiness_passes_when_the_database_answers() -> None:
    client = control(ReachableDatabase(SECRET_URL))

    assert client.get("/health/ready").status_code == 200


def test_readiness_names_the_dependency_without_quoting_the_error() -> None:
    """A connection error routinely contains a host, a user, and a password."""
    client = control(UnreachableDatabase(SECRET_URL))

    body = client.get("/health/ready").text

    assert "database" in body
    assert "hunter2" not in body
    assert "db.internal" not in body


def test_retrieval_liveness_does_not_touch_the_store() -> None:
    client = retrieval(UnreachableStore())

    assert client.get("/health/live").status_code == 200


def test_retrieval_readiness_reports_an_unreachable_store() -> None:
    client = retrieval(UnreachableStore())

    response = client.get("/health/ready")

    assert response.status_code == 503
    assert SECRET_URL not in response.text
    assert "hunter2" not in response.text


def test_retrieval_readiness_passes_when_the_store_answers() -> None:
    assert retrieval(ReachableStore()).get("/health/ready").status_code == 200


@pytest.mark.parametrize("path", ["/health/live", "/health/ready"])
def test_health_needs_no_credential(path: str) -> None:
    """An orchestrator sends no service token, and never will."""
    assert retrieval(ReachableStore()).get(path).status_code in (200, 503)
