"""Qwen3.5: a multimodal checkpoint, loaded whole and read through its hybrid layers.

Three of every four Qwen3.5 layers mix tokens with linear attention and
have no softmax attention output. Everything else Lab reads - the
residual stream, the MLPs, the logits - is there on every layer.
"""

from __future__ import annotations

import shutil
from collections.abc import Iterator
from pathlib import Path
from typing import Any, cast

import nnsight
import pytest
import torch
from fastapi.testclient import TestClient
from lab_owned import make
from lab_streams import PROMPT, completed, generate
from lab_support import HYBRID_EOS, HYBRID_LAYERS, TINY, build_tiny_hybrid_model, lab, settle
from primer_contracts.lab import LocalModel
from primer_lab.inspection import special_ids
from primer_lab.interventions import generate as generate_tokens
from primer_lab.interventions import stop_tokens
from primer_lab.loader import LoadPlan, load_model
from transformers import AutoModelForImageTextToText, Qwen3_5ForConditionalGeneration

HYBRID = "hybrid"
#: The one layer of the tiny hybrid model with softmax attention.
FULL = HYBRID_LAYERS.index("full_attention")


@pytest.fixture(scope="module")
def hybrid_model(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return build_tiny_hybrid_model(tmp_path_factory.mktemp("hybrid"))


@pytest.fixture
def hybrid(weights_dir: Path, hub, factory, hybrid_model: Path) -> Iterator[TestClient]:
    shutil.copytree(hybrid_model, weights_dir / HYBRID)
    with lab(weights_dir, hub, factory, lab_model=LocalModel(path=HYBRID)) as client:
        status = settle(client)
        assert status["ready"], status["switch"]
        yield client


def loaded(directory: Path) -> Any:
    plan = LoadPlan(
        directory=directory, architecture="qwen3_5", commit=None, device="cpu", dtype="float32"
    )
    return load_model(plan)


def test_a_qwen3_5_checkpoint_loads_as_the_image_text_model_it_is(hybrid, factory):
    """nnsight refuses `qwen3_5` through its default causal-LM class because
    the config names an image-text model, so Lab names the image-text class."""
    model = hybrid.get("/api/v1/lab/status").json()["model"]
    assert model["architecture"] == "qwen3_5"
    assert model["num_layers"] == len(HYBRID_LAYERS)
    (_, options), *_ = factory.calls
    assert options["automodel"] is AutoModelForImageTextToText
    assert "config_model" not in options


def test_its_logits_are_the_multimodal_models_own(hybrid_model):
    """Through nnsight, a text prompt must give what transformers gives it."""
    ids = torch.tensor([[62, 63, 3, 7, 7]])
    with torch.no_grad():
        reference = cast(Any, Qwen3_5ForConditionalGeneration.from_pretrained(hybrid_model))
        expected = reference(input_ids=ids).logits
        model = loaded(hybrid_model).model
        with model.trace(ids):
            logits = nnsight.save(model.logits)
    assert torch.allclose(logits, expected, atol=1e-5)


def test_every_layer_is_inspected_linear_or_not(hybrid):
    answer = hybrid.post("/api/v1/lab/inspect", json={"prompt": PROMPT, "chat": False})
    assert answer.status_code == 200, answer.text
    layers = answer.json()["layers"]
    assert len(layers) == len(HYBRID_LAYERS)
    assert all(layer["neuron_indices"] for layer in layers)


@pytest.mark.parametrize("site", ["resid_post", "mlp_out"])
def test_a_linear_attention_layer_reads_everywhere_but_attention(hybrid, site):
    request = {"prompt": PROMPT, "chat": False, "layer": 0, "token": 0, "site": site}
    answer = hybrid.post("/api/v1/lab/activation", json=request)
    assert answer.status_code == 200, answer.text
    assert len(answer.json()["values"]) == 16


def test_attention_is_read_on_the_layers_that_have_it(hybrid):
    request = {"prompt": PROMPT, "chat": False, "layer": FULL, "token": 0, "site": "attn_out"}
    answer = hybrid.post("/api/v1/lab/activation", json=request)
    assert answer.status_code == 200, answer.text


def test_reading_attention_on_a_linear_layer_says_which_layers_have_it(hybrid):
    """Without the check this is an nnsight exception from inside the pass,
    which reaches the user as a failed run rather than a wrong request."""
    request = {"prompt": PROMPT, "chat": False, "layer": 0, "token": 0, "site": "attn_out"}
    answer = hybrid.post("/api/v1/lab/activation", json=request)
    assert answer.status_code == 422
    assert f"attention layers are {FULL}" in answer.json()["detail"]


def test_editing_attention_on_a_linear_layer_is_refused_before_it_runs(hybrid):
    edit = {"site": "attn_out", "layer": 1, "op": "zero"}
    answer = generate(hybrid, interventions=[edit])
    assert answer.status_code == 422
    assert "Layer 1 mixes tokens with linear attention" in answer.json()["detail"]


def test_editing_attention_on_a_full_layer_changes_the_answer(hybrid):
    edit = {"site": "attn_out", "layer": FULL, "op": "zero"}
    target = completed(hybrid, interventions=[edit], target="hello")["target"]
    assert target["edited_logit"] != pytest.approx(target["baseline_logit"], abs=1e-4)


def test_a_steering_vector_is_made_and_added_on_a_linear_layer(hybrid):
    vector = make(hybrid, layer=1)
    assert vector.status_code == 201, vector.text
    edit = {"site": "resid_post", "layer": 1, "op": "add", "vector_id": vector.json()["id"]}
    target = completed(hybrid, interventions=[edit | {"value": 4.0}], target="hello")["target"]
    assert target["edited_logit"] != pytest.approx(target["baseline_logit"], abs=1e-2)


def test_unedited_generation_is_the_models_own_greedy_answer(hybrid_model):
    """Lab decodes one token at a time with its own cache. A linear-attention
    layer carries a recurrent state in that cache rather than keys and
    values, and a cache built without the config has nowhere to keep it."""
    model = loaded(hybrid_model).model
    prompt = [62, 63, 3, 7, 7]
    ours = generate_tokens(
        model,
        prompt,
        [],
        max_new_tokens=8,
        special=special_ids(model.tokenizer),
        cancelled=lambda: False,
        on_token=lambda index, token: None,
    )
    reference = cast(Any, Qwen3_5ForConditionalGeneration.from_pretrained(hybrid_model))
    ids = torch.tensor([prompt])
    expected = reference.generate(ids, max_new_tokens=8, do_sample=False)[0, len(prompt) :]
    assert ours.tokens == expected.tolist()


def test_generation_stops_at_the_language_models_end_token(hybrid_model):
    """A multimodal config has no end-of-sequence token at its top level:
    it is in the text config, and without it Lab would decode past the
    end of every answer whose tokenizer names a different one."""
    model = loaded(hybrid_model).model
    assert HYBRID_EOS in stop_tokens(model)


def test_an_architecture_nnterp_cannot_map_is_refused_by_name(client, weights_dir, factory):
    """nnterp's own message names the rename argument a developer would pass,
    which is no use to the person choosing a model."""
    shutil.copytree(weights_dir / TINY, weights_dir / "unmapped")
    factory.unmappable.add("unmapped")
    client.put("/api/v1/lab/model", json={"model": {"source": "local", "path": "unmapped"}})
    status = settle(client)
    assert status["switch"]["state"] == "failed"
    assert "cannot inspect qwen3 models yet" in status["switch"]["error"]
    assert "RenameConfig" not in status["switch"]["error"]
    assert status["model"]["reference"]["path"] == TINY
