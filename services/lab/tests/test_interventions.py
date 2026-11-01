"""Generating with edits to the forward pass, against the real tiny model.

The tiny model is random, so an edit rarely changes which token wins. The
tests that need to see an edit take effect watch a target token's logit
at the first answer position instead, which moves with any edit upstream
of it.
"""

from __future__ import annotations

from typing import Any, cast

import pytest
import torch
from fastapi import FastAPI
from fastapi.testclient import TestClient
from lab_streams import PROMPT, completed, events, generate
from lab_support import TINY, lab, settle
from primer_contracts.lab import Intervention
from primer_lab.interventions import Edit, apply, first_divergence, prompt_means, selected
from primer_lab.interventions import generate as generate_tokens
from transformers import Qwen3ForCausalLM

#: A token to watch. One token in the tiny vocabulary.
TARGET = "w3"


def edit(**fields: Any) -> dict[str, Any]:
    return Intervention.model_validate(fields).model_dump(mode="json", exclude_none=True)


def watched(client: TestClient, *interventions: dict[str, Any], **body: Any) -> dict[str, Any]:
    return completed(client, interventions=list(interventions), target=TARGET, **body)["target"]


def test_with_no_edits_both_runs_are_the_models_own_greedy_answer(client, weights_dir):
    """The baseline is only a baseline if it is what the model does untouched."""
    last = completed(client, max_new_tokens=8)

    # transformers types `generate` against a protocol its own models do not satisfy.
    reference = cast(Any, Qwen3ForCausalLM.from_pretrained(weights_dir / TINY))
    ids = torch.tensor([[token["id"] for token in last["prompt"]]])
    expected = reference.generate(ids, max_new_tokens=8, do_sample=False)[0, ids.shape[1] :]
    assert [token["id"] for token in last["baseline"]] == expected.tolist()
    assert last["edited"] == last["baseline"]
    assert last["diverged_at"] is None


def test_zeroing_every_neuron_is_zeroing_the_mlps_output(client):
    """The MLP's output is its down projection of the neurons, and Qwen3's has no bias.

    So the two edits are the same edit made at two sites, and a neuron edit
    that wrote nowhere, or wrote somewhere other than where it read, would
    not agree with the output edit.
    """
    by_output = watched(client, edit(site="mlp_out", layer=0, op="zero"))
    by_neurons = watched(client, edit(site="mlp_neuron", layer=0, op="zero"))

    assert by_neurons["edited_logit"] == pytest.approx(by_output["edited_logit"], abs=1e-5)
    assert by_output["edited_logit"] != pytest.approx(by_output["baseline_logit"], abs=1e-4)


def test_a_generated_only_edit_leaves_the_first_answer_token_alone(client):
    """The first answer token is read from the prompt's last position, which is not generated."""
    strong = {"site": "resid_post", "layer": 0, "op": "scale", "value": 0.0}

    later = watched(client, edit(**strong, positions="generated"))
    now = watched(client, edit(**strong, positions="prompt"))

    assert later["edited_logit"] == pytest.approx(later["baseline_logit"], abs=1e-6)
    assert now["edited_logit"] != pytest.approx(now["baseline_logit"], abs=1e-4)


def test_a_generated_only_edit_changes_what_comes_after_the_first_token(client):
    last = completed(
        client,
        max_new_tokens=6,
        interventions=[
            edit(site="mlp_neuron", layer=1, index=2, op="clamp", value=10, positions="generated")
        ],
    )

    assert last["edited"][0] == last["baseline"][0]
    assert last["diverged_at"] is not None and last["diverged_at"] >= 1


def test_scaling_a_feature_by_one_changes_nothing(with_saes):
    """A feature edit adds only the change in that feature through the decoder.

    Replacing the residual with the SAE's reconstruction instead would drop
    the SAE's error term at every edited position, which is a large edit
    that this would catch. The feature is one that fires at the last
    position: scaling one that is silent multiplies zero, and would pass
    whatever the edit added.
    """
    reading = with_saes.post(
        "/api/v1/lab/inspect", json={"prompt": PROMPT, "chat": False, "features": 1}
    ).json()
    (firing,) = reading["layers"][1]["feature_indices"][-1]
    feature = {"site": "sae_feature", "layer": 1, "index": firing}

    same = watched(with_saes, edit(**feature, op="scale", value=1.0))
    moved = watched(with_saes, edit(**feature, op="clamp", value=50.0))

    assert same["edited_logit"] == pytest.approx(same["baseline_logit"], abs=1e-5)
    assert moved["edited_logit"] != pytest.approx(moved["baseline_logit"], abs=1e-3)


