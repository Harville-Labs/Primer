"""What Primer knows about the embedding models people actually run.

Not a wire model, but a contract all the same: Retrieval embeds with these
prompts and dimensions and the parse worker sizes chunks with this
tokenizer, and the two only produce a usable index if they agree. Keeping
the table here is what makes them read the same row.

An operator names a model and nothing else. Most retrieval models are
trained with a prompt on the query side, the passage side, or both, and
embedding without it is not an error anybody sees - it is search that is a
few points worse than it should be, measured by nobody. Qwen reports 1-5%
lost retrieval quality without its query instruction; nomic and E5 were
trained with their prefixes on both sides and drift further without them.
Asking every operator to find and copy those strings is how they end up
missing, so the strings live here and settings override them only when a
deployment has a reason to.

Every entry is taken from the model's own card, not from memory or a
third-party list. When adding one, copy the prompts exactly - trailing
spaces and newlines included, since the model saw those during training.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class EmbeddingModel:
    """One model, under every name a server is likely to serve it as."""

    #: The Hugging Face repository, or the vendor's own name for a hosted
    #: model that has none.
    model_id: str
    #: Native output width. Changing it invalidates every stored vector.
    dimensions: int
    #: The most tokens the model reads; anything longer is truncated by the
    #: server, silently, so chunks must fit inside it.
    max_tokens: int
    query_prefix: str = ""
    document_prefix: str = ""
    #: The tokenizer chunks are sized against. None for a hosted model that
    #: publishes none; chunking then falls back to document structure.
    tokenizer: str | None = None
    #: Names local servers use. Ollama in particular serves its own names,
    #: not repository ids, and `nomic-embed-text` has to resolve to the same
    #: row as `nomic-ai/nomic-embed-text-v1.5`.
    aliases: tuple[str, ...] = ()


_QWEN3_QUERY = (
    "Instruct: Given a web search query, retrieve relevant passages that answer the query\nQuery:"
)
_BGE_QUERY = "Represent this sentence for searching relevant passages: "


def _qwen3(size: str, dimensions: int) -> EmbeddingModel:
    repository = f"Qwen/Qwen3-Embedding-{size}"
    return EmbeddingModel(
        model_id=repository,
        dimensions=dimensions,
        max_tokens=32768,
        query_prefix=_QWEN3_QUERY,
        tokenizer=repository,
        aliases=(f"qwen3-embedding:{size.lower()}",),
    )


#: Ordered by how strongly Primer recommends them. The first is the default
#: the documentation and the bundled server use.
KNOWN_MODELS: tuple[EmbeddingModel, ...] = (
    # Apache-2.0, ungated, and served by Text Embeddings Inference on CPU.
    # The ONNX export is the same weights producing the same vectors, and
    # several times faster under TEI, so it is the same row.
    EmbeddingModel(
        model_id="Qwen/Qwen3-Embedding-0.6B",
        dimensions=1024,
        max_tokens=32768,
        query_prefix=_QWEN3_QUERY,
        tokenizer="Qwen/Qwen3-Embedding-0.6B",
        aliases=("qwen3-embedding:0.6b", "onnx-community/qwen3-embedding-0.6b-onnx"),
    ),
    _qwen3("4B", 2560),
    _qwen3("8B", 4096),
    # The `Document` prompt carries no title. Chunks do not have one that
    # means anything to the model, and the card's "title: none" is what it
    # was trained to see in that case.
    EmbeddingModel(
        model_id="google/embeddinggemma-2",
        dimensions=768,
        max_tokens=8192,
        query_prefix="task: search result | query: ",
        document_prefix="title: none | text: ",
        tokenizer="google/embeddinggemma-2",
    ),
    EmbeddingModel(
        model_id="google/embeddinggemma-300m",
        dimensions=768,
        max_tokens=2048,
        query_prefix="task: search result | query: ",
        document_prefix="title: none | text: ",
        tokenizer="google/embeddinggemma-300m",
        aliases=("embeddinggemma", "embeddinggemma:300m"),
    ),
    EmbeddingModel(
        model_id="BAAI/bge-m3",
        dimensions=1024,
        max_tokens=8192,
        tokenizer="BAAI/bge-m3",
        aliases=("bge-m3",),
    ),
    EmbeddingModel(
        model_id="Snowflake/snowflake-arctic-embed-l-v2.0",
        dimensions=1024,
        max_tokens=8192,
        query_prefix="query: ",
        tokenizer="Snowflake/snowflake-arctic-embed-l-v2.0",
        aliases=("snowflake-arctic-embed2", "snowflake-arctic-embed2:568m"),
    ),
    EmbeddingModel(
        model_id="nomic-ai/nomic-embed-text-v1.5",
        dimensions=768,
        max_tokens=8192,
        query_prefix="search_query: ",
        document_prefix="search_document: ",
        tokenizer="nomic-ai/nomic-embed-text-v1.5",
        aliases=("nomic-embed-text", "nomic-embed-text:v1.5"),
    ),
    EmbeddingModel(
        model_id="nomic-ai/nomic-embed-text-v2-moe",
        dimensions=768,
        max_tokens=512,
        query_prefix="search_query: ",
        document_prefix="search_document: ",
        tokenizer="nomic-ai/nomic-embed-text-v2-moe",
        aliases=("nomic-embed-text-v2-moe",),
    ),
    EmbeddingModel(
        model_id="mixedbread-ai/mxbai-embed-large-v1",
        dimensions=1024,
        max_tokens=512,
        query_prefix=_BGE_QUERY,
        tokenizer="mixedbread-ai/mxbai-embed-large-v1",
        aliases=("mxbai-embed-large", "mxbai-embed-large:335m"),
    ),
    EmbeddingModel(
        model_id="intfloat/multilingual-e5-large",
        dimensions=1024,
        max_tokens=512,
        query_prefix="query: ",
        document_prefix="passage: ",
        tokenizer="intfloat/multilingual-e5-large",
    ),
    EmbeddingModel(
        model_id="BAAI/bge-large-en-v1.5",
        dimensions=1024,
        max_tokens=512,
        query_prefix=_BGE_QUERY,
        tokenizer="BAAI/bge-large-en-v1.5",
        aliases=("bge-large", "bge-large:335m"),
    ),
    EmbeddingModel(
        model_id="BAAI/bge-base-en-v1.5",
        dimensions=768,
        max_tokens=512,
        query_prefix=_BGE_QUERY,
        tokenizer="BAAI/bge-base-en-v1.5",
    ),
    EmbeddingModel(
        model_id="BAAI/bge-small-en-v1.5",
        dimensions=384,
        max_tokens=512,
        query_prefix=_BGE_QUERY,
        tokenizer="BAAI/bge-small-en-v1.5",
    ),
    EmbeddingModel(model_id="text-embedding-3-small", dimensions=1536, max_tokens=8191),
    EmbeddingModel(model_id="text-embedding-3-large", dimensions=3072, max_tokens=8191),
)


def _names(model: EmbeddingModel) -> tuple[str, ...]:
    return (model.model_id.lower(), *model.aliases)


_BY_NAME: dict[str, EmbeddingModel] = {
    name: model for model in KNOWN_MODELS for name in _names(model)
}


def find_embedding_model(name: str) -> EmbeddingModel | None:
    """The row for a model name, or None for one Primer does not know.

    Case does not matter, and neither does Ollama's implicit `:latest` tag:
    `nomic-embed-text:latest` is the model `nomic-embed-text` names.
    """
    key = name.strip().lower().removesuffix(":latest")
    return _BY_NAME.get(key)


def tokenizer_for(name: str) -> str | None:
    """Which tokenizer to size chunks with, for a model named `name`.

    A known model says. An unknown one named `owner/model` is assumed to be
    a Hugging Face repository with a tokenizer of its own, because anything
    served from there is; a bare name like `text-embedding-3-small` names no
    repository, and guessing one would hand the worker something it cannot
    fetch and refuses to start on.
    """
    known = find_embedding_model(name)
    if known is not None:
        return known.tokenizer
    return name if "/" in name else None
