"""Retrieval configuration.

The vector backend is a deployment-wide choice, not a per-library or
per-request one. Mixing backends would mean two isolation implementations to
prove correct instead of one, and the conformance suite exists precisely so
that choosing either is not a change in behavior.
"""

from __future__ import annotations

from typing import Literal

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from primer_retrieval.graph import MAX_HOPS

VectorStore = Literal["pgvector", "qdrant"]


class Settings(BaseSettings):
    """Deployment configuration for the Retrieval service."""

    model_config = SettingsConfigDict(env_prefix="PRIMER_", extra="forbid")

    vector_store: VectorStore = "pgvector"

    #: pgvector keeps its tables in their own schema, away from Control's, so
    #: neither service's migrations touch the other's tables.
    database_url: str = Field(default="postgresql://primer:primer@localhost:5432/primer")
    vector_schema: str = Field(default="vectors")
    vector_table: str = Field(default="chunks")

    qdrant_url: str | None = Field(default=None)
    qdrant_index: str = Field(default="primer_chunks")

    #: Any OpenAI-compatible endpoint: a hosted model, or a local server.
    #: Primer never ships an embedding model of its own.
    embedding_model: str = Field(default="text-embedding-3-small")
    embedding_dimensions: int = Field(default=1536, gt=0)
    embedding_base_url: str | None = Field(default=None)
    embedding_api_key: SecretStr | None = Field(default=None)
    embedding_timeout_seconds: float = Field(default=30.0, gt=0)

    #: A reranker reads a question and a passage together and scores the
    #: pair, which a vector search cannot do: its two embeddings were made
    #: without knowing about each other. Off unless an endpoint is named, and
    #: a deployment without one behaves exactly as it did before.
    rerank_base_url: str | None = Field(default=None)
    rerank_model: str = Field(default="")
    rerank_api_key: SecretStr | None = Field(default=None)
    rerank_timeout_seconds: float = Field(default=30.0, gt=0)
    #: How many the vector search fetches before reranking. Wider than the
    #: answer needs on purpose: the passage that answers the question is
    #: often inside the first twenty and outside the first six, and finding
    #: it is the entire reason to rerank.
    rerank_candidates: int = Field(default=20, ge=1, le=200)

    #: Passages scoring below this are not returned at all, even when that
    #: means returning nothing.
    #:
    #: Off unless set, because the right value is a property of whatever
    #: produced the score and this service cannot know it. Cosine similarity
    #: from one embedding model is not comparable to cosine from another,
    #: and neither is comparable to a reranker's output - so a default here
    #: would either drop good passages on one deployment or admit everything
    #: on the next, and both failures are silent.
    #:
    #: Measure before setting one. Embed a question alongside a passage that
    #: answers it and a passage that does not, and put the floor between the
    #: two. For Qwen3-Embedding-0.6B, unrelated text measures around 0.19
    #: and a passage that answers the question around 0.63 to 0.69.
    min_score: float | None = Field(default=None, ge=0.0, le=1.0)

    #: Neo4j, holding the entities each passage mentions and the relations
    #: between them. Off unless a URL is named, and a deployment without one
    #: never opens a connection to it.
    graph_url: str | None = Field(default=None)
    graph_username: str = Field(default="neo4j")
    graph_password: SecretStr | None = Field(default=None)
    graph_database: str = Field(default="neo4j")
    graph_timeout_seconds: float = Field(default=30.0, gt=0)
    #: How far search walks from the entities a question names. One reaches
    #: passages about what those entities are directly related to; two
    #: reaches what is related to that, which is where a vector search has
    #: the least chance of finding anything on its own.
    graph_hops: int = Field(default=2, ge=0, le=MAX_HOPS)
    #: How many passages the graph contributes before fusion. Kept about as
    #: wide as the vector candidates so neither ranking drowns the other.
    graph_candidates: int = Field(default=20, ge=1, le=200)

    @property
    def reranking_enabled(self) -> bool:
        """Both are needed. An endpoint with no model cannot be called."""
        return bool(self.rerank_base_url and self.rerank_model)

    internal_api_token: SecretStr | None = Field(
        default=None,
        description="Shared credential for this cluster-internal API; unset denies it",
    )

    @model_validator(mode="after")
    def _qdrant_needs_a_url(self) -> Settings:
        """Fail at startup rather than on the first search.

        A Qdrant deployment with no URL cannot answer anything. Discovering
        that when a user asks their first question means the failure surfaces
        as a broken product rather than a broken deployment.
        """
        if self.vector_store == "qdrant" and not self.qdrant_url:
            raise ValueError("vector_store='qdrant' requires qdrant_url")
        return self
