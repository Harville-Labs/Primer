"""Naming the embedding model is enough to embed with it properly.

The prompts a model was trained with are invisible when missing: nothing
fails, search is just worse. So these pin that they reach the embedder, and
that the one setting that cannot be guessed - the vector width - is refused
at startup rather than discovered by the first document indexed.
"""

from __future__ import annotations

import pytest
from primer_retrieval.config import Settings
from primer_retrieval.pipelines import build_document_embedder, build_text_embedder
from pydantic import ValidationError


def test_a_known_model_brings_its_width_and_prompts() -> None:
    settings = Settings(embedding_model="nomic-embed-text")

    assert settings.vector_dimensions == 768
    assert settings.embedding_query_prefix == "search_query: "
    assert settings.embedding_document_prefix == "search_document: "


def test_the_prompts_reach_the_embedders() -> None:
    """Resolved and then not passed on would be the same bug, harder to see.

    Queries and passages get different prompts, and swapping them is as
    quiet a failure as omitting them, so each side is checked for its own.
    """
    settings = Settings(embedding_model="nomic-embed-text")

    assert build_text_embedder(settings).prefix == "search_query: "  # ty: ignore[unresolved-attribute]
    assert build_document_embedder(settings).prefix == "search_document: "  # ty: ignore[unresolved-attribute]


def test_a_model_that_wants_no_passage_prompt_gets_none() -> None:
    """Qwen embeds documents bare; a stray prefix would shift every vector."""
    settings = Settings(embedding_model="Qwen/Qwen3-Embedding-0.6B")

    assert settings.embedding_query_prefix is not None
    assert settings.embedding_query_prefix.startswith("Instruct: ")
    assert settings.embedding_document_prefix == ""


def test_an_explicit_setting_overrides_the_table() -> None:
    """A deployment serving a truncated Matryoshka width has to be able to say so."""
    settings = Settings(
        embedding_model="Qwen/Qwen3-Embedding-0.6B",
        embedding_dimensions=512,
        embedding_query_prefix="Instruct: Find the clause\nQuery:",
    )

    assert settings.vector_dimensions == 512
    assert settings.embedding_query_prefix == "Instruct: Find the clause\nQuery:"


def test_an_unknown_model_must_state_its_width() -> None:
    """Otherwise the vector column is made at the wrong width and the first
    document indexed fails, naming neither the model nor the setting."""
    with pytest.raises(ValidationError) as raised:
        Settings(embedding_model="someone/their-own-finetune")

    message = str(raised.value)
    assert "someone/their-own-finetune" in message
    assert "PRIMER_EMBEDDING_DIMENSIONS" in message


def test_an_unknown_model_with_a_width_embeds_without_prompts() -> None:
    settings = Settings(embedding_model="someone/their-own-finetune", embedding_dimensions=1024)

    assert settings.vector_dimensions == 1024
    assert settings.embedding_query_prefix is None
    assert build_text_embedder(settings).prefix == ""  # ty: ignore[unresolved-attribute]


def test_an_empty_variable_means_use_the_models_own(monkeypatch: pytest.MonkeyPatch) -> None:
    """Compose passes an optional setting as "" rather than leaving it out."""
    monkeypatch.setenv("PRIMER_EMBEDDING_MODEL", "BAAI/bge-small-en-v1.5")
    monkeypatch.setenv("PRIMER_EMBEDDING_DIMENSIONS", "")

    assert Settings().vector_dimensions == 384
