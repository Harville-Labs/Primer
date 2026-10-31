"""The table Retrieval and the parse worker both read.

A wrong row here is not a crash anywhere. It is search that is quietly
worse, or chunks the embedder quietly truncates - so the shape of the table
is pinned rather than trusted.
"""

from __future__ import annotations

import pytest
from primer_contracts.embedding_models import KNOWN_MODELS, find_embedding_model, tokenizer_for


def test_the_table_is_not_empty() -> None:
    """Every other test here loops over it."""
    assert len(KNOWN_MODELS) > 0


def test_no_name_resolves_to_two_models() -> None:
    """A duplicated alias would make the later row unreachable, silently."""
    names = [name for model in KNOWN_MODELS for name in (model.model_id.lower(), *model.aliases)]
    assert len(names) == len(set(names))


def test_aliases_are_written_the_way_they_are_looked_up() -> None:
    """Lookup lowercases the name it is given; an alias with capitals never matches."""
    for model in KNOWN_MODELS:
        for alias in model.aliases:
            assert alias == alias.lower(), alias


@pytest.mark.parametrize(
    ("name", "model_id"),
    [
        ("Qwen/Qwen3-Embedding-0.6B", "Qwen/Qwen3-Embedding-0.6B"),
        ("qwen/qwen3-embedding-0.6b", "Qwen/Qwen3-Embedding-0.6B"),
        ("onnx-community/Qwen3-Embedding-0.6B-ONNX", "Qwen/Qwen3-Embedding-0.6B"),
        ("qwen3-embedding:0.6b", "Qwen/Qwen3-Embedding-0.6B"),
        ("nomic-embed-text", "nomic-ai/nomic-embed-text-v1.5"),
        ("nomic-embed-text:latest", "nomic-ai/nomic-embed-text-v1.5"),
    ],
)
def test_a_model_is_found_under_the_name_its_server_uses(name: str, model_id: str) -> None:
    """Ollama serves its own names and TEI serves repository ids.

    The operator types whichever their server answers to, and both have to
    reach the same prompts.
    """
    known = find_embedding_model(name)
    assert known is not None
    assert known.model_id == model_id


def test_the_recommended_model_carries_its_query_instruction() -> None:
    """The reason this table exists, for the model the documentation recommends.

    Qwen3-Embedding embeds documents bare and queries behind an instruction;
    without it retrieval loses a few points that nobody measures.
    """
    qwen = find_embedding_model("Qwen/Qwen3-Embedding-0.6B")
    assert qwen is not None
    assert qwen.query_prefix.startswith("Instruct: ")
    assert qwen.query_prefix.endswith("\nQuery:")
    assert qwen.document_prefix == ""


def test_an_unknown_model_is_not_guessed_at() -> None:
    assert find_embedding_model("someone/their-own-finetune") is None


def test_a_known_models_tokenizer_comes_from_the_table() -> None:
    """Ollama's name is not a repository, so it cannot be used as one."""
    assert tokenizer_for("nomic-embed-text") == "nomic-ai/nomic-embed-text-v1.5"


def test_an_unknown_repository_is_its_own_tokenizer() -> None:
    """Anything named `owner/model` is on the Hub and publishes one."""
    assert tokenizer_for("someone/their-own-finetune") == "someone/their-own-finetune"


def test_a_hosted_model_has_no_tokenizer_to_fetch() -> None:
    """Naming one anyway would stop the worker starting, rather than chunk worse."""
    assert tokenizer_for("text-embedding-3-small") is None
    assert tokenizer_for("embed-english-v3.0") is None
