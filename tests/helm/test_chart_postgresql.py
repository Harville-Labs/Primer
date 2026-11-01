"""A PostgreSQL run by the release, beside the operator's own.

Kept apart from `test_chart.py` because the default render has no bundled
PostgreSQL in it, so the cross-cutting tests there would pass for one that
broke every rule they check.
"""

from __future__ import annotations

from typing import Any

import pytest
from test_chart import containers, named, of_kind, refusal, render, workloads

BUNDLED = ("postgresql.bundled.enabled=true",)
DATABASE_URL = "postgresql://primer@primer-primer-postgresql:5432/primer"


def database_env(workload: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        entry["name"]: entry
        for container in containers(workload)
        for entry in container.get("env") or []
        if entry["name"] in ("PRIMER_DATABASE_URL", "PGPASSWORD")
    }


def components(workload: dict[str, Any]) -> str:
    return workload["spec"]["template"]["metadata"]["labels"]["app.kubernetes.io/component"]


def test_by_default_no_postgresql_is_run() -> None:
    rendered = render()

    assert not [doc for doc in rendered if "postgresql" in doc["metadata"]["name"]]
    for name, workload in workloads(rendered).items():
        assert "PGPASSWORD" not in database_env(workload), name


def test_every_database_user_is_pointed_at_the_bundled_server() -> None:
    """With no password in the URI: PGPASSWORD carries it, from the Secret."""
    rendered = render(*BUNDLED)
    users = {name: database_env(w) for name, w in workloads(rendered).items()}
    users = {name: env for name, env in users.items() if env}

    assert len(users) >= 6, "services, workers and the two SQL migrations"
    for name, env in users.items():
        assert env["PRIMER_DATABASE_URL"]["value"] == DATABASE_URL, name
        reference = env["PGPASSWORD"]["valueFrom"]["secretKeyRef"]
        assert reference == {"name": "primer-postgres", "key": "postgres-password"}, name
        assert "value" not in env["PGPASSWORD"]


def test_the_bundled_server_takes_its_password_from_the_same_secret() -> None:
    """So the server and every client cannot disagree about it."""
    postgresql = containers(named(render(*BUNDLED), "StatefulSet", "-postgresql"))[0]
    env = {entry["name"]: entry for entry in postgresql["env"]}

    reference = env["POSTGRES_PASSWORD"]["valueFrom"]["secretKeyRef"]
    assert reference == {"name": "primer-postgres", "key": "postgres-password"}
    assert env["POSTGRES_USER"]["value"] == env["POSTGRES_DB"]["value"] == "primer"
    assert env["PGDATA"]["value"].startswith("/var/lib/postgresql/data/")


def test_a_bundled_postgresql_still_needs_a_secret() -> None:
    message = refusal(*BUNDLED, "postgresql.existingSecret=")
    assert "postgresql.existingSecret is required" in message
    assert "postgresql.bundled.enabled" in message


def test_sql_migrations_run_after_install_and_before_upgrade() -> None:
    """The server does not exist before install; afterwards, the schema leads the code."""
    jobs = of_kind(render(*BUNDLED), "Job")

    assert len(jobs) == 2
    for job in jobs:
        assert job["metadata"]["annotations"]["helm.sh/hook"] == "post-install,pre-upgrade"
        (wait,) = job["spec"]["template"]["spec"]["initContainers"]
        assert "pg_isready -h primer-primer-postgresql" in wait["command"][-1]
        assert "timeout 300" in wait["command"][-1]


@pytest.mark.parametrize("lab", [(), ("lab.enabled=true",)], ids=["without-lab", "with-lab"])
def test_only_the_workloads_given_the_database_may_reach_it(lab: tuple[str, ...]) -> None:
    """The policy and the environment are written separately; they must agree."""
    rendered = render(*BUNDLED, *lab)
    policy = named(rendered, "NetworkPolicy", "-allow-postgresql")
    admitted = {
        peer["podSelector"]["matchLabels"]["app.kubernetes.io/component"]
        for rule in policy["spec"]["ingress"]
        for peer in rule["from"]
    }
    given = {
        components(workload)
        for workload in workloads(rendered).values()
        if "PRIMER_DATABASE_URL" in database_env(workload)
    }

    assert admitted == given
    assert {"web", "auth"}.isdisjoint(admitted)
    assert policy["spec"]["podSelector"]["matchLabels"]["app.kubernetes.io/component"] == (
        "postgresql"
    )
    assert [rule["ports"] for rule in policy["spec"]["ingress"]] == [
        [{"port": 5432, "protocol": "TCP"}]
    ]


def test_with_policies_off_a_bundled_postgresql_has_no_policy() -> None:
    rendered = render(*BUNDLED, "networkPolicies.enabled=false")

    assert of_kind(rendered, "NetworkPolicy") == []
    assert named(rendered, "StatefulSet", "-postgresql")


def test_a_bundled_postgresql_runs_under_the_charts_hardening() -> None:
    """Non-root, read-only root, no capabilities, its own account, and sized."""
    rendered = render(*BUNDLED)
    pod = named(rendered, "StatefulSet", "-postgresql")["spec"]["template"]["spec"]
    defaults = named(rendered, "Deployment", "-retrieval")["spec"]["template"]["spec"]
    accounts = {doc["metadata"]["name"] for doc in of_kind(rendered, "ServiceAccount")}
    others = {
        workload["spec"]["template"]["spec"]["serviceAccountName"]
        for name, workload in workloads(rendered).items()
        if not name.endswith("-postgresql")
    }

    assert pod["securityContext"] == defaults["securityContext"]
    assert pod["serviceAccountName"] in accounts - others
    assert pod["automountServiceAccountToken"] is False
    (container,) = pod["containers"]
    assert container["securityContext"]["readOnlyRootFilesystem"] is True
    assert container["securityContext"]["capabilities"] == {"drop": ["ALL"]}
    assert container["resources"]["requests"] and container["resources"]["limits"]
    mounts = {mount["mountPath"] for mount in container["volumeMounts"]}
    assert {"/var/lib/postgresql/data", "/var/run/postgresql", "/tmp"} <= mounts  # noqa: S108


def test_bundling_postgresql_leaves_the_graph_choice_alone() -> None:
    """The two bundles are independent: either, both, or neither."""
    rendered = render(*BUNDLED, "graph.neo4j.enabled=true", "graph.existingSecret=primer-neo4j")

    assert named(rendered, "StatefulSet", "-postgresql")
    assert named(rendered, "StatefulSet", "-neo4j")
