"""Activation outliers and probes, read beside the score of each token.

The reference statistics are recomputed here from `transformers` and
scikit-learn, loaded separately from Lab, so a residual read at the wrong
layer or position, or a reference reused from another model, disagrees with
something that did not share the mistake.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, cast

import numpy as np
import pytest
import torch
from fastapi import FastAPI
from fastapi.testclient import TestClient
from lab_support import TINY, as_user, build_tiny_model, settle
from primer_lab import reference
from primer_service.identity import LOCAL_SUBJECT, user_id_for_subject
from sklearn.covariance import LedoitWolf
from transformers import Qwen3ForCausalLM

PROMPT = "hello world w3 w7 w7 w12"
PROMPT_IDS = [62, 63, 3, 7, 7, 12]
#: Plain word-level text the tiny model can read, in place of the bundled English.
TEXTS = tuple(
    " ".join(f"w{n}" for n in np.random.default_rng(seed).integers(0, 60, size=20))
    for seed in range(10)
)


@pytest.fixture(autouse=True)
def tiny_texts(monkeypatch):
    monkeypatch.setattr(reference, "TEXTS", TEXTS)


def score(client: TestClient, *, headers: dict[str, str] | None = None, **body: Any) -> Any:
    request = {"prompt": PROMPT, "mode": "text"} | body
    return client.post("/api/v1/lab/score", json=request, headers=headers)


def ids_of(text: str) -> list[int]:
    return [int(word[1:]) for word in text.split()]


def residuals(model: Qwen3ForCausalLM, ids: list[int], layer: int) -> np.ndarray:
    """After `layer`, at every position but the first, read by a hook rather than a trace."""
    caught: list[torch.Tensor] = []

    def hook(module: Any, inputs: Any, output: Any) -> None:
        caught.append(output[0] if isinstance(output, tuple) else output)

    handle = model.model.layers[layer].register_forward_hook(hook)
    with torch.no_grad():
        model(torch.tensor([ids]))
    handle.remove()
    return caught[0][0, 1:].double().numpy()


def expected(directory: Path, layer: int) -> tuple[list[float], float]:
    """Distances of PROMPT's tokens, and the threshold, computed independently."""
    model = Qwen3ForCausalLM.from_pretrained(directory)
    rows = [residuals(model, ids_of(text), layer) for text in TEXTS]
    fitted = LedoitWolf().fit(np.concatenate([r for i, r in enumerate(rows) if i % 5 != 4]))
    held_out = np.concatenate([r for i, r in enumerate(rows) if i % 5 == 4])
    threshold = float(np.quantile(np.sqrt(fitted.mahalanobis(held_out)), 0.99))
    distances = np.sqrt(fitted.mahalanobis(residuals(model, PROMPT_IDS, layer)))
    return distances.tolist(), threshold


@pytest.mark.parametrize("layer", [0, 1])
def test_each_token_s_distance_is_measured_against_reference_text(client, weights_dir, layer):
    answer = score(client, outlier_layer=layer)
    assert answer.status_code == 200, answer.text
    outliers = answer.json()["outliers"]

    distances, threshold = expected(weights_dir / TINY, layer)
    assert outliers["layer"] == layer
    assert outliers["distances"] == pytest.approx(distances, rel=1e-3)
    assert outliers["threshold"] == pytest.approx(threshold, rel=1e-3)
    assert outliers["reference_tokens"] == 8 * 19


def test_distances_line_up_with_the_scored_tokens(client):
    """Special tokens are not scored, so they must not be measured either."""
    body = score(client, prompt="hello <eos> world w3 w9", outlier_layer=1).json()

    assert len(body["outliers"]["distances"]) == len(body["score"]["tokens"]) == 3


def test_a_new_model_is_measured_against_its_own_reference(client, weights_dir):
    """A reference kept from the old model would score the new one against the wrong stream."""
    before = score(client, outlier_layer=1).json()["outliers"]
    build_tiny_model(weights_dir / "other", seed=1)
    client.put("/api/v1/lab/model", json={"model": {"source": "local", "path": "other"}})
    assert settle(client)["model"]["reference"]["path"] == "other"

    after = score(client, outlier_layer=1).json()["outliers"]

    distances, threshold = expected(weights_dir / "other", 1)
    assert after["distances"] == pytest.approx(distances, rel=1e-3)
    assert after["threshold"] == pytest.approx(threshold, rel=1e-3)
    assert after["distances"] != pytest.approx(before["distances"], rel=1e-3)


def test_a_reference_is_built_once_per_layer_and_the_oldest_is_let_go(client, monkeypatch):
    built: list[int] = []
    build = reference.build_reference

    def counted(model: Any, layer: int, special: set[int]) -> reference.Reference:
        built.append(layer)
        return build(model, layer, special)

    monkeypatch.setattr(reference, "build_reference", counted)
    monkeypatch.setattr(reference, "KEPT", 1)

    for layer in (0, 0, 1, 0):
        assert score(client, outlier_layer=layer).status_code == 200

    assert built == [0, 1, 0]


def test_outliers_at_a_layer_the_model_does_not_have_are_refused(client):
    answer = score(client, outlier_layer=2)

    assert answer.status_code == 422
    assert "0 to 1" in answer.json()["detail"]


def train(client: TestClient, *, headers: dict[str, str] | None = None) -> str:
    body = {
        "label": "test",
        "layer": 0,
        "positive": ["hello world", "w1 w2 w3", "w1 w3", "w2 w1 w3"],
        "negative": ["w9 w8", "w4 w5 w6 w7", "w5 w4", "w8 w9 w7"],
        "chat": False,
    }
    answer = client.post("/api/v1/lab/probes", json=body, headers=headers)
    assert answer.status_code == 201, answer.text
    return answer.json()["id"]


def test_a_probe_is_applied_to_each_scored_token(client, weights_dir):
    probe = train(client)

    body = score(client, probe_id=probe).json()

    local = user_id_for_subject(LOCAL_SUBJECT)
    resident = cast(FastAPI, client.app).state.host.resident
    fitted = resident.probes.owned_by(local)[probe]
    rows = residuals(Qwen3ForCausalLM.from_pretrained(weights_dir / TINY), PROMPT_IDS, 0)
    assert body["probe"]["probe"]["id"] == probe
    assert body["probe"]["probabilities"] == pytest.approx(
        fitted.regression.predict_proba(rows)[:, 1].tolist(), abs=1e-4
    )
    assert body["outliers"] is None


def test_someone_elses_probe_cannot_be_applied_even_knowing_its_id(shared):
    """A probe is computed from its maker's prompts, which may be private."""
    probe = train(shared, headers=as_user("ada"))

    answer = score(shared, probe_id=probe, headers=as_user("bo"))

    assert answer.status_code == 422
    assert answer.json()["title"] == "No such probe"
    assert score(shared, probe_id=probe, headers=as_user("ada")).status_code == 200
