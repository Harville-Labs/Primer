"""Saving experiments, against a real PostgreSQL: what is kept, and for whom."""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient
from lab_owned import make, train
from lab_streams import PROMPT, completed, generate
from lab_support import as_user, build_tiny_model, lab, settle
from primer_service.db import Database, as_sync_url
from sqlalchemy import create_engine, text

ADA = as_user("ada")
BO = as_user("bo")
EXPERIMENTS = "/api/v1/lab/experiments"


def stored(url: str) -> int:
    """Rows in the table, read past the API so nothing it filters can hide one."""
    engine = create_engine(as_sync_url(url))
    try:
        with engine.connect() as connection:
            return connection.execute(text("SELECT count(*) FROM lab.experiments")).scalar_one()
    finally:
        engine.dispose()


def make_vector(client: TestClient, headers: dict[str, str]) -> str:
    answer = make(client, headers=headers)
    assert answer.status_code == 201, answer.text
    return answer.json()["id"]


def train_probe(client: TestClient, headers: dict[str, str]) -> str:
    answer = train(client, headers=headers, layer=0)
    assert answer.status_code == 201, answer.text
    return answer.json()["id"]


def steered(vector: str) -> dict[str, Any]:
    intervention = {"site": "resid_post", "layer": 1, "op": "add", "value": 4.0}
    return {
        "prompt": PROMPT,
        "chat": False,
        "max_new_tokens": 6,
        "interventions": [intervention | {"vector_id": vector}],
    }


def save_neurons(client: TestClient, headers: dict[str, str], title: str = "steered") -> dict:
    """Make a vector, generate with it, and save that run."""
    request = steered(make_vector(client, headers))
    result = completed(client, headers=headers, **request)
    run = {"kind": "neurons", "request": request, "result": result}
    answer = client.post(EXPERIMENTS, json={"title": title, "run": run}, headers=headers)
    assert answer.status_code == 201, answer.text
    return answer.json()


def switch(client: TestClient, path: str, headers: dict[str, str]) -> None:
    answer = client.put(
        "/api/v1/lab/model", json={"model": {"source": "local", "path": path}}, headers=headers
    )
    assert answer.status_code == 202, answer.text
    settle(client)
    status = client.get("/api/v1/lab/status", headers=headers).json()
    assert status["model"]["reference"]["path"] == path


def test_lab_without_a_database_says_it_cannot_save(client):
    """Lab runs without one; saving is the only thing missing, and it says so."""
    assert client.get("/api/v1/lab/status").json()["experiment_retention_days"] is None

    answer = client.get(EXPERIMENTS)

    assert answer.status_code == 503
    assert answer.json()["code"] == "dependency_unavailable"
    assert "PRIMER_DATABASE_URL" in answer.json()["detail"]


def test_an_unreachable_database_is_named_rather_than_a_server_error(weights_dir, hub, factory):
    """A transport error reaching a catch-all would read as a bug in Lab."""
    database = Database("postgresql://lab:lab@127.0.0.1:1/lab")
    with lab(weights_dir, hub, factory, database=database) as client:
        settle(client)
        answer = client.get(EXPERIMENTS)

    assert answer.status_code == 503
    assert answer.json()["code"] == "dependency_unavailable"
    assert "try again" in answer.json()["detail"]


def test_the_status_states_how_long_experiments_are_kept(saving):
    assert saving.get("/api/v1/lab/status", headers=ADA).json()["experiment_retention_days"] == 90


def test_nothing_is_stored_until_someone_saves(saving, empty_tables):
    """Running, making a vector, training a probe and scoring all keep nothing."""
    vector = make_vector(saving, ADA)
    probe = train_probe(saving, ADA)
    completed(saving, headers=ADA, **steered(vector))
    scored = saving.post(
        "/api/v1/lab/score",
        json={"prompt": PROMPT, "mode": "text", "probe_id": probe},
        headers=ADA,
    )
    assert scored.status_code == 200, scored.text

    assert stored(empty_tables) == 0
    assert saving.get(EXPERIMENTS, headers=ADA).json() == []


def test_a_saved_run_comes_back_as_it_was_sent(saving, empty_tables):
    saved = save_neurons(saving, ADA)

    opened = saving.get(f"{EXPERIMENTS}/{saved['id']}", headers=ADA).json()
    listed = saving.get(EXPERIMENTS, headers=ADA).json()

    assert stored(empty_tables) == 1
    assert opened == saved
    assert [entry["id"] for entry in listed] == [saved["id"]]
    assert opened["kind"] == "neurons"
    assert opened["model"]["reference"] == {"source": "local", "path": "tiny"}
    [vector] = opened["vectors"]
    assert vector["id"] == opened["run"]["request"]["interventions"][0]["vector_id"]
    assert "values" not in vector, "a vector's numbers stay in Lab"


def test_the_newest_experiment_is_listed_first(saving):
    first = save_neurons(saving, ADA, "first")
    second = save_neurons(saving, ADA, "second")

    listed = saving.get(EXPERIMENTS, headers=ADA).json()

    assert [entry["id"] for entry in listed] == [second["id"], first["id"]]


