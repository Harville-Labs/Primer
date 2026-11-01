"""The builder, run as Lab runs it: a request on stdin and a graph on stdout.

The model is a two-layer Qwen3 with sixteen-wide hidden states and random
weights, and each transcoder is 64 features wide, so a build takes well
under a second and needs no network.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest
import torch
from circuit_tracer.transcoder.activation_functions import JumpReLU
from circuit_tracer.transcoder.single_layer_transcoder import SingleLayerTranscoder
from primer_contracts.lab_graphs import BuiltGraph, GraphBuild
from tokenizers import Tokenizer, models, pre_tokenizers
from transformers import PreTrainedTokenizerFast, Qwen3Config, Qwen3ForCausalLM

LAYERS = 2
# The special tokens come last: circuit-tracer picks the token it prepends
# with `filter(None, ...)`, which passes over an id of 0.
WORDS = [f"w{i}" for i in range(29)] + ["<unk>", "<|endoftext|>"]
ENDOFTEXT = WORDS.index("<|endoftext|>")
PROMPT = [WORDS.index(w) for w in ("w3", "w7", "w7", "w1", "w2")]


@pytest.fixture(scope="module")
def tiny(tmp_path_factory) -> tuple[Path, list[str]]:
    root = tmp_path_factory.mktemp("tiny")
    torch.manual_seed(0)
    tokenizer = Tokenizer(
        models.WordLevel(vocab={w: i for i, w in enumerate(WORDS)}, unk_token="<unk>")
    )
    tokenizer.pre_tokenizer = pre_tokenizers.WhitespaceSplit()
    config = Qwen3Config(
        vocab_size=len(WORDS),
        hidden_size=16,
        intermediate_size=32,
        num_hidden_layers=LAYERS,
        num_attention_heads=2,
        num_key_value_heads=1,
        head_dim=8,
        max_position_embeddings=64,
        tie_word_embeddings=False,
    )
    Qwen3ForCausalLM(config).save_pretrained(root / "model")
    PreTrainedTokenizerFast(
        tokenizer_object=tokenizer, unk_token="<unk>", eos_token="<|endoftext|>"
    ).save_pretrained(root / "model")
    paths = []
    for layer in range(LAYERS):
        transcoder = SingleLayerTranscoder(16, 64, JumpReLU(torch.tensor(0.0), 0.1), layer)
        for parameter in transcoder.parameters():
            torch.nn.init.uniform_(parameter, -1, 1)
        path = root / f"layer_{layer}.safetensors"
        transcoder.to_safetensors(str(path))
        paths.append(str(path))
    return root / "model", paths


def request(tiny: tuple[Path, list[str]], **overrides) -> GraphBuild:
    model, transcoders = tiny
    fields = {
        "model_dir": str(model),
        "transcoders": transcoders,
        "scan": "lab/tiny-transcoders@0",
        "token_ids": PROMPT,
        "max_feature_nodes": 50,
        "max_logits": 3,
        "max_edges": 10_000,
        "node_threshold": 0.8,
        "edge_threshold": 0.98,
        "device": "cpu",
        "dtype": "float32",
    } | overrides
    return GraphBuild.model_validate(fields)


def run(body: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "primer_graphs"],
        input=body,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )


def built(tiny, **overrides) -> BuiltGraph:
    result = run(request(tiny, **overrides).model_dump_json())
    assert result.returncode == 0, result.stderr
    return BuiltGraph.model_validate_json(result.stdout)


def test_a_graph_reads_the_prompt_behind_the_token_circuit_tracer_puts_first(tiny):
    graph = built(tiny)
    assert graph.tokens == ["<|endoftext|>", "w3", "w7", "w7", "w1", "w2"]
    tokens = [n for n in graph.nodes if n.kind == "token"]
    assert [n.token for n in tokens] == graph.tokens


def test_every_edge_joins_two_nodes_in_the_graph_and_every_kind_appears(tiny):
    graph = built(tiny)
    ids = {n.id for n in graph.nodes}
    assert graph.edges
    assert all(e.source in ids and e.target in ids for e in graph.edges)
    assert {n.kind for n in graph.nodes} >= {"feature", "token", "logit"}
    assert all(n.layer is not None and n.layer < LAYERS for n in graph.nodes if n.kind == "feature")


def test_without_a_target_the_likeliest_tokens_are_explained(tiny):
    logits = [n for n in built(tiny).nodes if n.kind == "logit"]
    assert 1 <= len(logits) <= 3
    assert all(n.probability is not None for n in logits)


def test_a_target_explains_that_token_and_no_other(tiny):
    target = WORDS.index("w11")
    logits = [n for n in built(tiny, target_id=target).nodes if n.kind == "logit"]
    assert [(n.id, n.token) for n in logits] == [(f"l{target}", "w11")]


def test_edges_past_the_limit_are_dropped_weakest_first_and_counted(tiny):
    whole = built(tiny)
    assert len(whole.edges) > 5
    capped = built(tiny, max_edges=5)
    strongest = sorted((abs(e.weight) for e in whole.edges), reverse=True)[:5]
    assert [abs(e.weight) for e in capped.edges] == pytest.approx(strongest)
    assert capped.dropped_edges == len(whole.edges) - 5


def test_a_tighter_node_threshold_keeps_fewer_features(tiny):
    def features(threshold: float) -> int:
        return sum(n.kind == "feature" for n in built(tiny, node_threshold=threshold).nodes)

    assert features(0.3) < features(0.95)


def test_a_request_that_is_not_valid_fails_with_one_line_on_stderr():
    result = run('{"model_dir": ""}')
    assert result.returncode == 1
    assert result.stderr.startswith("the build request is not valid"), result.stderr
    assert result.stdout == ""


def test_a_missing_transcoder_file_fails_naming_the_error(tiny, tmp_path):
    result = run(
        request(
            tiny, transcoders=[str(tmp_path / "nowhere.safetensors")] * LAYERS
        ).model_dump_json()
    )
    assert result.returncode == 1
    assert result.stdout == ""
    assert result.stderr.strip(), "the failure should say what went wrong"
