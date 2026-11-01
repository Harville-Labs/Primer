"""SAE features: fetched beside the model, opened safely, encoded as trained.

The reference encode in these tests is written independently of Lab's -
every feature's pre-activation, sorted - so a Lab that added a ReLU,
subtracted the decoder bias, or read the wrong layer disagrees with
something that did not share its mistake.
"""

from __future__ import annotations

import builtins
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
import torch
from fastapi.testclient import TestClient
from lab_support import build_saes, lab, settle
from primer_contracts.lab import HubModel
from primer_lab.config import Settings

MODEL = "lab/tiny"
SAES = "lab/tiny-saes"
PROMPT = "hello world w3 w7 w7"


@pytest.fixture
def published(hub, tiny_model: Path) -> None:
    hub.publish(MODEL, tiny_model)


def lab_with_saes(weights_dir: Path, hub, factory, **overrides: Any) -> TestClient:
    return lab(
        weights_dir,
        hub,
        factory,
        lab_model=HubModel(repository=MODEL),
        lab_saes={MODEL: SAES},
        **overrides,
    )


@pytest.fixture
def with_saes(weights_dir, hub, factory, published, tmp_path) -> Iterator[TestClient]:
    hub.publish(SAES, build_saes(tmp_path / "saes", layers=[1]))
    with lab_with_saes(weights_dir, hub, factory) as client:
        settle(client)
        yield client


def inspect(client: TestClient, **body: Any) -> dict:
    answer = client.post(
        "/api/v1/lab/inspect", json={"prompt": PROMPT, "chat": False, "features": 4} | body
    )
    assert answer.status_code == 200, answer.text
    return answer.json()


def residual(client: TestClient, layer: int, token: int) -> torch.Tensor:
    request = {"prompt": PROMPT, "chat": False, "layer": layer, "token": token}
    answer = client.post("/api/v1/lab/activation", json=request | {"site": "resid_post"})
    return torch.tensor(answer.json()["values"])


def test_features_are_the_topk_encode_of_the_residual_after_their_layer(with_saes, tmp_path):
    body = inspect(with_saes)
    sae = torch.load(tmp_path / "saes" / "layer1.sae.pt", weights_only=True)

    assert body["layers"][1]["feature_indices"] is not None
    for token in range(len(body["tokens"])):
        stream = residual(with_saes, 1, token)
        pre = [
            (float(sae["b_enc"][f] + torch.dot(sae["W_enc"][f], stream)), f)
            for f in range(sae["W_enc"].shape[0])
        ]
        expected = sorted(pre, reverse=True)[:4]
        assert body["layers"][1]["feature_indices"][token] == [f for _, f in expected]
        assert body["layers"][1]["feature_activations"][token] == pytest.approx(
            [value for value, _ in expected], abs=1e-4
        )


def test_a_feature_below_zero_is_reported_as_it_is_rather_than_clipped(
    weights_dir, hub, factory, published, tmp_path
):
    """Qwen-Scope's encode has no ReLU; adding one would flatten these to zero."""
    directory = build_saes(tmp_path / "saes", layers=[1])
    state = torch.load(directory / "layer1.sae.pt", weights_only=True)
    torch.save(state | {"b_enc": state["b_enc"] - 1e4}, directory / "layer1.sae.pt")
    hub.publish(SAES, directory)

    with lab_with_saes(weights_dir, hub, factory) as client:
        settle(client)
        activations = inspect(client)["layers"][1]["feature_activations"]

    assert activations
    assert all(value < 0 for row in activations for value in row), activations


def test_a_layer_without_an_sae_reads_no_features_rather_than_none_firing(with_saes):
    layer = inspect(with_saes)["layers"][0]
    assert layer["feature_indices"] is None
    assert layer["feature_activations"] is None


def test_a_reading_asks_for_no_more_features_than_the_sae_activates(with_saes):
    """k is how many fire; past it, the rest are not features of this token."""
    rows = inspect(with_saes, features=32)["layers"][1]["feature_indices"]
    assert {len(row) for row in rows} == {8}


def test_the_status_says_which_saes_are_attached_and_where(with_saes):
    saes = with_saes.get("/api/v1/lab/status").json()["model"]["saes"]
    assert saes["state"] == "ready", saes
    assert (saes["repository"], saes["width"], saes["k"], saes["layers"]) == (SAES, 64, 8, [1])
    assert saes["commit"]


def test_only_the_config_and_the_layers_it_names_are_downloaded(with_saes, hub, weights_dir):
    asked = [patterns for repository, _, patterns in hub.downloaded if repository == SAES]
    assert asked == [["config.json"], ["layer1.sae.pt"]]
    assert not list((weights_dir / "hub").rglob("app.py"))