def test_one_person_cannot_read_change_or_delete_anothers_experiment(saving, empty_tables):
    """Its prompts may be private; to anyone else it does not exist."""
    saved = save_neurons(saving, ADA)
    at = f"{EXPERIMENTS}/{saved['id']}"

    attempts = [
        saving.get(at, headers=BO),
        saving.patch(at, json={"title": "mine now"}, headers=BO),
        saving.delete(at, headers=BO),
        saving.post(f"{at}/restore", headers=BO),
    ]

    assert [answer.status_code for answer in attempts] == [404, 404, 404, 404]
    assert saving.get(EXPERIMENTS, headers=BO).json() == []
    assert stored(empty_tables) == 1
    assert saving.get(at, headers=ADA).json()["title"] == saved["title"]
    assert saving.get("/api/v1/lab/vectors", headers=BO).json() == []


def test_an_experiment_can_be_renamed_and_deleted_by_its_owner(saving, empty_tables):
    saved = save_neurons(saving, ADA)
    at = f"{EXPERIMENTS}/{saved['id']}"

    renamed = saving.patch(at, json={"title": "  a better name "}, headers=ADA)
    assert renamed.status_code == 200, renamed.text
    assert saving.get(at, headers=ADA).json()["title"] == "a better name"

    assert saving.delete(at, headers=ADA).status_code == 204
    assert saving.get(at, headers=ADA).status_code == 404
    assert stored(empty_tables) == 0


def test_a_run_whose_vector_is_gone_is_refused_rather_than_saved_without_it(saving, empty_tables):
    """Saved without its vector, the run could never be repeated."""
    run = {"kind": "neurons", "request": steered("0" * 32)}

    answer = saving.post(EXPERIMENTS, json={"title": "orphan", "run": run}, headers=ADA)

    assert answer.status_code == 422
    assert stored(empty_tables) == 0


def test_someone_elses_vector_cannot_be_saved_into_an_experiment(saving, empty_tables):
    vector = make_vector(saving, ADA)
    run = {"kind": "neurons", "request": steered(vector)}

    answer = saving.post(EXPERIMENTS, json={"title": "taken", "run": run}, headers=BO)

    assert answer.status_code == 422
    assert stored(empty_tables) == 0


def test_restoring_puts_the_vector_back_so_the_run_repeats(saving, weights_dir):
    """A switch of model drops every vector; the saved one comes back under its id."""
    saved = save_neurons(saving, ADA)
    request = saved["run"]["request"]
    build_tiny_model(weights_dir / "other", seed=1)
    switch(saving, "other", ADA)
    switch(saving, "tiny", ADA)
    assert generate(saving, headers=ADA, **request).status_code == 422, "the vector is gone"

    restored = saving.post(f"{EXPERIMENTS}/{saved['id']}/restore", headers=ADA)

    assert restored.status_code == 200, restored.text
    again = completed(saving, headers=ADA, **request)
    assert again["edited"] == saved["run"]["result"]["edited"]
    assert [v["id"] for v in saving.get("/api/v1/lab/vectors", headers=ADA).json()] == [
        request["interventions"][0]["vector_id"]
    ]


def test_restoring_a_probe_reads_tokens_as_before(saving, weights_dir):
    probe = train_probe(saving, ADA)
    request = {"prompt": PROMPT, "mode": "text", "chat": False, "probe_id": probe}
    result = saving.post("/api/v1/lab/score", json=request, headers=ADA).json()
    run = {"kind": "anomalies", "request": request, "result": result}
    saved = saving.post(EXPERIMENTS, json={"title": "probe", "run": run}, headers=ADA).json()
    build_tiny_model(weights_dir / "other", seed=1)
    switch(saving, "other", ADA)
    switch(saving, "tiny", ADA)

    assert saving.post(f"{EXPERIMENTS}/{saved['id']}/restore", headers=ADA).status_code == 200

    again = saving.post("/api/v1/lab/score", json=request, headers=ADA).json()
    assert again["probe"]["probabilities"] == pytest.approx(result["probe"]["probabilities"])


def test_an_experiment_is_not_restored_onto_another_model(saving, weights_dir):
    """A vector is a direction in one model's residual stream and nobody else's."""
    saved = save_neurons(saving, ADA)
    build_tiny_model(weights_dir / "other", seed=1)
    switch(saving, "other", ADA)

    answer = saving.post(f"{EXPERIMENTS}/{saved['id']}/restore", headers=ADA)

    assert answer.status_code == 409
    assert "tiny" in answer.json()["detail"]
    assert saving.get("/api/v1/lab/vectors", headers=ADA).json() == []


def test_a_result_from_a_model_lab_no_longer_holds_is_not_saved(saving, weights_dir):
    request = steered(make_vector(saving, ADA))
    result = completed(saving, headers=ADA, **request)
    build_tiny_model(weights_dir / "other", seed=1)
    switch(saving, "other", ADA)

    run = {"kind": "neurons", "request": request, "result": result}
    answer = saving.post(EXPERIMENTS, json={"title": "late", "run": run}, headers=ADA)

    assert answer.status_code == 409
