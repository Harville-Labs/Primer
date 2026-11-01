"""Helpers the Lab tests share that are not fixtures.

A module of its own rather than `conftest`, because every service has a
`conftest` and an import of that name resolves to whichever one the type
checker finds first.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from pathlib import Path
from typing import Any, cast

import torch
from fastapi import FastAPI
from fastapi.testclient import TestClient
from primer_contracts.lab import HubModel, LocalModel
from primer_lab.app import create_app
from primer_lab.config import Settings
from tokenizers import Tokenizer, models, pre_tokenizers
from transformers import PreTrainedTokenizerFast, Qwen3Config, Qwen3ForCausalLM

TINY = "tiny"
#: The tiny model published to the fake hub, and the SAEs mapped to it.
MODEL = "lab/tiny"
SAES = "lab/tiny-saes"


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


def build_saes(
    directory: Path,
    *,
    layers: Iterable[int] = (0, 1),
    d_model: int = 16,
    d_sae: int = 64,
    k: int = 8,
    seed: int = 0,
    **config: Any,
) -> Path:
    """An SAE repository laid out as Qwen-Scope's is, small enough for a test.

    It includes a Python file, as the real one does, so a test can prove
    Lab never downloads it.
    """
    directory.mkdir(parents=True, exist_ok=True)
    layers = list(layers)
    settings = {
        "model_type": "topk_sae",
        "d_model": d_model,
        "d_sae": d_sae,
        "k": k,
        "num_layers": 2,
        "layers": layers,
        "hook_point": "resid_post",
        "dtype": "float32",
    }
    (directory / "config.json").write_text(json.dumps(settings | config))
    (directory / "app.py").write_text("raise SystemExit('Lab downloaded the demo application')\n")
    generator = torch.Generator().manual_seed(seed)
    for layer in layers:
        torch.save(
            {
                "W_enc": torch.randn(d_sae, d_model, generator=generator),
                "W_dec": torch.randn(d_model, d_sae, generator=generator),
                "b_enc": torch.randn(d_sae, generator=generator),
                "b_dec": torch.randn(d_model, generator=generator),
            },
            directory / f"layer{layer}.sae.pt",
        )
    return directory


def lab(weights_dir: Path, hub, factory, **overrides: Any) -> TestClient:
    """Lab on CPU, starting on the tiny model in `weights_dir`."""
    defaults: dict[str, Any] = {
        "lab_weights_dir": weights_dir,
        "lab_model": LocalModel(path=TINY),
        "lab_device": "cpu",
    }
    settings = Settings(**defaults | overrides)
    return TestClient(create_app(settings, hub=hub, factory=factory, cuda_available=lambda: False))


def lab_with_saes(weights_dir: Path, hub, factory, **overrides: Any) -> TestClient:
    return lab(
        weights_dir,
        hub,
        factory,
        lab_model=HubModel(repository=MODEL),
        lab_saes={MODEL: SAES},
        **overrides,
    )


def settle(client: TestClient) -> dict:
    app = cast(FastAPI, client.app)
    assert client.portal is not None, "settle needs a client inside its `with`"
    client.portal.call(app.state.host.wait)
    return client.get("/api/v1/lab/status").json()


def as_user(name: str) -> dict[str, str]:
    """Headers as the proxy sets them, for a Lab running with authentication on."""
    return {"X-Forwarded-User": name}
