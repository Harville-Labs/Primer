"""The vector store in the rendered chart: one Qdrant, run here or named.

Kept apart from `test_chart.py` for the same reason the graph is: these
assertions are about where one dependency lives and who may reach it.
"""

from __future__ import annotations

from typing import Any

from test_chart import containers, env_of, named, of_kind, refusal, render, workloads

OWN = ("vectorStore.url=https://qdrant.vectors:6333", "vectorStore.qdrant.enabled=false")
KEYED = ("vectorStore.existingSecret=primer-qdrant",)


def admitted(policy: dict[str, Any]) -> set[str]:
    return {
        peer["podSelector"]["matchLabels"]["app.kubernetes.io/component"]
        for rule in policy["spec"]["ingress"]
        for peer in rule["from"]
    }


def test_by_default_this_release_runs_qdrant_and_retrieval_uses_it() -> None:
    """So an install with no vector store settings comes up searchable."""
    rendered = render()

    assert named(rendered, "StatefulSet", "-qdrant")
    service = named(rendered, "Service", "-qdrant")
    assert [port["port"] for port in service["spec"]["ports"]] == [6333]
    retrieval = env_of(named(rendered, "Deployment", "-retrieval"))
    assert retrieval["PRIMER_QDRANT_URL"] == "http://primer-primer-qdrant:6333"


def test_the_bundled_qdrant_reports_nothing_upstream() -> None:
    """Qdrant sends usage telemetry unless told not to."""
    qdrant = containers(named(render(), "StatefulSet", "-qdrant"))[0]
    env = {entry["name"]: entry.get("value") for entry in qdrant["env"]}

    assert env["QDRANT__TELEMETRY_DISABLED"] == "true"


def test_the_bundled_qdrant_runs_hardened_with_somewhere_to_write() -> None:
    """A read-only root is only workable if every path Qdrant writes is mounted."""
    statefulset = named(render(), "StatefulSet", "-qdrant")
    qdrant = containers(statefulset)[0]

    assert qdrant["securityContext"]["readOnlyRootFilesystem"] is True
    assert statefulset["spec"]["template"]["spec"]["automountServiceAccountToken"] is False
    mounts = {mount["mountPath"] for mount in qdrant["volumeMounts"]}
    assert mounts == {"/qdrant/storage", "/qdrant/snapshots", "/tmp"}  # noqa: S108
    env = {entry["name"]: entry.get("value") for entry in qdrant["env"]}
    assert env["QDRANT_INIT_FILE_PATH"].startswith("/tmp/")  # noqa: S108


def test_your_own_qdrant_replaces_the_bundled_one() -> None:
    rendered = render(*OWN)

    assert not [doc for doc in rendered if "qdrant" in doc["metadata"]["name"]]
    retrieval = env_of(named(rendered, "Deployment", "-retrieval"))
    assert retrieval["PRIMER_QDRANT_URL"] == "https://qdrant.vectors:6333"


def test_a_url_and_a_bundled_qdrant_together_are_refused() -> None:
    """Either choice would leave the other unused, usually the one meant."""
    message = refusal("vectorStore.url=https://qdrant.vectors:6333")
    assert "vectorStore.url and vectorStore.qdrant.enabled are both set" in message


def test_no_vector_store_at_all_is_refused() -> None:
    """Retrieval would start and fail its first search instead."""
    message = refusal("vectorStore.qdrant.enabled=false")
    assert "vectorStore.url is required" in message


def test_a_url_that_is_not_http_is_refused() -> None:
    message = refusal("vectorStore.url=qdrant.vectors:6333", "vectorStore.qdrant.enabled=false")
    assert "/vectorStore/url" in message


def test_the_api_key_comes_from_one_secret_for_server_and_client() -> None:
    """So the key Qdrant requires and the key Retrieval sends cannot disagree."""
    rendered = render(*KEYED)
    retrieval = {
        e["name"]: e for e in containers(named(rendered, "Deployment", "-retrieval"))[0]["env"]
    }
    qdrant = {e["name"]: e for e in containers(named(rendered, "StatefulSet", "-qdrant"))[0]["env"]}

    expected = {"name": "primer-qdrant", "key": "qdrant-api-key"}
    assert retrieval["PRIMER_QDRANT_API_KEY"]["valueFrom"]["secretKeyRef"] == expected
    assert qdrant["QDRANT__SERVICE__API_KEY"]["valueFrom"]["secretKeyRef"] == expected


def test_without_a_secret_no_api_key_is_set() -> None:
    rendered = render()
    for name, workload in workloads(rendered).items():
        for container in containers(workload):
            names = {entry["name"] for entry in container.get("env") or []}
            assert not names & {"PRIMER_QDRANT_API_KEY", "QDRANT__SERVICE__API_KEY"}, name


def test_only_retrieval_may_reach_qdrant() -> None:
    """Chat, Control, the workers and the web app go through Retrieval."""
    policy = named(render(), "NetworkPolicy", "-allow-qdrant")

    assert admitted(policy) == {"retrieval"}
    assert [port["port"] for rule in policy["spec"]["ingress"] for port in rule["ports"]] == [6333]


def test_retrieval_is_not_given_the_database() -> None:
    """Retrieval keeps nothing in PostgreSQL; credentials it never uses only
    widen what a compromise of it reaches."""
    for values in [(), ("postgresql.bundled.enabled=true",)]:
        rendered = render(*values)
        retrieval = named(rendered, "Deployment", "-retrieval")
        names = {entry["name"] for entry in containers(retrieval)[0]["env"]}
        assert not names & {"PRIMER_DATABASE_URL", "PGPASSWORD"}, values
        policies = [
            p
            for p in of_kind(rendered, "NetworkPolicy")
            if p["metadata"]["name"].endswith("-allow-postgresql")
        ]
        for policy in policies:
            assert "retrieval" not in admitted(policy)


def test_retrieval_is_told_how_to_lay_out_the_collection() -> None:
    """Exact vectors in memory unless a library too large for that asks."""
    assert env_of(named(render(), "Deployment", "-retrieval"))["PRIMER_QDRANT_LAYOUT"] == "memory"

    disk = env_of(named(render("vectorStore.layout=disk"), "Deployment", "-retrieval"))
    assert disk["PRIMER_QDRANT_LAYOUT"] == "disk"
