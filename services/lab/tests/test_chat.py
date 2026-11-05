"""Chatting with the resident model while an edit steers every token it writes.

A steered conversation is a comparison like `/generate`'s, made from a
conversation rendered through the chat template rather than one prompt.
These tests use the tiny model with a template, whose markers are `<eos>`.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any, cast

import pytest
import torch
from fastapi.testclient import TestClient
from lab_streams import events
from lab_support import lab, settle, switch_to_chat_model
from primer_contracts.lab import MAX_CHAT_TURNS
from transformers import Qwen3ForCausalLM

CONVERSATION = [
    {"role": "user", "content": "hello world w3"},
    {"role": "assistant", "content": "w7 w7"},
    {"role": "user", "content": "w12 world"},
]
#: Pushes one MLP neuron at the last layer far from anything it does on its
#: own, which is the edit a person steering a model by a neuron makes.
SHIFT = {"site": "mlp_neuron", "layer": 1, "index": 2, "op": "add", "value": 200.0}


@pytest.fixture
def chatty(weights_dir, hub, factory) -> Iterator[TestClient]:
    with lab(weights_dir, hub, factory) as client:
        settle(client)
        switch_to_chat_model(client, weights_dir)
        yield client


def chat(client: TestClient, **body: Any) -> Any:
    request = {"messages": CONVERSATION, "max_new_tokens": 6} | body
    return client.post("/api/v1/lab/chat", json=request)


def replied(client: TestClient, **body: Any) -> dict[str, Any]:
    """The `completed` event of a reply that is expected to succeed."""
    answer = chat(client, **body)
    assert answer.status_code == 200, answer.text
    last = events(answer.text)[-1]
    assert last["type"] == "completed", last
    return last


def ids(tokens: list[dict[str, Any]]) -> list[int]:
    return [token["id"] for token in tokens]


def test_the_whole_conversation_is_read_through_the_chat_template(chatty):
    """Every turn, the assistant's included, is in what the model read, each in its markers."""
    last = replied(chatty)

    text = [token["text"] for token in last["prompt"]]
    assert text == [
        *["<eos>", "hello", "world", "w3", "<eos>"],
        *["<eos>", "w7", "w7", "<eos>"],
        *["<eos>", "w12", "world", "<eos>"],
    ]


def test_with_the_edits_cleared_the_reply_is_the_models_own(chatty, weights_dir):
    """Clearing the steering has to give back the model untouched, not a weaker edit."""
    last = replied(chatty, interventions=[])

    # transformers types `generate` against a protocol its own models do not satisfy.
    reference = cast(Any, Qwen3ForCausalLM.from_pretrained(weights_dir / "chatty"))
    prompt = torch.tensor([ids(last["prompt"])])
    expected = reference.generate(prompt, max_new_tokens=6, do_sample=False)[0, prompt.shape[1] :]
    assert ids(last["baseline"]) == expected.tolist()
    assert last["edited"] == last["baseline"]
    assert last["diverged_at"] is None


def test_shifting_a_neuron_changes_the_reply_and_leaves_the_unsteered_one_alone(chatty):
    plain = replied(chatty, interventions=[])
    steered = replied(chatty, interventions=[SHIFT])

    assert steered["edited"] != steered["baseline"]
    assert steered["diverged_at"] is not None
    # The comparison is only fair if steering one reply did not touch the other.
    assert steered["baseline"] == plain["baseline"]


def test_clamping_a_neuron_steers_the_reply_too(chatty):
    clamp = SHIFT | {"op": "clamp", "value": -200.0}
    steered = replied(chatty, interventions=[clamp])

    assert steered["edited"] != steered["baseline"]


def test_the_reply_streams_both_runs_as_generate_does(chatty):
    stream = events(chat(chatty, interventions=[SHIFT]).text)

    runs = [event["run"] for event in stream if event["type"] == "token"]
    assert runs, "the reply streamed no tokens"
    assert set(runs) == {"baseline", "edited"}
    assert runs.index("edited") > max(i for i, run in enumerate(runs) if run == "baseline")
    assert stream[-1]["type"] == "completed"


def test_a_model_without_a_chat_template_is_refused(client):
    """Without one, a "reply" would only continue the user's words."""
    answer = chat(client)

    assert answer.status_code == 422, answer.text
    assert answer.json()["title"] == "This model cannot chat"


@pytest.mark.parametrize(
    "messages",
    [
        [],
        CONVERSATION[:2],
        [{"role": "system", "content": "w1"}, CONVERSATION[0]],
        [{"role": "user", "content": ""}],
        [{"role": "user", "content": "w1"}] * (MAX_CHAT_TURNS + 1),
    ],
    ids=["empty", "ends-with-assistant", "unknown-role", "empty-turn", "too-many-turns"],
)
def test_a_conversation_that_cannot_be_replied_to_is_refused(chatty, messages):
    assert chat(chatty, messages=messages).status_code == 422


@pytest.mark.parametrize("value", [65_505.0, -1e9])
def test_an_amount_past_half_precisions_range_is_refused(chatty, value):
    """Past it, a half-precision model writes infinity, and every logit after is NaN."""
    assert chat(chatty, interventions=[SHIFT | {"value": value}]).status_code == 422


@pytest.mark.parametrize(
    ("intervention", "named"),
    [
        (SHIFT | {"layer": 2}, "layers are 0 to 1"),
        (SHIFT | {"index": 32}, "no neuron 32 at mlp_neuron. They are 0 to 31"),
        ({"site": "sae_feature", "layer": 1, "index": 0, "op": "add"}, "no SAEs ready"),
    ],
)
def test_a_neuron_or_feature_the_model_lacks_is_refused_naming_what_there_is(
    chatty, intervention, named
):
    answer = chat(chatty, interventions=[intervention])

    assert answer.status_code == 422, answer.text
    assert named in answer.json()["detail"]


def test_the_conversation_and_reply_are_held_to_the_deployments_limits(weights_dir, hub, factory):
    with lab(weights_dir, hub, factory, lab_max_prompt_tokens=12, lab_max_new_tokens=4) as client:
        settle(client)
        switch_to_chat_model(client, weights_dir)
        long = chat(client, max_new_tokens=4)
        short = chat(client, messages=CONVERSATION[:1], max_new_tokens=4)
        wordy = chat(client, messages=CONVERSATION[:1], max_new_tokens=5)

    assert long.status_code == wordy.status_code == 422
    assert short.status_code == 200, short.text
    assert "13 tokens with the chat template" in long.json()["detail"]
    assert "PRIMER_LAB_MAX_PROMPT_TOKENS" in long.json()["detail"]
    assert "PRIMER_LAB_MAX_NEW_TOKENS" in wordy.json()["detail"]