@pytest.mark.parametrize(
    ("intervention", "named"),
    [
        ({"site": "resid_post", "layer": 2, "op": "zero"}, "layers are 0 to 1"),
        ({"site": "mlp_neuron", "layer": 0, "index": 32, "op": "zero"}, "They are 0 to 31"),
        ({"site": "attn_out", "layer": 0, "index": 16, "op": "zero"}, "They are 0 to 15"),
        ({"site": "sae_feature", "layer": 1, "index": 0, "op": "zero"}, "no SAEs ready"),
        (
            {"site": "resid_post", "layer": 0, "op": "add", "vector_id": "0" * 32},
            "no steering vector",
        ),
    ],
)
def test_an_edit_outside_the_model_is_refused_naming_what_there_is(client, intervention, named):
    answer = generate(client, interventions=[intervention])

    assert answer.status_code == 422, answer.text
    assert named in answer.json()["detail"]


@pytest.mark.parametrize(
    ("intervention", "named"),
    [
        ({"layer": 0, "index": 0}, "SAEs are at layers 1"),
        ({"layer": 1, "index": 64}, "no feature 64 at sae_feature. They are 0 to 63"),
    ],
)
def test_a_feature_without_an_sae_is_refused_naming_where_there_are_some(
    with_saes, intervention, named
):
    answer = generate(
        with_saes, interventions=[{"site": "sae_feature", "op": "zero"} | intervention]
    )

    assert answer.status_code == 422, answer.text
    assert named in answer.json()["detail"]


def test_a_target_of_more_than_one_token_is_refused(client):
    answer = generate(client, target="hello world")

    assert answer.status_code == 422
    assert "2 tokens" in answer.json()["detail"]


def test_the_limits_are_the_deployments_and_say_which_setting(weights_dir, hub, factory):
    with lab(weights_dir, hub, factory, lab_max_new_tokens=4, lab_max_interventions=1) as client:
        settle(client)
        long = generate(client, max_new_tokens=5)
        many = generate(
            client, max_new_tokens=4, interventions=[edit(site="mlp_out", layer=0, op="zero")] * 2
        )

    assert long.status_code == many.status_code == 422
    assert "PRIMER_LAB_MAX_NEW_TOKENS" in long.json()["detail"]
    assert "PRIMER_LAB_MAX_INTERVENTIONS" in many.json()["detail"]


def test_the_stream_sends_the_baseline_then_the_edit_then_the_result(client):
    answer = generate(
        client, max_new_tokens=3, interventions=[edit(site="mlp_out", layer=0, op="zero")]
    )
    stream = events(answer.text)

    assert answer.headers["content-type"].startswith("text/event-stream")
    assert [event["id"] for event in stream] == list(range(len(stream)))
    tokens = [(event["run"], event["index"]) for event in stream if event["type"] == "token"]
    assert tokens == [("baseline", i) for i in range(3)] + [("edited", i) for i in range(3)]
    last = stream[-1]
    assert last["type"] == "completed"
    streamed = [event["token"] for event in stream if event["type"] == "token"]
    assert streamed == last["baseline"] + last["edited"]


def test_a_run_that_fails_once_streaming_ends_with_an_error_event(client, monkeypatch):
    def broken(*_: Any, **__: Any) -> None:
        raise RuntimeError("the pass failed")

    monkeypatch.setattr("primer_lab.editing.compare", broken)
    stream = events(generate(client).text)

    assert [event["type"] for event in stream] == ["error"]
    assert stream[0]["problem"]["code"] == "internal_error"
    assert "the pass failed" not in stream[0]["problem"]["detail"]


