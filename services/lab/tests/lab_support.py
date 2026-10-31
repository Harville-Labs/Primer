"""Helpers the Lab tests share that are not fixtures.

A module of its own rather than `conftest`, because every service has a
`conftest` and an import of that name resolves to whichever one the type
checker finds first.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, cast

import torch
from fastapi import FastAPI
from fastapi.testclient import TestClient
from primer_contracts.lab import LocalModel
from primer_lab.app import create_app
from primer_lab.config import Settings
from tokenizers import Tokenizer, models, pre_tokenizers
from transformers import PreTrainedTokenizerFast, Qwen3Config, Qwen3ForCausalLM

TINY = "tiny"


def build_tiny_model(
    directory: Path,
    *,
    seed: int = 0,
    layers: int = 2,
    positions: int = 64,
    chat_template: str | None = None,
) -> Path:
    vocab = {f"w{i}": i for i in range(60)} | {"<unk>": 60, "<eos>": 61, "hello": 62, "world": 63}
    tokenizer = Tokenizer(models.WordLevel(vocab=vocab, unk_token="<unk>"))
    tokenizer.pre_tokenizer = pre_tokenizers.Whitespace()
    config = Qwen3Config(
        vocab_size=64,
        hidden_size=16,
        intermediate_size=32,
        num_hidden_layers=layers,
        num_attention_heads=2,
        num_key_value_heads=1,
        head_dim=8,
        max_position_embeddings=positions,
        eos_token_id=61,
        pad_token_id=61,
    )
    torch.manual_seed(seed)
    Qwen3ForCausalLM(config).save_pretrained(directory, safe_serialization=True)
    fast = PreTrainedTokenizerFast(
        tokenizer_object=tokenizer, unk_token="<unk>", eos_token="<eos>", pad_token="<eos>"
    )
    fast.chat_template = chat_template
    fast.save_pretrained(directory)
    return directory


def lab(weights_dir: Path, hub, factory, **overrides: Any) -> TestClient:
    """Lab on CPU, starting on the tiny model in `weights_dir`."""
    settings = Settings(
        lab_weights_dir=weights_dir,
        lab_model=LocalModel(path=TINY),
        lab_device="cpu",
        **overrides,
    )
    return TestClient(create_app(settings, hub=hub, factory=factory, cuda_available=lambda: False))


def settle(client: TestClient) -> dict:
    app = cast(FastAPI, client.app)
    assert client.portal is not None, "settle needs a client inside its `with`"
    client.portal.call(app.state.host.wait)
    return client.get("/api/v1/lab/status").json()
