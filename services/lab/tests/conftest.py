"""A model small enough to build in a fixture, and a hub that records.

The model is a real Qwen3 with two layers and a sixteen-wide residual
stream, saved the way a published one is. That makes the load path the
real one - transformers, nnsight, nnterp - in well under a second, which
a fake model would not prove anything about.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

import httpx
import pytest
import torch
from huggingface_hub.errors import RepositoryNotFoundError
from huggingface_hub.utils import filter_repo_objects
from nnterp import StandardizedTransformer
from primer_lab.sources import Listing
from tokenizers import Tokenizer, models, pre_tokenizers
from transformers import PreTrainedTokenizerFast, Qwen3Config, Qwen3ForCausalLM

TINY = "tiny"


def build_tiny_model(directory: Path, *, seed: int = 0) -> Path:
    vocab = {f"w{i}": i for i in range(60)} | {"<unk>": 60, "<eos>": 61, "hello": 62, "world": 63}
    tokenizer = Tokenizer(models.WordLevel(vocab=vocab, unk_token="<unk>"))
    tokenizer.pre_tokenizer = pre_tokenizers.Whitespace()
    config = Qwen3Config(
        vocab_size=64,
        hidden_size=16,
        intermediate_size=32,
        num_hidden_layers=2,
        num_attention_heads=2,
        num_key_value_heads=1,
        head_dim=8,
        max_position_embeddings=64,
        eos_token_id=61,
        pad_token_id=61,
    )
    torch.manual_seed(seed)
    Qwen3ForCausalLM(config).save_pretrained(directory, safe_serialization=True)
    PreTrainedTokenizerFast(
        tokenizer_object=tokenizer, unk_token="<unk>", eos_token="<eos>", pad_token="<eos>"
    ).save_pretrained(directory)
    return directory


@pytest.fixture(scope="session")
def tiny_model(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return build_tiny_model(tmp_path_factory.mktemp("tiny"))


@pytest.fixture
def weights_dir(tmp_path: Path, tiny_model: Path) -> Path:
    """A weights volume holding the tiny model as an operator's directory."""
    root = tmp_path / "models"
    shutil.copytree(tiny_model, root / TINY)
    return root


class FakeHub:
    """Serves repositories from local directories, and remembers every ask.

    `download` honours `patterns` the way the real hub does, so a test that
    plants a pickle in a repository can prove Lab never asked for it.
    """

    def __init__(self, cache: Path) -> None:
        self.repositories: dict[str, Path] = {}
        self.sizes: dict[str, dict[str, int | None]] = {}
        self.described: list[tuple[str, str | None]] = []
        self.downloaded: list[tuple[str, str, list[str]]] = []
        self._cache = cache

    def publish(self, repository: str, directory: Path) -> None:
        self.repositories[repository] = directory

    def _files(self, repository: str) -> dict[str, int | None]:
        directory = self.repositories[repository]
        actual = {
            str(path.relative_to(directory)): path.stat().st_size
            for path in directory.rglob("*")
            if path.is_file()
        }
        return actual | self.sizes.get(repository, {})

    def describe(self, repository: str, revision: str | None) -> Listing:
        self.described.append((repository, revision))
        if repository not in self.repositories:
            raise RepositoryNotFoundError(f"{repository} not found", response=_response(404))
        return Listing(commit=f"{'0' * 39}1", files=self._files(repository))

    def download(self, repository: str, commit: str, patterns: list[str]) -> Path:
        self.downloaded.append((repository, commit, patterns))
        source = self.repositories[repository]
        target = self._cache / repository.replace("/", "--") / commit
        for name in filter_repo_objects(self._files(repository), allow_patterns=patterns):
            (target / name).parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source / name, target / name)
        return target


def _response(status: int) -> httpx.Response:
    return httpx.Response(status, request=httpx.Request("GET", "https://hub.invalid"))


@pytest.fixture
def hub(weights_dir: Path) -> FakeHub:
    return FakeHub(weights_dir / "hub")


class RecordingFactory:
    """Builds the real model, and keeps the arguments it was asked with."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.fail_for: set[str] = set()

    def __call__(self, path: str, **options: Any) -> Any:
        self.calls.append((path, options))
        if Path(path).name in self.fail_for:
            raise ValueError("nnterp cannot map this architecture")
        return StandardizedTransformer(path, **options)


@pytest.fixture
def factory() -> RecordingFactory:
    return RecordingFactory()


@pytest.fixture
def make_model(tmp_path: Path):
    """Builds another tiny model, different from the first by its seed."""

    def make(name: str, *, seed: int = 1) -> Path:
        return build_tiny_model(tmp_path / name, seed=seed)

    return make