def test_a_model_with_no_saes_mapped_reports_none(client):
    assert client.get("/api/v1/lab/status").json()["model"]["saes"] is None
    assert inspect(client)["layers"][0]["feature_indices"] is None


def refused(weights_dir, hub, factory) -> tuple[dict, dict]:
    """The SAE status, and an inspection made after the SAEs failed."""
    with lab_with_saes(weights_dir, hub, factory) as client:
        status = settle(client)
        return status["model"]["saes"], inspect(client)


def test_saes_for_another_width_are_refused_and_the_model_stays_usable(
    weights_dir, hub, factory, published, tmp_path
):
    hub.publish(SAES, build_saes(tmp_path / "saes", d_model=32))

    saes, body = refused(weights_dir, hub, factory)

    assert saes["state"] == "failed"
    assert "32-wide" in saes["error"] and "16" in saes["error"], saes["error"]
    assert all(layer["feature_indices"] is None for layer in body["layers"])
    assert [p for r, _, p in hub.downloaded if r == SAES] == [["config.json"]]


def test_another_kind_of_sae_is_refused_rather_than_read_as_topk(
    weights_dir, hub, factory, published, tmp_path
):
    hub.publish(SAES, build_saes(tmp_path / "saes", model_type="relu_sae"))
    saes, _ = refused(weights_dir, hub, factory)
    assert saes["state"] == "failed"
    assert "TopK" in saes["error"] and "model_type" in saes["error"], saes["error"]


def test_saes_over_the_size_limit_are_refused_before_a_layer_downloads(
    weights_dir, hub, factory, published, tmp_path
):
    hub.publish(SAES, build_saes(tmp_path / "saes"))
    hub.sizes[SAES] = {"layer0.sae.pt": 10**12}

    saes, _ = refused(weights_dir, hub, factory)

    assert saes["state"] == "failed"
    assert "limit" in saes["error"], saes["error"]
    assert [p for r, _, p in hub.downloaded if r == SAES] == [["config.json"]]


def test_a_layer_of_the_wrong_shape_is_refused_naming_the_tensor(
    weights_dir, hub, factory, published, tmp_path
):
    directory = build_saes(tmp_path / "saes")
    state = torch.load(directory / "layer1.sae.pt", weights_only=True)
    torch.save(state | {"W_enc": state["W_enc"].T.contiguous()}, directory / "layer1.sae.pt")
    hub.publish(SAES, directory)

    saes, _ = refused(weights_dir, hub, factory)

    assert saes["state"] == "failed"
    assert "layer1.sae.pt: W_enc" in saes["error"], saes["error"]


class Payload:
    """Unpickles by calling `open`, which would create the marker file."""

    def __init__(self, marker: Path) -> None:
        self.marker = marker

    def __reduce__(self) -> tuple[Any, ...]:
        return (builtins.open, (str(self.marker), "w"))


def test_a_layer_that_would_run_code_when_unpickled_is_refused_without_running_it(
    weights_dir, hub, factory, published, tmp_path
):
    directory = build_saes(tmp_path / "saes")
    marker = tmp_path / "ran"
    state = torch.load(directory / "layer0.sae.pt", weights_only=True)
    torch.save(state | {"W_enc": Payload(marker)}, directory / "layer0.sae.pt")
    hub.publish(SAES, directory)

    saes, _ = refused(weights_dir, hub, factory)

    assert saes["state"] == "failed"
    assert "not a plain set of tensors" in saes["error"], saes["error"]
    assert not marker.exists(), "loading the SAE ran the code in its pickle"


def test_by_default_qwen3_1_7b_is_given_qwen_scope(monkeypatch):
    monkeypatch.delenv("PRIMER_LAB_SAES", raising=False)
    saes = Settings().lab_saes
    assert saes["Qwen/Qwen3-1.7B"] == "Qwen/SAE-Res-Qwen3-1.7B-Base-W32K-L0_50"
    assert saes["Qwen/Qwen3-1.7B-Base"] == saes["Qwen/Qwen3-1.7B"]


def test_an_operator_maps_saes_as_json_and_can_map_none(monkeypatch):
    monkeypatch.setenv("PRIMER_LAB_SAES", '{"me/model": "me/model-saes"}')
    assert Settings().lab_saes == {"me/model": "me/model-saes"}
    monkeypatch.setenv("PRIMER_LAB_SAES", "{}")
    assert Settings().lab_saes == {}
