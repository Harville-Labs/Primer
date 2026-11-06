"""Which PostgreSQL login each workload connects as.

Control, Chat and Lab share one PostgreSQL. A bundled one gives each its own
role, which the migrations confine to that service's schema, so a flaw in
one service that ran arbitrary SQL reaches that service's rows alone. The
owner's password is for the migrations, and nothing at runtime holds it.
"""

from __future__ import annotations

from typing import Any

import pytest
from test_chart import containers, render, workloads

BUNDLED = ("postgresql.bundled.enabled=true", "lab.enabled=true")
STUDIO = ("studio.enabled=true", "studio.retentionDays=30")


def env_of(workload: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {e["name"]: e for c in containers(workload) for e in c.get("env") or []}


def secret_key(entry: dict[str, Any]) -> tuple[str, str]:
    ref = entry["valueFrom"]["secretKeyRef"]
    return ref["name"], ref["key"]


def only(rendered: list[dict[str, Any]], suffix: str, kind: str) -> dict[str, Any]:
    matching = [
        w for name, w in workloads(rendered).items() if name.endswith(suffix) and w["kind"] == kind
    ]
    assert len(matching) == 1, suffix
    return matching[0]


@pytest.mark.parametrize(
    ("suffix", "kind", "role"),
    [
        ("-control", "Deployment", "control"),
        ("-chat", "Deployment", "chat"),
        ("-lab", "Deployment", "lab"),
        ("-studio-retention", "CronJob", "control"),
    ],
)
def test_each_service_connects_as_its_own_role(suffix: str, kind: str, role: str) -> None:
    rendered = render(*BUNDLED, *STUDIO)
    if kind == "CronJob":
        jobs = [d for d in rendered if d.get("kind") == "CronJob"]
        assert len(jobs) == 1
        env = {
            e["name"]: e
            for c in jobs[0]["spec"]["jobTemplate"]["spec"]["template"]["spec"]["containers"]
            for e in c.get("env") or []
        }
    else:
        env = env_of(only(rendered, suffix, kind))

    assert env["PRIMER_DATABASE_URL"]["value"].startswith(f"postgresql://primer_{role}@")
    assert secret_key(env["PGPASSWORD"]) == ("primer-primer-internal", f"db-{role}-password")


@pytest.mark.parametrize("schema", ["control", "chat", "lab"])
def test_migrations_run_as_the_owner_and_make_the_service_role(schema: str) -> None:
    """The Job that alters a schema is the only thing holding the owner's password."""
    env = env_of(only(render(*BUNDLED), f"-migrate-{schema}", "Job"))

    assert env["PRIMER_DATABASE_URL"]["value"].startswith("postgresql://primer@")
    assert secret_key(env["PGPASSWORD"]) == ("primer-postgres", "postgres-password")
    assert env["PRIMER_DATABASE_RUNTIME_ROLE"]["value"] == f"primer_{schema}"
    assert secret_key(env["PRIMER_DATABASE_RUNTIME_PASSWORD"]) == (
        "primer-primer-internal",
        f"db-{schema}-password",
    )


def test_nothing_but_the_migrations_holds_the_owner_password() -> None:
    rendered = render(*BUNDLED, *STUDIO)
    holders = [
        name
        for name, w in workloads(rendered).items()
        for e in env_of(w).values()
        if (e.get("valueFrom") or {}).get("secretKeyRef", {}).get("name") == "primer-postgres"
    ]
    assert holders
    assert all("-migrate-" in name or name.endswith("-postgresql") for name in holders), holders


def test_workers_hold_no_database_credential() -> None:
    """They reach jobs through Control's API, and parse whatever users upload."""
    rendered = render(*BUNDLED)
    for suffix in ("-worker-parse", "-worker-index"):
        env = env_of(only(rendered, suffix, "Deployment"))
        assert "PRIMER_DATABASE_URL" not in env
        assert "PGPASSWORD" not in env


def test_every_service_role_has_a_generated_password() -> None:
    secret = next(
        d
        for d in render(*BUNDLED)
        if d.get("kind") == "Secret" and d["metadata"]["name"].endswith("-internal")
    )
    assert {"db-control-password", "db-chat-password", "db-lab-password"} <= set(secret["data"])


def test_an_operator_database_is_one_uri_for_every_service() -> None:
    """The chart cannot build a URI for a role on a server it does not know."""
    rendered = render("lab.enabled=true")
    for suffix, kind in (
        ("-control", "Deployment"),
        ("-chat", "Deployment"),
        ("-migrate-chat", "Job"),
    ):
        env = env_of(only(rendered, suffix, kind))
        assert secret_key(env["PRIMER_DATABASE_URL"]) == ("primer-postgres", "database-url")
        assert "PRIMER_DATABASE_RUNTIME_ROLE" not in env
