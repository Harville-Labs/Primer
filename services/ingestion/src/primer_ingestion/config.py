"""Worker configuration.

Workers hold no database credentials. Everything they may do to a job goes
through the Control API, so the only secrets here are a broker URL and the
service credential that identifies this process to Control.
"""

from __future__ import annotations

from typing import Annotated, Literal

from primer_contracts.embedding_models import find_embedding_model, tokenizer_for
from primer_storage import check_url
from pydantic import AfterValidator, Field, SecretStr, model_validator
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

    #: Checked here so an unsupported scheme stops the process at startup
    #: rather than failing the first upload.
    source_store_url: Annotated[str, AfterValidator(check_url)] = Field(
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
    #: Batches of one document sent to Retrieval at once. Embedding is a
    #: network round trip, so one call at a time leaves the model server
    #: idle while each response travels back and the next request is built.
    #: Multiplied by the worker's concurrency, this is what the embedding
    #: endpoint sees in flight.
    index_concurrency: int = Field(
        default=4,
        gt=0,
        description="Index calls one document has in flight at once",
    )

    enable_ocr: bool = Field(
        default=True,
        description="Read text from images and slides; off makes parsing faster and offline",
    )
    #: Docling's layout and table models run on torch, which otherwise takes
    #: every core the node has, not the share the pod was granted, and then
    #: contends with itself. Named as strings rather than Docling's enum so
    #: reading the configuration does not import the parser.
    parse_device: Literal["auto", "cpu", "cuda", "mps", "xpu"] = Field(
        default="auto",
        description="Where Docling runs its models; auto uses a GPU when there is one",
    )
    parse_threads: int = Field(
        default=4,
        gt=0,
        description="CPU threads Docling's models use; match the CPU the worker is given",
    )
    #: Each picture costs a full OCR pass, and a call to the vision model
    #: when one describes figures, so a deck built entirely of screenshots
    #: is the slowest thing this worker does. The ceiling is a bound on that
    #: rather than a judgement about how many pictures a document should
    #: have: past it, the remaining images are left unread rather than the
    #: document left unfinished.
    max_pictures_per_document: int = Field(
        default=100,
        ge=0,
        description="How many embedded images to read or describe before giving up on the rest",
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
    #: The longest input the embedding server accepts, as its operator
    #: declared it. llama-server and vLLM refuse a longer one outright rather
    #: than truncating it, so a chunk above this fails every batch it is in.
    embedding_max_input_tokens: int | None = Field(
        default=None,
        gt=0,
        description="Longest input the embedding server accepts; chunks must fit inside it",
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

    #: Any OpenAI-compatible endpoint that supports structured output; a
    #: small instruction-tuned model is enough, and it need not be the one
    #: Chat answers with. Unset, it is the chat endpoint below.
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

    #: Chat's endpoint, read only as extraction's fallback. It is not the
    #: fallback without a model named, because Chat then asks the endpoint
    #: which models it serves, and extraction has no first model to take.
    chat_base_url: str | None = Field(default=None)
    chat_model: str | None = Field(default=None)
    chat_api_key: SecretStr | None = Field(default=None)

    #: Reads what a picture shows: an image added to a library from Studio,
    #: and the figures inside documents. Off unless both are set, and
    #: without it Control does not offer the Studio action. Any
    #: OpenAI-compatible endpoint that accepts images.
    vision_base_url: str | None = Field(
        default=None,
        description="OpenAI-compatible base URL of the model that describes images",
    )
    vision_model: str | None = Field(
        default=None,
        description="Vision model that describes images and figures; unset turns it off",
    )
    vision_api_key: SecretStr | None = Field(default=None)
    vision_timeout_seconds: float = Field(default=120.0, gt=0)
    #: On whenever a vision model is configured, because a chart is
    #: otherwise findable only by whatever words OCR recovers from it. This
    #: is the way to keep the model for Studio alone, where each figure it
    #: would describe is a request someone pays for.
    describe_figures: bool = Field(
        default=True,
        description="Ask the vision model what each figure in a document shows",
    )
    #: Figures are described one request each. The worker only waits, so
    #: this is bounded by what the model server can take, not by this pod.
    vision_concurrency: int = Field(
        default=4,
        gt=0,
        description="Figures sent to the vision model at once",
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
        limit = self.embedding_max_input_tokens
        if limit is not None and self.max_chunk_tokens > limit:
            raise ValueError(
                f"PRIMER_MAX_CHUNK_TOKENS is {self.max_chunk_tokens}, but the embedding server "
                f"accepts at most {limit} tokens (PRIMER_EMBEDDING_MAX_INPUT_TOKENS) and refuses "
                f"longer inputs, so every batch holding a longer chunk would fail. Lower "
                f"PRIMER_MAX_CHUNK_TOKENS to {limit} or less, or start the server with a "
                f"larger limit and raise PRIMER_EMBEDDING_MAX_INPUT_TOKENS to match."
            )
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

    @model_validator(mode="after")
    def _extract_with_chat_model(self) -> Settings:
        """Extract with the chat model when no extraction endpoint is named.

        Chosen whole, endpoint, model and key together, and only when the
        extraction endpoint is unset. Falling back one setting at a time
        would send the chat key to an extraction endpoint named without one.
        """
        if self.graph_extraction_base_url is None and self.chat_base_url and self.chat_model:
            self.graph_extraction_base_url = self.chat_base_url
            self.graph_extraction_model = self.chat_model
            self.graph_extraction_api_key = self.chat_api_key
        return self

    @property
    def graph_extraction_enabled(self) -> bool:
        return (
            self.graph_extraction_base_url is not None and self.graph_extraction_model is not None
        )
