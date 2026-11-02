"""The knowledge graph in the rendered chart: a bundled Neo4j by default, or one named.

Kept apart from `test_chart.py` because the graph is optional and its
assertions are about which workloads it reaches and which it must not.
"""

from __future__ import annotations

from typing import Any

from test_chart import containers, env_of, named, of_kind, refusal, render, workloads

GRAPH_OFF = ("graph.neo4j.enabled=false",)
GRAPH_ON = (*GRAPH_OFF, "graph.url=bolt://neo4j.graph:7687", "graph.existingSecret=primer-neo4j")
BUNDLED = ("graph.neo4j.enabled=true", "graph.existingSecret=primer-neo4j")
EXTRACTION_ON = (
    "graph.extraction.baseUrl=http://model/v1",
    "graph.extraction.model=extract-model",
)


def graph_variables(workload: dict[str, Any]) -> set[str]:
    return {
        entry["name"]
        for container in containers(workload)
        for entry in container.get("env") or []
        if entry["name"].startswith("PRIMER_GRAPH_")
    }


def test_with_the_graph_off_nothing_is_told_about_a_graph() -> None:
    """Turned off, a deployment must render as it did before the graph existed."""
    rendered = render(*GRAPH_OFF)
    found = workloads(rendered)
    assert found, "the chart rendered no workloads, so this test would check nothing"

    for name, workload in found.items():
        assert graph_variables(workload) == set(), f"{name} is told about a graph nobody set"
    assert not any(name.endswith("-migrate-graph") for name in found)
    assert not [doc for doc in rendered if "neo4j" in doc["metadata"]["name"]]


def test_by_default_a_release_runs_its_own_neo4j_with_a_password_it_made() -> None:
    """As it runs its own Qdrant: a graph is part of Primer, not an extra.

    Nobody named a Secret, so the password is the release's own, kept in the
    internal Secret both the server and its clients read.
    """
    rendered = render()
    internal = named(rendered, "Secret", "-internal")
    expected = {"name": internal["metadata"]["name"], "key": "graph-password"}

    assert "graph-password" in internal["data"]
    neo4j = containers(named(rendered, "StatefulSet", "-neo4j"))[0]
    retrieval = containers(named(rendered, "Deployment", "-retrieval"))[0]
    for container in (neo4j, retrieval):
        env = {entry["name"]: entry for entry in container["env"]}
        assert env["PRIMER_GRAPH_PASSWORD"]["valueFrom"]["secretKeyRef"] == expected


def test_a_named_secret_replaces_the_generated_password() -> None:
    rendered = render(*BUNDLED)

    assert "graph-password" not in named(rendered, "Secret", "-internal")["data"]


def test_retrieval_reads_the_graph_password_from_the_named_secret() -> None:
    retrieval = named(render(*GRAPH_ON), "Deployment", "-retrieval")
    env = {entry["name"]: entry for entry in containers(retrieval)[0]["env"]}

    assert env["PRIMER_GRAPH_URL"]["value"] == "bolt://neo4j.graph:7687"
    reference = env["PRIMER_GRAPH_PASSWORD"]["valueFrom"]["secretKeyRef"]
    assert reference == {"name": "primer-neo4j", "key": "graph-password"}
    assert "value" not in env["PRIMER_GRAPH_PASSWORD"]


def test_search_tuning_reaches_retrieval() -> None:
    retrieval = named(
        render(*GRAPH_ON, "graph.hops=1", "graph.candidates=40"), "Deployment", "-retrieval"
    )
    env = env_of(retrieval)

    assert env["PRIMER_GRAPH_HOPS"] == "1"
    assert env["PRIMER_GRAPH_CANDIDATES"] == "40"


def test_the_graph_schema_is_applied_by_a_hook_not_by_retrieval() -> None:
    """Every replica creating the same constraint at once is the race a hook avoids."""
    rendered = render(*GRAPH_ON)
    job = named(rendered, "Job", "-migrate-graph")

    assert job["metadata"]["annotations"]["helm.sh/hook"] == "pre-install,pre-upgrade"
    assert containers(job)[0]["command"] == ["python", "-m", "primer_retrieval.graph_schema"]
    assert "PRIMER_GRAPH_PASSWORD" in graph_variables(job)
    assert len(of_kind(rendered, "Job")) == 3


