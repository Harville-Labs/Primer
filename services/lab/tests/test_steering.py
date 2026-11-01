"""Steering vectors: made from two sets of prompts, kept for whoever made them."""

from __future__ import annotations

from typing import Any, cast
from uuid import UUID, uuid4

import pytest
import torch
from fastapi import FastAPI
from fastapi.testclient import TestClient
from lab_streams import completed, generate
from lab_support import as_user
from primer_lab.owned import MAX_PER_OWNER, Owned
from primer_lab.steering import difference_of_means, made_vector
from primer_service.identity import LOCAL_SUBJECT, user_id_for_subject

POSITIVE = ["hello world", "w1 w2 w3"]
NEGATIVE = ["w9 w8", "w4 w5 w6 w7"]


def make(client: TestClient, *, headers: dict[str, str] | None = None, **body: Any) -> Any:
    request = {"label": "test", "layer": 1, "positive": POSITIVE, "negative": NEGATIVE}
    return client.post(
        "/api/v1/lab/vectors", json=request | {"chat": False} | body, headers=headers
    )


def last_residual(client: TestClient, prompt: str, layer: int) -> torch.Tensor:
    tokens = client.post(
        "/api/v1/lab/inspect", json={"prompt": prompt, "chat": False, "neurons": 1}
    ).json()["tokens"]
    request = {"prompt": prompt, "chat": False, "layer": layer, "token": len(tokens) - 1}
    answer = client.post("/api/v1/lab/activation", json=request | {"site": "resid_post"})
    return torch.tensor(answer.json()["values"])


def test_the_difference_of_means_is_the_positive_mean_minus_the_negative_one():
    positive = torch.tensor([[1.0, 2.0], [3.0, 4.0]])
    negative = torch.tensor([[0.0, 1.0]])

    assert difference_of_means(positive, negative).tolist() == [2.0, 2.0]


def test_a_vector_is_the_difference_of_the_residuals_at_each_prompts_last_token(client):
    """Checked against the activation route, which reads the residual its own way."""
    answer = make(client)
    assert answer.status_code == 201, answer.text

    expected = torch.stack([last_residual(client, p, 1) for p in POSITIVE]).mean(0) - torch.stack(
        [last_residual(client, p, 1) for p in NEGATIVE]
    ).mean(0)
    local = user_id_for_subject(LOCAL_SUBJECT)
    vector = cast(FastAPI, client.app).state.host.resident.vectors.owned_by(local)
    assert torch.allclose(vector[answer.json()["id"]], expected, atol=1e-5)
    assert answer.json()["norm"] == pytest.approx(float(expected.norm()), rel=1e-5)
    assert (answer.json()["positive"], answer.json()["negative"]) == (2, 2)


def test_adding_a_vector_moves_the_answer_in_proportion_to_its_coefficient(client):
    vector = make(client).json()["id"]

    def logit(value: float) -> dict[str, float]:
        intervention = {
            "site": "resid_post",
            "layer": 1,
            "op": "add",
            "vector_id": vector,
            "value": value,
        }
        return completed(client, interventions=[intervention], target="w3")["target"]

    none, once, twice = (logit(v)["edited_logit"] for v in (0.0, 1.0, 2.0))
    assert none == pytest.approx(logit(0.0)["baseline_logit"], abs=1e-6)
    assert once != pytest.approx(none, abs=1e-4)
    assert twice != pytest.approx(once, abs=1e-4)


def test_a_vector_at_a_layer_the_model_does_not_have_is_refused(client):
    answer = make(client, layer=2)

    assert answer.status_code == 422
    assert "0 to 1" in answer.json()["detail"]


def test_a_vector_is_listed_only_for_the_person_who_made_it(shared):
    made = make(shared, headers=as_user("ada")).json()

    mine = shared.get("/api/v1/lab/vectors", headers=as_user("ada")).json()
    theirs = shared.get("/api/v1/lab/vectors", headers=as_user("bo")).json()

    assert [vector["id"] for vector in mine] == [made["id"]]
    assert theirs == []


def test_someone_elses_vector_cannot_be_added_even_knowing_its_id(shared):
    """Its prompts may be private, and the vector is computed from nothing else."""
    vector = make(shared, headers=as_user("ada")).json()["id"]
    intervention = {"site": "resid_post", "layer": 1, "op": "add", "vector_id": vector}

    answer = generate(shared, interventions=[intervention], headers=as_user("bo"))

    assert answer.status_code == 422
    assert "no steering vector" in answer.json()["detail"]


def test_past_the_cap_the_oldest_vector_goes_and_only_the_owners():
    store: Owned[Any, torch.Tensor] = Owned()
    me, them = uuid4(), uuid4()

    def add(owner: UUID, label: str) -> Any:
        vector = torch.ones(2)
        summary = made_vector(vector, label=label, layer=0, positive=1, negative=1)
        return store.add(owner, summary, vector)

    theirs = add(them, "theirs")
    made = [add(me, str(i)) for i in range(MAX_PER_OWNER + 1)]

    assert [v.id for v in store.listed(me)] == [v.id for v in made[1:]]
    assert [v.id for v in store.listed(them)] == [theirs.id]
