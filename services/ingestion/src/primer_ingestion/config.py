"""Worker configuration.

Workers hold no database credentials. Everything they may do to a job goes
through the Control API, so the only secrets here are a broker URL and the
service credential that identifies this process to Control.
"""

from __future__ import annotations

from primer_contracts.embedding_models import find_embedding_model, tokenizer_for
from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Deployment configuration for an ingestion worker."""

    #: An empty variable is an unset one. Compose can only pass an optional
    #: setting through as `${VAR:-}`, which arrives as "" rather than absent,
    #: and "" for the extraction endpoint and model would otherwise turn
    #: extraction on with nowhere to send it.
    model_config = SettingsConfigDict(env_prefix="PRIMER_", extra="forbid", env_ignore_empty=True)

    broker_url: str = Field(
        default="amqp://guest:guest@localhost:5672//",
        description="RabbitMQ URL for ingestion queues",
    )
    control_url: str = Field(
        default="http://control-api:8000",
        description="Cluster-internal base URL of the Control API",
    )
    service_token: SecretStr | None = Field(
        default=None,
        description="Credential presented to the Control internal API",
    )

    request_timeout_seconds: float = Field(default=15.0, gt=0)
    #: Renewed well inside Control's lease so a slow stage is not mistaken
    #: for a dead worker.
    heartbeat_seconds: float = Field(default=60.0, gt=0)

    source_store_url: str = Field(
        default="file:///var/lib/primer/sources",
        description="The same fsspec URL Control writes uploads to",
    )
    max_source_bytes: int = Field(
        default=100 * 1024 * 1024,
        ge=1,
        description="Matches Control's upload limit; workers only ever read",
    )

    retrieval_url: str = Field(
        default="http://retrieval:8000",
        description="Cluster-internal base URL of the Retrieval service",
    )
    retrieval_token: SecretStr | None = Field(
        default=None,
        description="Credential for Retrieval; falls back to service_token when unset",
    )
    retrieval_timeout_seconds: float = Field(default=120.0, gt=0)
    index_batch_size: int = Field(
        default=64,
        gt=0,
        description="Chunks per index call; bounds embedding cost lost to one timeout",
    )

    enable_ocr: bool = Field(
        default=True,
        description="Read text from images and slides; off makes parsing faster and offline",
    )
    #: Pictures are read one at a time and each costs a full OCR pass, so a
    #: deck built entirely of screenshots is the slowest thing this worker
    #: does. The ceiling is a bound on that rather than a judgement about
    #: how many pictures a document should have: past it, the remaining
    #: images are left unread rather than the document left unfinished.
    max_pictures_per_document: int = Field(
        default=100,
        ge=0,
        description="How many embedded images to run OCR over before giving up on the rest",
    )

    #: The model Retrieval embeds with. Only the parse worker reads it, to
    #: find the tokenizer chunks are sized against - see `_derive_tokenizer`.
    embedding_model: str | None = Field(
        default=None,
        description="The embedding model's name, as Retrieval is configured with it",
    )
    #: Normally derived from `embedding_model`. Without one chunks are split
    #: on document structure alone, which is a markedly worse result rather
    #: than an equivalent one - see `build_chunker`.
    chunk_tokenizer: str | None = Field(
        default=None,
        description="Hugging Face id of the embedding model's tokenizer, so chunks fit it",
    )
    max_chunk_tokens: int = Field(
        default=512,
        gt=0,
        description="Token ceiling per chunk; ignored when no tokenizer is configured",
    )
    #: Chunking splits on structure first, and a document whose labels are
    #: recognised as headings ends up one value per chunk. Passages under
    #: this length are joined to their neighbours afterwards - see
    #: `_merge_fragments`. Zero switches the joining off.
    min_chunk_chars: int = Field(
        default=40,
        ge=0,
        description="Passages shorter than this are joined to the ones beside them",
    )
    max_chunks_per_document: int = Field(
        default=5000,
        gt=0,
        description="Refuse rather than silently index part of an enormous document",
    )
    parse_deadline_seconds: float = Field(
        default=900.0,
        gt=0,
        description="Budget for converting one document, checked between phases",
    )

    #: Off unless both are set. Any OpenAI-compatible endpoint that supports
    #: structured output; a small instruction-tuned model is enough, and it
    #: need not be the one Chat answers with.
    graph_extraction_base_url: str | None = Field(
        default=None,
        description="OpenAI-compatible base URL of the model that extracts the knowledge graph",
    )
    graph_extraction_model: str | None = Field(
        default=None,
        description="Model that extracts entities and relations; unset turns extraction off",
    )
    graph_extraction_api_key: SecretStr | None = Field(default=None)
    graph_extraction_timeout_seconds: float = Field(default=120.0, gt=0)
    #: Passages are extracted in parallel within a batch. Bounded by what the
    #: model server can take, not by this worker, which only waits.
    graph_extraction_concurrency: int = Field(
        default=4,
        gt=0,
        description="Passages sent to the extraction model at once",
    )

    max_retries: int = Field(
        default=4,
        ge=0,
        description="Celery retries per stage; Control holds an independent hard bound",
    )
    retry_backoff_seconds: float = Field(default=10.0, gt=0)
    retry_backoff_max_seconds: float = Field(default=600.0, gt=0)

    #: A stage killed at the hard limit never acknowledges, so its lease
    #: expires and the job is re-claimed rather than lost.
    task_time_limit_seconds: int = Field(default=1800, gt=0)
    task_soft_time_limit_seconds: int = Field(default=1500, gt=0)

    @model_validator(mode="after")
    def _derive_tokenizer(self) -> Settings:
        """Size chunks with the embedding model's tokenizer, and to fit it.

        Derived rather than configured, because the right value is not a
        preference: the tokenizer that decides where a chunk ends has to be
        the one the model reads it with. Naming it separately was a second
        setting to keep in step with the model, and the two disagreeing
        produces chunks the embedder silently truncates.
        """
        if self.embedding_model is None:
            return self
        if self.chunk_tokenizer is None:
            self.chunk_tokenizer = tokenizer_for(self.embedding_model)
        known = find_embedding_model(self.embedding_model)
        if known is not None and self.max_chunk_tokens > known.max_tokens:
            raise ValueError(
                f"PRIMER_MAX_CHUNK_TOKENS is {self.max_chunk_tokens}, but {known.model_id} "
                f"reads at most {known.max_tokens} tokens and would silently cut every "
                f"longer chunk short. Set it to {known.max_tokens} or less."
            )
        return self

    @property
    def graph_extraction_enabled(self) -> bool:
        return (
            self.graph_extraction_base_url is not None and self.graph_extraction_model is not None
        )