def test_only_retrieval_and_its_schema_job_reach_neo4j() -> None:
    """Chat, Control, the web app and the workers never open the graph.

    Ingestion hands extracted graphs to Retrieval rather than writing them,
    so the graph's password stays with the one service that owns the store.
    """
    rendered = render(*GRAPH_ON, *EXTRACTION_ON)

    holders = {
        name
        for name, workload in workloads(rendered).items()
        if "PRIMER_GRAPH_PASSWORD" in graph_variables(workload)
    }

    assert {name.rsplit("-", 1)[-1] for name in holders} == {"retrieval", "graph"}


def test_extraction_runs_on_the_index_worker_alone() -> None:
    """The parse worker produces passages; extraction reads them at indexing."""
    rendered = render(*GRAPH_ON, *EXTRACTION_ON)
    index = named(rendered, "Deployment", "-worker-index")
    parse = named(rendered, "Deployment", "-worker-parse")

    env = env_of(index)
    assert env["PRIMER_GRAPH_EXTRACTION_BASE_URL"] == "http://model/v1"
    assert env["PRIMER_GRAPH_EXTRACTION_MODEL"] == "extract-model"
    assert graph_variables(parse) == set()


def test_a_graph_url_without_its_password_is_refused() -> None:
    """Only a bundled Neo4j has a password the release can make up."""
    message = refusal(*GRAPH_OFF, "graph.url=bolt://neo4j.graph:7687")
    assert "graph.existingSecret is required" in message


def test_extraction_without_a_graph_is_refused() -> None:
    """Every document would extract a graph and then fail to store it."""
    assert "Set graph.url or graph.neo4j.enabled" in refusal(*GRAPH_OFF, *EXTRACTION_ON)


def test_extraction_without_a_model_is_refused() -> None:
    message = refusal(*GRAPH_ON, "graph.extraction.baseUrl=http://model/v1")
    assert "graph.extraction.model" in message


def test_a_graph_url_that_is_not_bolt_is_refused() -> None:
    """An http:// URL would be accepted here and fail on the first search."""
    message = refusal("graph.url=http://neo4j.graph:7474", "graph.existingSecret=primer-neo4j")
    assert "graph" in message and "url" in message


def test_a_bundled_neo4j_is_what_retrieval_is_pointed_at() -> None:
    rendered = render(*BUNDLED)
    service = named(rendered, "Service", "-neo4j")
    port = service["spec"]["ports"][0]["port"]

    url = env_of(named(rendered, "Deployment", "-retrieval"))["PRIMER_GRAPH_URL"]

    assert url == f"bolt://{service['metadata']['name']}:{port}"
    assert url == env_of(named(rendered, "Job", "-migrate-graph"))["PRIMER_GRAPH_URL"]


def test_a_bundled_neo4j_takes_its_password_from_the_graph_secret() -> None:
    """One Secret for both ends, so the server and its client cannot disagree."""
    neo4j = containers(named(render(*BUNDLED), "StatefulSet", "-neo4j"))[0]
    env = {entry["name"]: entry for entry in neo4j["env"]}

    reference = env["PRIMER_GRAPH_PASSWORD"]["valueFrom"]["secretKeyRef"]
    assert reference == {"name": "primer-neo4j", "key": "graph-password"}
    # Expanded by the kubelet from the variable above; a literal here would
    # be a password in the manifest.
    assert env["NEO4J_AUTH"]["value"] == "neo4j/$(PRIMER_GRAPH_PASSWORD)"
    assert env["NEO4J_server_http_enabled"]["value"] == "false"


def test_only_retrieval_and_the_schema_step_may_reach_a_bundled_neo4j() -> None:
    """The workers extract graphs but never open one; nor does anything else."""
    rendered = render(*BUNDLED)
    policy = named(rendered, "NetworkPolicy", "-allow-neo4j")
    selected = policy["spec"]["podSelector"]["matchLabels"]
    (rule,) = policy["spec"]["ingress"]

    admitted = {
        peer["podSelector"]["matchLabels"]["app.kubernetes.io/component"] for peer in rule["from"]
    }

    assert selected["app.kubernetes.io/component"] == "neo4j"
    assert admitted == {"retrieval", "migrate-graph"}
    assert [port["port"] for port in rule["ports"]] == [7687]
    labels = {
        name: workload["spec"]["template"]["metadata"]["labels"]["app.kubernetes.io/component"]
        for name, workload in workloads(rendered).items()
    }
    assert admitted <= set(labels.values()), "the policy admits a component nothing runs as"


