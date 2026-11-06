"""Health, readiness, error shape, and correlation behavior."""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient
from primer_control.app import create_app
from primer_control.config import Settings
from primer_control.health import DependencyRegistry
from primer_service.db import Database


class ReachableDatabase(Database):
    """A database whose connectivity probe succeeds without a server."""

    async def check(self) -> bool:
        return True


def test_liveness_needs_no_identity(oidc_client: TestClient) -> None:
    assert oidc_client.get("/health/live").status_code == 200


def reachable(tmp_path: Path) -> Settings:
    """A database that answers, and a source store Control can write."""
    return Settings(auth_mode="disabled", source_store_url=f"file://{tmp_path}")


def test_readiness_passes_when_every_dependency_is_reachable(tmp_path: Path) -> None:
    settings = reachable(tmp_path)
    app = create_app(settings, database=ReachableDatabase(settings.database_url))
    with TestClient(app) as client:
        response = client.get("/health/ready")
    assert response.status_code == 200
    assert response.json() == {"status": "ready", "checks": {"database": True, "sources": True}}


def test_readiness_reports_a_source_store_control_cannot_write(tmp_path: Path) -> None:
    """A read-only or unmounted volume passes every check that only looks.
    Control would take traffic and fail each upload; this keeps it out of
    the Service instead, and says which dependency is why."""
    (tmp_path / "sources").write_text("a file where the store's directory should be")
    settings = Settings(auth_mode="disabled", source_store_url=f"file://{tmp_path}/sources")
    app = create_app(settings, database=ReachableDatabase(settings.database_url))
    with TestClient(app) as client:
        response = client.get("/health/ready")
    assert response.status_code == 503
    assert response.json()["checks"] == {"database": True, "sources": False}


def test_readiness_reports_an_unreachable_database(disabled_client: TestClient) -> None:
    """The fixture points at a database that is not running."""
    response = disabled_client.get("/health/ready")
    assert response.status_code == 503
    assert response.json()["checks"]["database"] is False


def test_a_probe_that_raises_counts_as_unready(tmp_path: Path) -> None:
    def explode() -> bool:
        raise RuntimeError("probe blew up")

    registry = DependencyRegistry()
    registry.register("storage", explode)
    settings = reachable(tmp_path)
    app = create_app(
        settings, dependencies=registry, database=ReachableDatabase(settings.database_url)
    )
    with TestClient(app) as client:
        response = client.get("/health/ready")
    assert response.status_code == 503
    assert response.json()["checks"] == {"storage": False, "database": True, "sources": True}


def test_errors_use_the_problem_detail_contract(oidc_client: TestClient) -> None:
    body = oidc_client.get("/api/v1/me").json()
    assert set(body) >= {"code", "title", "status", "request_id"}
    assert body["status"] == 401


def test_responses_echo_a_client_request_id(disabled_client: TestClient) -> None:
    response = disabled_client.get("/api/v1/me", headers={"X-Request-ID": "abc-123"})
    assert response.headers["x-request-id"] == "abc-123"


def test_responses_generate_a_request_id_when_absent(disabled_client: TestClient) -> None:
    assert disabled_client.get("/api/v1/me").headers["x-request-id"]


def test_openapi_documents_the_identity_and_health_routes(disabled_client: TestClient) -> None:
    paths = disabled_client.get("/openapi.json").json()["paths"]
    assert {"/api/v1/me", "/health/live", "/health/ready"} <= set(paths)
