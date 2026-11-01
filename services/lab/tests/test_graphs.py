"""Attribution graphs: what Lab tells the builder, and what it does with the answer.

Lab owns every decision about a build - which tokens, which files, which
bounds - so these tests read what the stand-in builder was asked, and
check that a prompt Lab should refuse never reaches it at all.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from lab_graphs import CANNED, TRANSCODERS, FakeBuilder, build_transcoders
from lab_support import MODEL, lab, settle
from primer_contracts.lab import HubModel
from primer_lab.config import Settings

GRAPH = "/api/v1/lab/graph"


@pytest.fixture
def builder(tmp_path: Path) -> FakeBuilder:
    return FakeBuilder(tmp_path / "builder")


def lab_with_graphs(weights_dir, hub, factory, builder: FakeBuilder, **overrides: Any):
    return lab(
        weights_dir,
        hub,
        factory,
        lab_model=HubModel(repository=MODEL),
        lab_transcoders={MODEL: TRANSCODERS},
        lab_graph_python=builder.python,
        **overrides,
    )


@pytest.fixture
def graphs(weights_dir, hub, factory, published, builder, tmp_path) -> Iterator[TestClient]:
    hub.publish(TRANSCODERS, build_transcoders(tmp_path / "transcoders"))
    with lab_with_graphs(weights_dir, hub, factory, builder) as client:
        settle(client)
        yield client


def transcoder_status(weights_dir, hub, factory, builder, **overrides: Any) -> dict:
    with lab_with_graphs(weights_dir, hub, factory, builder, **overrides) as client:
        return settle(client)["model"]["transcoders"]


def test_a_graph_is_the_builders_with_the_model_and_transcoders_it_was_built_from(graphs):
    answer = graphs.post(GRAPH, json={"prompt": "hello world"})
    assert answer.status_code == 200, answer.text
    body = answer.json()
    assert body["graph"] == CANNED.model_dump(mode="json")
    assert body["model"]["reference"]["repository"] == MODEL
    assert body["transcoders"]["state"] == "ready"
    assert body["transcoders"]["width"] == 32


def test_the_builder_is_given_the_prompts_tokens_the_layers_in_order_and_labs_bounds(
    graphs, builder
):
    graphs.post(GRAPH, json={"prompt": "hello world", "node_threshold": 0.5})
    asked = builder.asked
    assert asked is not None
    assert asked["token_ids"] == [62, 63], "the prompt should be read with nothing added"
    assert [Path(p).name for p in asked["transcoders"]] == [
        "layer_0.safetensors",
        "layer_1.safetensors",
    ]
    assert asked["scan"].startswith(f"{TRANSCODERS}@")
    assert asked["target_id"] is None
    assert asked["node_threshold"] == 0.5
    settings = Settings()
    assert asked["max_feature_nodes"] == settings.lab_graph_max_feature_nodes
    assert asked["max_edges"] == settings.lab_graph_max_edges
    assert (asked["device"], asked["dtype"]) == ("cpu", "float32")


def test_a_target_is_sent_as_its_one_token(graphs, builder):
    assert graphs.post(GRAPH, json={"prompt": "hello", "target": "w5"}).status_code == 200
    assert builder.asked is not None and builder.asked["target_id"] == 5


def test_a_target_of_several_tokens_is_refused_before_a_build(graphs, builder):
    answer = graphs.post(GRAPH, json={"prompt": "hello", "target": "w5 w6"})
    assert answer.status_code == 422
    assert "2 tokens" in answer.json()["detail"]
    assert builder.asked is None


def test_a_prompt_holding_a_special_token_is_refused_before_a_build(graphs, builder):
    # A second attention sink after the one circuit-tracer zeroes would be
    # credited with most of the graph.
    answer = graphs.post(GRAPH, json={"prompt": "hello <eos> world"})
    assert answer.status_code == 422
    assert "<eos>" in answer.json()["detail"]
    assert builder.asked is None


def test_a_prompt_over_the_graph_limit_is_refused_naming_the_setting(
    weights_dir, hub, factory, published, builder, tmp_path
):
    hub.publish(TRANSCODERS, build_transcoders(tmp_path / "transcoders"))
    with lab_with_graphs(
        weights_dir, hub, factory, builder, lab_graph_max_prompt_tokens=2
    ) as client:
        settle(client)
        answer = client.post(GRAPH, json={"prompt": "w1 w2 w3"})
    assert answer.status_code == 422
    assert "PRIMER_LAB_GRAPH_MAX_PROMPT_TOKENS" in answer.json()["detail"]
    assert builder.asked is None


def test_a_build_that_runs_over_is_killed_and_reported_naming_the_graph_limit(
    weights_dir, hub, factory, published, builder, tmp_path
):
    hub.publish(TRANSCODERS, build_transcoders(tmp_path / "transcoders"))
    builder.does("hang")
    with lab_with_graphs(weights_dir, hub, factory, builder, lab_graph_timeout_seconds=1) as client:
        settle(client)
        answer = client.post(GRAPH, json={"prompt": "hello"})
        assert answer.status_code == 504
        assert "PRIMER_LAB_GRAPH_TIMEOUT_SECONDS" in answer.json()["detail"]
        # Killed rather than abandoned, so the queue is free at once.
        builder.does("graph")
        assert client.post(GRAPH, json={"prompt": "hello"}).status_code == 200


@pytest.mark.parametrize("mode", ["crash", "garbage"])
def test_a_build_that_fails_is_logged_and_told_without_its_output(graphs, builder, caplog, mode):
    builder.does(mode)
    with caplog.at_level(logging.WARNING, logger="primer_lab.graphs"):
        answer = graphs.post(GRAPH, json={"prompt": "hello"})
    assert answer.status_code == 500
    assert "fell over" not in answer.text
    assert caplog.records, "the reason should reach the operator's log"
    if mode == "crash":
        assert "fell over" in caplog.text


def test_the_builder_is_not_given_labs_settings(graphs, builder, monkeypatch):
    monkeypatch.setenv("PRIMER_DATABASE_URL", "postgresql://lab:not-a-real-one@db/lab")
    graphs.post(GRAPH, json={"prompt": "hello"})
    assert not [name for name in builder.environment if name.startswith("PRIMER_")]


def test_without_a_builder_no_transcoders_are_fetched_and_graphs_are_refused(
    weights_dir, hub, factory, published, tmp_path
):
    hub.publish(TRANSCODERS, build_transcoders(tmp_path / "transcoders"))
    with lab(
        weights_dir,
        hub,
        factory,
        lab_model=HubModel(repository=MODEL),
        lab_transcoders={MODEL: TRANSCODERS},
    ) as client:
        status = settle(client)
        answer = client.post(GRAPH, json={"prompt": "hello"})
    assert status["graph_max_prompt_tokens"] is None
    assert status["model"]["transcoders"] is None
    assert TRANSCODERS not in [r for r, _ in hub.described]
    assert answer.status_code == 422
    assert "PRIMER_LAB_GRAPH_PYTHON" in answer.json()["detail"]


def test_a_transcoder_repository_outside_the_allowed_patterns_is_refused_unasked(
    weights_dir, hub, factory, published, builder, tmp_path
):
    hub.publish(TRANSCODERS, build_transcoders(tmp_path / "transcoders"))
    status = transcoder_status(weights_dir, hub, factory, builder, lab_repository_patterns=(MODEL,))
    assert status["state"] == "failed"
    assert "does not allow" in status["error"], status["error"]
    assert TRANSCODERS not in [r for r, _ in hub.described]


@pytest.mark.parametrize(
    ("repository", "problem"),
    [
        ({"d_model": 32}, "16-wide"),
        ({"layers": 1}, "layer_1.safetensors"),
        ({"layers": 3}, "more layers"),
        ({"widths": {1: 48}}, "different widths"),
        ({"config": 'model_kind: "sae"\n'}, "model_kind"),
        (
            {
                "config": 'model_kind: "transcoder_set"\nfeature_input_hook: "hook_resid_mid"\n'
                'feature_output_hook: "mlp.hook_out"\n'
            },
            "hook_resid_mid",
        ),
        ({"config": "{ not: yaml"}, "not YAML"),
    ],
)
def test_transcoders_that_do_not_fit_the_model_are_refused_saying_why(
    weights_dir, hub, factory, published, builder, tmp_path, repository, problem
):
    hub.publish(TRANSCODERS, build_transcoders(tmp_path / "transcoders", **repository))
    status = transcoder_status(weights_dir, hub, factory, builder)
    assert status["state"] == "failed"
    assert problem in status["error"], status["error"]


def test_a_layer_that_is_not_safetensors_is_refused(
    weights_dir, hub, factory, published, builder, tmp_path
):
    directory = build_transcoders(tmp_path / "transcoders")
    (directory / "layer_1.safetensors").write_bytes(b"\x80\x04 a pickle, not a header")
    hub.publish(TRANSCODERS, directory)
    status = transcoder_status(weights_dir, hub, factory, builder)
    assert status["state"] == "failed"
    assert "not a safetensors file" in status["error"], status["error"]


def test_transcoders_over_their_size_limit_are_refused_before_a_layer_downloads(
    weights_dir, hub, factory, published, builder, tmp_path
):
    hub.publish(TRANSCODERS, build_transcoders(tmp_path / "transcoders"))
    status = transcoder_status(weights_dir, hub, factory, builder, lab_max_transcoder_bytes=1000)
    assert status["state"] == "failed"
    assert "limit" in status["error"], status["error"]
    assert [p for r, _, p in hub.downloaded if r == TRANSCODERS] == [["config.yaml"]]


def test_graphs_wait_for_transcoders_that_failed_and_say_why(
    weights_dir, hub, factory, published, builder
):
    with lab_with_graphs(weights_dir, hub, factory, builder) as client:
        settle(client)
        answer = client.post(GRAPH, json={"prompt": "hello"})
    assert answer.status_code == 409
    assert TRANSCODERS in answer.json()["detail"]


def test_by_default_qwen3_4b_is_given_the_published_transcoders(monkeypatch):
    monkeypatch.delenv("PRIMER_LAB_TRANSCODERS", raising=False)
    settings = Settings()
    assert settings.lab_transcoders == {"Qwen/Qwen3-4B": "mwhanna/qwen3-4b-transcoders"}
    assert settings.lab_graph_python is None
