"""Inspecting a prompt, against a real model small enough to build.

The reference for what the model "really" does is `transformers` itself,
loaded separately from Lab, so a lens that read the wrong layer or skipped
the final norm disagrees with something that did not share its mistake.
"""

from __future__ import annotations

import dataclasses
from pathlib import Path
from typing import Any, cast

import pytest
import torch
from fastapi import FastAPI
from fastapi.testclient import TestClient
from lab_support import TINY, build_tiny_model, lab, settle
from primer_contracts.lab import LENS_TOP, InspectedToken
from primer_lab import routes
from transformers import Qwen3ForCausalLM

PROMPT = "hello world w3 w7 w7"
#: Wraps a user turn in <eos> markers, so the template's own tokens are
#: ones the tests can tell apart from the prompt's.
TEMPLATE = "{% for m in messages %}<eos> {{ m['content'] }} <eos>{% endfor %}"


def inspect(client: TestClient, **body: Any) -> Any:
    return client.post("/api/v1/lab/inspect", json={"prompt": PROMPT, "chat": False} | body)


def activation(client: TestClient, **body: Any) -> Any:
    request = {"prompt": PROMPT, "chat": False, "layer": 0, "token": 0, "site": "resid_post"}
    return client.post("/api/v1/lab/activation", json=request | body)


def test_the_lens_at_the_last_layer_is_the_models_own_next_token_distribution(client, weights_dir):
    """The lens's one fixed point: past the last layer it is the output."""
    answer = inspect(client)
    assert answer.status_code == 200, answer.text
    body = answer.json()

    reference = Qwen3ForCausalLM.from_pretrained(weights_dir / TINY)
    ids = torch.tensor([[token["id"] for token in body["tokens"]]])
    with torch.no_grad():
        expected = reference(ids).logits[0].softmax(dim=-1).topk(LENS_TOP, dim=-1)
    last = body["layers"][-1]
    assert last["lens_tokens"] == expected.indices.tolist()
    assert torch.allclose(torch.tensor(last["lens_probabilities"]), expected.values, atol=1e-5)


def test_every_layer_reads_every_position(client):
    body = inspect(client, neurons=5).json()

    assert len(body["tokens"]) == 5
    assert len(body["layers"]) == body["model"]["num_layers"] == 2
    for layer in body["layers"]:
        assert len(layer["residual_norm"]) == 5
        assert [len(row) for row in layer["lens_tokens"]] == [LENS_TOP] * 5
        assert [len(row) for row in layer["neuron_indices"]] == [5] * 5
    assert set(body["vocabulary"]) == {
        str(i) for layer in body["layers"] for row in layer["lens_tokens"] for i in row
    }
    assert (body["hidden_size"], body["mlp_width"]) == (16, 32)


def test_top_neurons_are_the_largest_by_magnitude_and_keep_their_sign(client):
    """A neuron that fires strongly negative is as interesting as a positive one."""
    body = inspect(client, neurons=4).json()
    full = activation(client, layer=1, token=2, site="mlp_neuron").json()["values"]

    indices = body["layers"][1]["neuron_indices"][2]
    values = body["layers"][1]["neuron_activations"][2]
    assert values == pytest.approx([full[i] for i in indices], abs=1e-6)
    assert sorted(map(abs, full), reverse=True)[:4] == pytest.approx(list(map(abs, values)))
    assert any(value < 0 for value in full), "the fixture has no negative activation to keep"


@pytest.mark.parametrize(
    ("site", "width"), [("resid_post", 16), ("mlp_neuron", 32), ("mlp_out", 16), ("attn_out", 16)]
)
def test_an_activation_vector_has_the_width_of_its_site(client, site, width):
    answer = activation(client, site=site, layer=1, token=4)
    assert answer.status_code == 200, answer.text
    assert len(answer.json()["values"]) == width


def test_the_residual_vector_is_the_one_whose_norm_the_inspection_reports(client):
    norm = inspect(client).json()["layers"][0]["residual_norm"][3]
    values = activation(client, layer=0, token=3).json()["values"]
    assert torch.tensor(values).norm().item() == pytest.approx(norm, rel=1e-5)


@pytest.mark.parametrize(("field", "value"), [("layer", 2), ("token", 5)])
def test_an_activation_outside_the_model_or_prompt_is_refused(client, field, value):
    answer = activation(client, **{field: value})
    assert answer.status_code == 422
    assert f"No such {field}" in answer.json()["title"]