def test_a_bundled_neo4j_runs_under_the_charts_hardening() -> None:
    """Non-root with a read-only root, like every other pod here.

    The image expects to write its own configuration; the chart gives it a
    seeded copy rather than relaxing the container.
    """
    rendered = render(*BUNDLED)
    statefulset = named(rendered, "StatefulSet", "-neo4j")
    pod = statefulset["spec"]["template"]["spec"]
    defaults = named(rendered, "Deployment", "-retrieval")["spec"]["template"]["spec"]

    assert pod["securityContext"] == defaults["securityContext"]
    for container in [*pod["initContainers"], *pod["containers"]]:
        assert container["securityContext"]["readOnlyRootFilesystem"] is True
        assert container["securityContext"]["capabilities"] == {"drop": ["ALL"]}
    mounts = {mount["mountPath"] for mount in pod["containers"][0]["volumeMounts"]}
    assert {"/data", "/var/lib/neo4j/conf", "/logs", "/tmp"} <= mounts  # noqa: S108


def test_a_bundled_neo4j_is_given_its_schema_once_it_is_running() -> None:
    """A pre hook would wait for a StatefulSet that does not exist yet.

    That is true before install, and before the upgrade that first turns the
    bundled Neo4j on - which is every 1.0 release taking the new default, so
    a pre-upgrade hook here would fail that upgrade and roll it back.
    """
    bundled = named(render(*BUNDLED), "Job", "-migrate-graph")
    external = named(render(*GRAPH_ON), "Job", "-migrate-graph")

    assert bundled["metadata"]["annotations"]["helm.sh/hook"] == "post-install,post-upgrade"
    assert external["metadata"]["annotations"]["helm.sh/hook"] == "pre-install,pre-upgrade"


def test_a_bundled_neo4j_meets_what_every_workload_here_does() -> None:
    """Its own account, no API token, and a size the scheduler can plan for.

    The cross-cutting tests in `test_chart.py` read the default render, which
    has no Neo4j in it, so they would pass for one that broke all three.
    """
    rendered = render(*BUNDLED)
    pod = named(rendered, "StatefulSet", "-neo4j")["spec"]["template"]["spec"]
    accounts = {doc["metadata"]["name"] for doc in of_kind(rendered, "ServiceAccount")}
    others = {
        workload["spec"]["template"]["spec"]["serviceAccountName"]
        for name, workload in workloads(rendered).items()
        if not name.endswith("-neo4j")
    }

    assert pod["serviceAccountName"] in accounts - others
    assert pod["automountServiceAccountToken"] is False
    for container in [*pod["initContainers"], *pod["containers"]]:
        assert container["resources"]["requests"] and container["resources"]["limits"]


def test_with_policies_off_a_bundled_neo4j_has_no_policy() -> None:
    rendered = render(*BUNDLED, "networkPolicies.enabled=false")

    assert of_kind(rendered, "NetworkPolicy") == []
    assert named(rendered, "StatefulSet", "-neo4j")


def test_a_bundled_neo4j_and_a_graph_url_together_are_refused() -> None:
    message = refusal(*BUNDLED, "graph.url=bolt://neo4j.graph:7687")
    assert "not both" in message


def test_a_bundled_neo4j_under_another_user_or_database_is_refused() -> None:
    """Community has one of each; any other name fails at the first search."""
    assert "both named neo4j" in refusal(*BUNDLED, "graph.username=primer")
    assert "both named neo4j" in refusal(*BUNDLED, "graph.database=primer")


def test_extraction_writes_into_a_bundled_neo4j() -> None:
    """The graph being on is what extraction needs, however it is provided."""
    index = named(render(*BUNDLED, *EXTRACTION_ON), "Deployment", "-worker-index")

    assert env_of(index)["PRIMER_GRAPH_EXTRACTION_MODEL"] == "extract-model"


def test_without_an_extraction_model_the_graph_is_built_by_the_chat_model() -> None:
    """A graph on by default would otherwise stay empty until someone found the setting."""
    env = env_of(named(render(), "Deployment", "-worker-index"))

    assert env["PRIMER_GRAPH_EXTRACTION_BASE_URL"] == "http://model/v1"
    assert env["PRIMER_GRAPH_EXTRACTION_MODEL"] == "chat-model"


def test_a_named_extraction_model_is_preferred_to_the_chat_model() -> None:
    env = env_of(named(render(*EXTRACTION_ON), "Deployment", "-worker-index"))

    assert env["PRIMER_GRAPH_EXTRACTION_MODEL"] == "extract-model"


def test_without_a_chat_model_to_fall_back_on_nothing_is_extracted() -> None:
    """An endpoint alone is not enough: extraction has no "first served model"."""
    rendered = render("inference.chat.model=")
    index = named(rendered, "Deployment", "-worker-index")

    assert not {name for name in graph_variables(index) if "EXTRACTION" in name}
    assert named(rendered, "StatefulSet", "-neo4j")