def test_generation_stops_between_steps_once_nobody_is_waiting(client):
    """A client that has gone should not hold the queue for the rest of its answer."""
    model = cast(FastAPI, client.app).state.host.resident.loaded.model
    asked: list[int] = []

    result = generate_tokens(
        model,
        model.tokenizer.encode(PROMPT),
        [],
        max_new_tokens=10,
        special=set(),
        cancelled=lambda: len(asked) >= 2,
        on_token=lambda index, _: asked.append(index),
    )

    assert asked == [0, 1]
    assert len(result.tokens) == 2


def edit_of(**fields: Any) -> Edit:
    defaults: dict[str, Any] = {"site": "resid_post", "layer": 0, "index": None, "value": 0.0}
    return cast(Any, Edit)(**{"positions": "all"} | defaults | fields)


def test_an_edit_at_one_component_leaves_the_others_as_they_were():
    rows = torch.arange(6.0).reshape(2, 3)

    edited = apply(edit_of(index=1, op="scale", value=10.0), rows, None)

    assert edited.tolist() == [[0.0, 10.0, 2.0], [3.0, 40.0, 5.0]]
    assert rows.tolist() == [[0.0, 1.0, 2.0], [3.0, 4.0, 5.0]], "the input was written to"


@pytest.mark.parametrize(
    ("fields", "expected"),
    [
        ({"op": "zero"}, [[0.0, 0.0], [0.0, 0.0]]),
        ({"op": "clamp", "index": 0, "value": 7.0}, [[7.0, 1.0], [7.0, 3.0]]),
        ({"op": "add", "value": 0.5}, [[0.5, 1.5], [2.5, 3.5]]),
        (
            {"op": "add", "value": 2.0, "vector": torch.tensor([1.0, -1.0])},
            [[2.0, -1.0], [4.0, 1.0]],
        ),
    ],
)
def test_each_operation_does_what_it_says(fields, expected):
    assert apply(edit_of(**fields), torch.arange(4.0).reshape(2, 2), None).tolist() == expected


def test_a_mean_edit_writes_the_mean_it_was_given_at_every_position():
    mean = torch.tensor([9.0, 8.0])

    assert apply(edit_of(op="mean"), torch.zeros(3, 2), mean).tolist() == [[9.0, 8.0]] * 3


@pytest.mark.parametrize(
    ("positions", "expected"),
    [
        ("all", [False, True, True, True, True]),
        ("prompt", [False, True, True, False, False]),
        ("generated", [False, False, False, True, True]),
        ((1, 4), [False, True, True, True, False]),
    ],
)
def test_positions_select_by_place_and_skip_template_tokens(positions, expected):
    """Position 0 is a special token: only a span that names it can edit it."""
    tokens = [99, 1, 2, 3, 4]
    chosen = [
        selected(positions, position=p, token=t, prompt_length=3, special={99})
        for p, t in enumerate(tokens)
    ]

    assert chosen == expected


def test_a_span_can_edit_a_template_token_by_naming_it():
    assert selected((0, 1), position=0, token=99, prompt_length=3, special={99})


@pytest.mark.parametrize(
    ("baseline", "edited", "at"),
    [([1, 2, 3], [1, 2, 3], None), ([1, 2, 3], [1, 5, 3], 1), ([1, 2], [1, 2, 3], 2)],
)
def test_the_answers_diverge_where_they_first_differ(baseline, edited, at):
    assert first_divergence(baseline, edited) == at


def test_a_mean_is_the_sites_average_over_the_prompts_text_tokens(client):
    """Read from the activation route, which is a pass with no edits in it."""
    model = cast(FastAPI, client.app).state.host.resident.loaded.model
    prompt = model.tokenizer.encode(PROMPT)
    special = {prompt[0]}
    mean_edit = edit_of(site="mlp_out", op="mean")

    (mean,) = prompt_means(model, prompt, [edit_of(op="zero"), mean_edit], special).items()

    rows = [
        client.post(
            "/api/v1/lab/activation",
            json={"prompt": PROMPT, "chat": False, "layer": 0, "token": t, "site": "mlp_out"},
        ).json()["values"]
        for t in range(1, len(prompt))
    ]
    assert mean[0] == 1, "the mean belongs to the edit at its place in the list"
    assert torch.allclose(mean[1], torch.tensor(rows).mean(0), atol=1e-6)
