"""Every way a named model is refused, and that refusal comes before cost."""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

import pytest
from primer_contracts.lab import HubModel, LocalModel
from primer_lab.config import Settings
from primer_lab.sources import FETCHED_PATTERNS, ModelUnavailable, resolve

TINY = "tiny"

REPO = "someone/tiny-qwen"


def settings_for(weights_dir: Path, **overrides: Any) -> Settings:
    return Settings(lab_weights_dir=weights_dir, **overrides)


def published(hub, tmp_path: Path, tiny_model: Path) -> Path:
    directory = tmp_path / "published"
    shutil.copytree(tiny_model, directory)
    hub.publish(REPO, directory)
    return directory


def test_an_operator_directory_resolves_with_its_architecture(weights_dir, hub):
    resolved = resolve(settings_for(weights_dir), LocalModel(path=TINY), hub)
    assert resolved.architecture == "qwen3"
    assert resolved.directory == (weights_dir / TINY).resolve()
    assert hub.described == []


@pytest.mark.parametrize("path", ["..", "../models/..", "/etc", "."])
def test_a_local_path_cannot_leave_the_weights_volume(weights_dir, hub, path):
    with pytest.raises(ModelUnavailable, match="not a directory inside"):
        resolve(settings_for(weights_dir), LocalModel(path=path), hub)


def test_a_symlink_out_of_the_weights_volume_is_followed_and_refused(
    weights_dir, hub, tmp_path, tiny_model
):
    """Checked on where the path leads, so a link planted in the volume
    cannot point Lab at a directory the operator never put there."""
    outside = tmp_path / "outside"
    shutil.copytree(tiny_model, outside)
    (weights_dir / "sneaky").symlink_to(outside)
    with pytest.raises(ModelUnavailable, match="not a directory inside"):
        resolve(settings_for(weights_dir), LocalModel(path="sneaky"), hub)


def test_the_download_cache_cannot_be_named_as_a_local_path(weights_dir, hub):
    (weights_dir / "hub" / "x").mkdir(parents=True)
    with pytest.raises(ModelUnavailable, match="download cache"):
        resolve(settings_for(weights_dir), LocalModel(path="hub/x"), hub)


def test_a_local_directory_holding_a_pickle_is_refused(weights_dir, hub):
    (weights_dir / TINY / "pytorch_model.bin").write_bytes(b"not really")
    with pytest.raises(ModelUnavailable, match="pickle"):
        resolve(settings_for(weights_dir), LocalModel(path=TINY), hub)


def test_a_repository_fetches_only_data_files(weights_dir, hub, tmp_path, tiny_model):
    """The pickle and the modelling code in a repository are never written
    to disk, so no later mistake can load them."""
    source = published(hub, tmp_path, tiny_model)
    (source / "pytorch_model.bin").write_bytes(b"pickle payload")
    (source / "modeling_tiny.py").write_text("import os")

    resolved = resolve(settings_for(weights_dir), HubModel(repository=REPO), hub)

    assert resolved.architecture == "qwen3"
    assert resolved.commit is not None
    names = {path.name for path in resolved.directory.iterdir()}
    assert "model.safetensors" in names
    assert "pytorch_model.bin" not in names
    assert "modeling_tiny.py" not in names
    assert all(
        patterns in (["config.json"], list(FETCHED_PATTERNS)) for *_, patterns in hub.downloaded
    )


def test_a_repository_is_fetched_by_the_commit_it_was_checked_at(
    weights_dir, hub, tmp_path, tiny_model
):
    published(hub, tmp_path, tiny_model)
    resolve(settings_for(weights_dir), HubModel(repository=REPO, revision="main"), hub)
    assert hub.described == [(REPO, "main")]
    commits = {commit for _, commit, _ in hub.downloaded}
    assert commits == {"0" * 39 + "1"}


def test_a_repository_outside_the_operators_patterns_is_refused_without_asking_the_hub(
    weights_dir, hub, tmp_path, tiny_model
):
    published(hub, tmp_path, tiny_model)
    settings = settings_for(weights_dir, lab_repository_patterns=("Qwen/*",))
    with pytest.raises(ModelUnavailable, match="matching Qwen/"):
        resolve(settings, HubModel(repository=REPO), hub)
    assert hub.described == []


def test_a_repository_over_the_size_limit_is_refused_before_downloading(
    weights_dir, hub, tmp_path, tiny_model
):
    published(hub, tmp_path, tiny_model)
    hub.sizes[REPO] = {"model.safetensors": 30_000_000_000}
    with pytest.raises(ModelUnavailable, match=r"30\.0 GB, over"):
        resolve(settings_for(weights_dir), HubModel(repository=REPO), hub)
    assert hub.downloaded == []


def test_a_repository_with_only_pickle_weights_is_refused_before_downloading(
    weights_dir, hub, tmp_path, tiny_model
):
    source = published(hub, tmp_path, tiny_model)
    (source / "model.safetensors").rename(source / "pytorch_model.bin")
    with pytest.raises(ModelUnavailable, match="no safetensors"):
        resolve(settings_for(weights_dir), HubModel(repository=REPO), hub)
    assert hub.downloaded == []


def test_a_repository_needing_remote_code_is_refused_after_only_its_config(
    weights_dir, hub, tmp_path, tiny_model
):
    source = published(hub, tmp_path, tiny_model)
    config = json.loads((source / "config.json").read_text())
    config["auto_map"] = {"AutoModelForCausalLM": "modeling_tiny.TinyForCausalLM"}
    (source / "config.json").write_text(json.dumps(config))
    with pytest.raises(ModelUnavailable, match="never runs remote code"):
        resolve(settings_for(weights_dir), HubModel(repository=REPO), hub)
    assert [patterns for *_, patterns in hub.downloaded] == [["config.json"]]


def test_a_missing_repository_says_so(weights_dir, hub):
    with pytest.raises(ModelUnavailable, match="no public repository called nobody/none"):
        resolve(settings_for(weights_dir), HubModel(repository="nobody/none"), hub)
