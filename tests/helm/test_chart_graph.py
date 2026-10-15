"""The knowledge graph in the rendered chart: off by default, and on in one place.

Kept apart from `test_chart.py` because the graph is optional and its
assertions are about which workloads it reaches and which it must not.
"""

from __future__ import annotations

from typing import Any

from test_chart import containers, env_of, named, of_kind, refusal, render, workloads

GRAPH_ON = ("graph.url=bolt://neo4j.graph:7687", "graph.existingSecret=primer-neo4j")
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


def test_without_a_graph_url_nothing_is_told_about_a_graph() -> None:
    """The default deployment must render as it did before the graph existed."""
    rendered = render()
    found = workloads(rendered)
    assert found, "the chart rendered no workloads, so this test would check nothing"

    for name, workload in found.items():
        assert graph_variables(workload) == set(), f"{name} is told about a graph nobody set"
    assert not any(name.endswith("-migrate-graph") for name in found)


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
    assert len(of_kind(rendered, "Job")) == 4


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


def test_a_graph_without_its_password_is_refused() -> None:
    assert "graph.existingSecret is required" in refusal("graph.url=bolt://neo4j.graph:7687")


def test_extraction_without_a_graph_is_refused() -> None:
    """Every document would extract a graph and then fail to store it."""
    assert "Set graph.url" in refusal(*EXTRACTION_ON)


def test_extraction_without_a_model_is_refused() -> None:
    message = refusal(*GRAPH_ON, "graph.extraction.baseUrl=http://model/v1")
    assert "graph.extraction.model" in message


def test_a_graph_url_that_is_not_bolt_is_refused() -> None:
    """An http:// URL would be accepted here and fail on the first search."""
    message = refusal("graph.url=http://neo4j.graph:7474", "graph.existingSecret=primer-neo4j")
    assert "graph" in message and "url" in message
