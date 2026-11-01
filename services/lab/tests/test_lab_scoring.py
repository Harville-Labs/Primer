"""Scoring the resident model's surprise, against a real model small enough to build.

The reference is `transformers` itself, loaded separately from Lab, so a
score read from the wrong position - the logits at token i rather than the
ones before it - disagrees with something that did not share the mistake.
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any

import pytest
import torch
from fastapi.testclient import TestClient
from lab_support import TINY, build_tiny_model, lab, settle
from primer_lab import scoring
from transformers import Qwen3ForCausalLM

#: The tiny vocabulary is word-level: `wN` is token N, then these.
EOS, HELLO, WORLD = 61, 62, 63
PROMPT = "hello world w3 w7 w7 w12"
PROMPT_IDS = [HELLO, WORLD, 3, 7, 7, 12]
NAMES = {EOS: "<eos>", HELLO: "hello", WORLD: "world"}
TEMPLATE = "{% for m in messages %}<eos> {{ m['content'] }} <eos>{% endfor %}"


def score(client: TestClient, **body: Any) -> Any:
    return client.post("/api/v1/lab/score", json={"prompt": PROMPT, "mode": "text"} | body)


def name(token: int) -> str:
    return NAMES.get(token, f"w{token}")


def reference(weights_dir: Path) -> Qwen3ForCausalLM:
    return Qwen3ForCausalLM.from_pretrained(weights_dir / TINY)


def log_softmax(model: Qwen3ForCausalLM, ids: list[int]) -> torch.Tensor:
    with torch.no_grad():
        return model(torch.tensor([ids])).logits[0].double().log_softmax(dim=-1)


def entropy(row: torch.Tensor) -> float:
    return float(-(row.exp() * row).sum() / math.log(2))


@pytest.mark.parametrize("block", [scoring.BLOCK, 2], ids=["one block", "across blocks"])
def test_each_token_is_scored_by_the_prediction_before_it(client, weights_dir, monkeypatch, block):
    """Position i - 1 predicts token i; measuring in blocks must not move that."""
    monkeypatch.setattr(scoring, "BLOCK", block)
    answer = score(client)
    assert answer.status_code == 200, answer.text
    body = answer.json()

    rows = log_softmax(reference(weights_dir), PROMPT_IDS)
    tokens = body["score"]["tokens"]
    assert [token["token"] for token in tokens] == ["world", "w3", "w7", "w7", "w12"]
    for token, position in zip(tokens, range(1, len(PROMPT_IDS)), strict=True):
        row = rows[position - 1]
        assert token["logprob"] == pytest.approx(float(row[PROMPT_IDS[position]]), abs=1e-5)
        assert token["entropy_bits"] == pytest.approx(entropy(row), abs=1e-5)
    assert body["text"] == PROMPT


def test_entropy_is_labelled_exact(client):
    """The tab says "top-20 only" for an endpoint; nothing here may need saying."""
    body = score(client).json()

    assert body["score"]["estimate"] == "full"
    assert body["score"]["top_k"] is None
    assert all(token["truncated_entropy_bits"] is None for token in body["score"]["tokens"])


def test_special_tokens_are_not_scored(client):
    body = score(client, prompt="hello <eos> world w3").json()

    assert [token["token"] for token in body["score"]["tokens"]] == ["world", "w3"]


def test_text_is_scored_without_the_chat_template(client, weights_dir):
    """Through the template, the first word would be scored as if predicted from a marker."""
    build_tiny_model(weights_dir / "chatty", chat_template=TEMPLATE)
    client.put("/api/v1/lab/model", json={"model": {"source": "local", "path": "chatty"}})
    assert settle(client)["model"]["reference"]["path"] == "chatty"

    body = score(client, chat=True).json()

    assert [token["token"] for token in body["score"]["tokens"]][:1] == ["world"]


def test_one_token_is_refused_as_nothing_to_score(client):
    answer = score(client, prompt="hello")

    assert answer.status_code == 422
    assert answer.json()["title"] == "Nothing to score"


def test_a_generated_answer_is_the_greedy_one_and_only_it_is_scored(client, weights_dir):
    answer = score(client, mode="generate", chat=False, max_new_tokens=5)
    assert answer.status_code == 200, answer.text
    body = answer.json()

    model = reference(weights_dir)
    ids = list(PROMPT_IDS)
    for _ in range(5):
        token = int(log_softmax(model, ids)[-1].argmax())
        ids.append(token)
        if token == EOS:
            break
    written = [token for token in ids[len(PROMPT_IDS) :] if token != EOS]
    assert written, "the fixture's greedy answer is only an end of sequence"

    rows = log_softmax(model, ids)
    tokens = body["score"]["tokens"]
    assert [token["token"] for token in tokens] == [name(i) for i in written]
    expected = [
        float(rows[position - 1, ids[position]])
        for position in range(len(PROMPT_IDS), len(ids))
        if ids[position] != EOS
    ]
    assert [token["logprob"] for token in tokens] == pytest.approx(expected, abs=1e-5)
    assert body["text"] == " ".join(map(name, written))


def test_an_answer_longer_than_the_deployment_allows_is_refused(weights_dir, hub, factory):
    with lab(weights_dir, hub, factory, lab_max_new_tokens=4) as client:
        settle(client)
        answer = score(client, mode="generate", max_new_tokens=5)

    assert answer.status_code == 422
    assert "PRIMER_LAB_MAX_NEW_TOKENS" in answer.json()["detail"]
