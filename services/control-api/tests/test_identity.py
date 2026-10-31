"""The identity boundary: edge-trusted headers in, internal principal out."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from primer_control.config import Settings
from primer_service.identity import LOCAL_SUBJECT
from pydantic import ValidationError


def test_disabled_auth_ignores_spoofed_header(disabled_client: TestClient) -> None:
    response = disabled_client.get("/api/v1/me", headers={"X-Auth-Request-User": "attacker"})
    assert response.status_code == 200
    assert response.json()["subject"] == LOCAL_SUBJECT


def test_disabled_auth_is_stable_across_requests(disabled_client: TestClient) -> None:
    first = disabled_client.get("/api/v1/me").json()
    second = disabled_client.get("/api/v1/me").json()
    assert first["user_id"] == second["user_id"]


def test_disabled_auth_ignores_spoofed_groups(disabled_client: TestClient) -> None:
    response = disabled_client.get(
        "/api/v1/me", headers={"X-Auth-Request-Groups": "admins,operators"}
    )
    assert response.json()["groups"] == []


def test_oidc_mode_requires_trusted_subject(oidc_client: TestClient) -> None:
    response = oidc_client.get("/api/v1/me")
    assert response.status_code == 401
    assert response.json()["code"] == "identity_missing"


def test_oidc_mode_rejects_a_blank_subject(oidc_client: TestClient) -> None:
    response = oidc_client.get("/api/v1/me", headers={"X-Forwarded-User": "   "})
    assert response.status_code == 401
    assert response.json()["code"] == "identity_missing"


def test_oidc_mode_maps_the_edge_subject(oidc_client: TestClient) -> None:
    response = oidc_client.get(
        "/api/v1/me",
        headers={
            "X-Forwarded-User": "oidc-subject-1",
            "X-Forwarded-Email": "researcher@example.edu",
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["subject"] == "oidc-subject-1"
    assert body["email"] == "researcher@example.edu"


def test_oidc_user_ids_are_stable_per_subject_and_distinct_across_subjects(
    oidc_client: TestClient,
) -> None:
    def user_id(subject: str) -> str:
        return oidc_client.get("/api/v1/me", headers={"X-Forwarded-User": subject}).json()[
            "user_id"
        ]

    assert user_id("subject-a") == user_id("subject-a")
    assert user_id("subject-a") != user_id("subject-b")


def test_oidc_mode_splits_groups_on_the_configured_delimiter(oidc_client: TestClient) -> None:
    response = oidc_client.get(
        "/api/v1/me",
        headers={
            "X-Forwarded-User": "oidc-subject-1",
            "X-Forwarded-Groups": " researchers , students ,, ",
        },
    )
    assert response.json()["groups"] == ["researchers", "students"]


def test_oidc_mode_rejects_an_unconfigured_subject_header() -> None:
    with pytest.raises(ValidationError):
        Settings(auth_mode="oidc", subject_header="")