def test_a_prompt_over_the_limit_is_refused_naming_the_limit(weights_dir, hub, factory):
    with lab(weights_dir, hub, factory, lab_max_prompt_tokens=4) as client:
        settle(client)
        answer = inspect(client)
        assert inspect(client, prompt="w1 w2 w3 w4").status_code == 200

    assert answer.status_code == 422
    detail = answer.json()["detail"]
    assert "5 tokens" in detail
    assert "at most 4 (PRIMER_LAB_MAX_PROMPT_TOKENS)" in detail


def test_the_chat_template_wraps_the_prompt_and_marks_its_tokens(client, weights_dir):
    build_tiny_model(weights_dir / "chatty", chat_template=TEMPLATE)
    client.put("/api/v1/lab/model", json={"model": {"source": "local", "path": "chatty"}})
    assert settle(client)["model"]["reference"]["path"] == "chatty"

    wrapped = inspect(client, chat=True).json()
    plain = inspect(client, chat=False).json()

    assert wrapped["chat_template"] is True
    assert [t["special"] for t in wrapped["tokens"]] == [True] + [False] * 5 + [True]
    assert plain["chat_template"] is False
    assert not any(t["special"] for t in plain["tokens"])


def test_a_base_model_reads_the_prompt_as_it_is_even_when_chat_is_asked(client):
    body = inspect(client, chat=True).json()
    assert body["chat_template"] is False
    assert [t["text"] for t in body["tokens"]] == PROMPT.split()


def test_token_text_keeps_its_whitespace():
    """ " world" and "world" are different tokens, and the reader has to see which."""
    assert InspectedToken(id=1, text=" world", special=False).text == " world"


def test_a_run_whose_model_was_replaced_while_it_waited_is_refused(client, monkeypatch):
    """Its token ids belong to the old vocabulary, so reading them on the new model is wrong."""
    host = cast(FastAPI, client.app).state.host
    tokenize = routes.tokenize

    def then_switch(*args: Any, **kwargs: Any) -> Any:
        prompt = tokenize(*args, **kwargs)
        host.resident = dataclasses.replace(host.resident)
        return prompt

    monkeypatch.setattr(routes, "tokenize", then_switch)
    answer = inspect(client)
    assert answer.status_code == 409
    assert answer.json()["title"] == "The model changed"


def test_inspecting_with_no_model_loaded_says_so(weights_dir, hub, factory):
    factory.fail_for.add(TINY)
    with lab(weights_dir, hub, factory) as client:
        settle(client)
        answer = inspect(client)
    assert answer.status_code == 409
    assert answer.json()["title"] == "No model is loaded"


def test_a_run_past_the_time_limit_is_abandoned_with_the_setting_named(weights_dir, hub, factory):
    with lab(weights_dir, hub, factory, lab_run_timeout_seconds=1e-6) as client:
        settle(client)
        answer = inspect(client)
    assert answer.status_code == 504
    assert "PRIMER_LAB_RUN_TIMEOUT_SECONDS" in answer.json()["detail"]


def test_a_full_length_prompt_on_a_deep_model_stays_within_its_shape(tmp_path: Path, hub, factory):
    """The response is bounded by layers x tokens x (LENS_TOP + neurons), and no more.

    36 layers is Qwen3-4B's depth and 512 tokens the default prompt limit,
    so this is the largest response a default deployment can send.
    """
    weights = tmp_path / "deep-weights"
    build_tiny_model(weights / TINY, layers=36, positions=512)
    prompt = " ".join(f"w{i % 60}" for i in range(512))
    with lab(weights, hub, factory) as client:
        settle(client)
        answer = inspect(client, prompt=prompt, neurons=32)
    assert answer.status_code == 200, answer.text
    body = answer.json()

    assert len(body["tokens"]) == 512
    assert len(body["layers"]) == 36
    for layer in body["layers"]:
        assert set(layer) == {
            "residual_norm",
            "lens_tokens",
            "lens_probabilities",
            "neuron_indices",
            "neuron_activations",
            "feature_indices",
            "feature_activations",
            "feature_labels",
        }
        assert len(layer["residual_norm"]) == 512
        for column, width in [
            ("lens_tokens", LENS_TOP),
            ("lens_probabilities", LENS_TOP),
            ("neuron_indices", 32),
            ("neuron_activations", 32),
        ]:
            assert {len(row) for row in layer[column]} == {width}, column
    assert len(body["vocabulary"]) <= 64
