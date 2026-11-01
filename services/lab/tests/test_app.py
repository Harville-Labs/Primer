"""The service end to end, against a real model small enough to build."""

from __future__ import annotations

import shutil

import pytest
from fastapi.testclient import TestClient
from lab_support import TINY, lab, settle
from primer_lab.app import create_app
from primer_lab.config import Settings
from primer_lab.loader import DeviceUnavailable

REPO = "someone/tiny-qwen"


def test_the_startup_model_loads_and_is_reported_ready(client):
    status = client.get("/api/v1/lab/status").json()
    assert status["ready"] is True
    assert status["switch"] is None
    assert status["model"]["reference"] == {"source": "local", "path": TINY}
    assert status["model"]["architecture"] == "qwen3"
    assert status["model"]["num_layers"] == 2
    assert status["can_choose_model"] is True


def test_every_load_refuses_remote_code_and_pickles_and_the_network(client, factory):
    assert factory.calls, "nothing was loaded, so nothing was checked"
    for _, options in factory.calls:
        assert options["trust_remote_code"] is False
        assert options["use_safetensors"] is True
        assert options["local_files_only"] is True


def test_a_user_can_switch_to_a_repository_they_name(client, hub, make_model):
    hub.publish(REPO, make_model("published"))
    answer = client.put("/api/v1/lab/model", json={"model": {"source": "hub", "repository": REPO}})
    assert answer.status_code == 202
    status = settle(client)
    assert status["switch"] is None
    assert status["model"]["reference"] == {"source": "hub", "repository": REPO, "revision": None}
    assert status["model"]["commit"] == "0" * 39 + "1"


def test_a_failed_switch_says_why_and_keeps_the_previous_model(client, weights_dir, factory):
    shutil.copytree(weights_dir / TINY, weights_dir / "broken")
    factory.fail_for.add("broken")
    client.put("/api/v1/lab/model", json={"model": {"source": "local", "path": "broken"}})
    status = settle(client)
    assert status["switch"]["state"] == "failed"
    assert "could not be loaded" in status["switch"]["error"]
    assert status["ready"] is True
    assert status["model"]["reference"]["path"] == TINY


def test_a_refused_model_is_reported_in_the_status_without_losing_the_resident_one(
    client, weights_dir
):
    (weights_dir / "pickled").mkdir()
    shutil.copy(weights_dir / TINY / "config.json", weights_dir / "pickled")
    (weights_dir / "pickled" / "model.ckpt").write_bytes(b"x")
    client.put("/api/v1/lab/model", json={"model": {"source": "local", "path": "pickled"}})
    status = settle(client)
    assert "pickle" in status["switch"]["error"]
    assert status["model"]["reference"]["path"] == TINY


def test_a_repository_outside_the_patterns_is_refused_on_the_request(weights_dir, hub, factory):
    with lab(weights_dir, hub, factory, lab_repository_patterns=("Qwen/*",)) as client:
        settle(client)
        answer = client.put(
            "/api/v1/lab/model", json={"model": {"source": "hub", "repository": REPO}}
        )
    assert answer.status_code == 422
    assert "Qwen/*" in answer.json()["detail"]
    assert hub.described == []


def test_where_the_operator_chooses_users_cannot_switch(weights_dir, hub, factory):
    with lab(weights_dir, hub, factory, lab_model_choice="operator") as client:
        status = settle(client)
        answer = client.put("/api/v1/lab/model", json={"model": {"source": "local", "path": TINY}})
    assert status["can_choose_model"] is False
    assert answer.status_code == 403


def test_a_startup_model_that_fails_leaves_the_service_up_to_choose_another(tmp_path, hub, factory):
    """An unready pod leaves its Service, and then nobody could reach Lab
    to pick a model that works."""
    with lab(tmp_path / "empty", hub, factory) as client:
        status = settle(client)
        assert client.get("/health/ready").status_code == 200
    assert status["ready"] is False
    assert status["switch"]["state"] == "failed"


def test_a_gpu_that_was_asked_for_and_is_missing_refuses_startup(weights_dir, hub, factory):
    settings = Settings(lab_weights_dir=weights_dir, lab_device="cuda")
    app = create_app(settings, hub=hub, factory=factory, cuda_available=lambda: False)
    with pytest.raises(DeviceUnavailable, match="PRIMER_LAB_DEVICE=cpu"), TestClient(app):
        pass
