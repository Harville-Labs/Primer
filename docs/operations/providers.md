# Connecting model providers

Primer needs three models: one to embed passages and questions, one to
rerank what the search finds, and one to answer. Compose and the chart run
the first two for you, so a fresh install can index and search on its own.
The chat model is the one you bring.

Every endpoint here is OpenAI-compatible, so any server that speaks that
protocol works: vLLM, Ollama, llama.cpp's `llama-server`, or a hosted API.

| Role | Bundled | URL Primer is given | Path Primer calls |
| --- | --- | --- | --- |
| Chat | no | the base, ending in `/v1` | `/chat/completions`, `/models` |
| Embedding | yes | the base, ending in `/v1` | `/embeddings` |
| Rerank | yes | the base, **without** `/v1` for TEI and vLLM | `/rerank` |

The rerank URL is the one people get wrong. TEI and vLLM serve `/rerank` at
the root; Cohere and Jina serve it under `/v1`, so for those the base does
end in `/v1`. Either way it is the URL before `/rerank`.

## Chat

In Compose, set `PRIMER_CHAT_BASE_URL` in `.env`, or leave it unset and add
a provider on the settings page. On the chart, `inference.chat.baseUrl`. A
key goes in `PRIMER_CHAT_API_KEY`, or on the chart in a Secret named by
`inference.chat.existingSecret` under `inference.chat.apiKeyKey`.

Naming a model is optional: Primer asks the endpoint what it serves.

### vLLM

```bash
vllm serve Qwen/Qwen3-8B --reasoning-parser qwen3
```

Base URL `http://<host>:8000/v1`. `--reasoning-parser` sends a reasoning
model's thinking in a field of its own. Without it, Primer separates the
thinking from the `<think>` tags in the answer instead, which also works.
For tools, also pass `--enable-auto-tool-choice` and the
`--tool-call-parser` for your model's family (`hermes` for Qwen).

### Ollama

```bash
ollama pull qwen3:8b
```

Base URL `http://<host>:11434/v1`. No key.

### llama.cpp

```bash
llama-server -m model.gguf --port 8080 --jinja --reasoning-format deepseek
```

Base URL `http://<host>:8080/v1`. `--jinja` uses the model's own chat
template, which tool calling needs.

### Hosted APIs

The provider's OpenAI-compatible base URL and an API key. Questions and the
passages retrieved for them are sent to that provider, which is worth
deciding on purpose for a private library.

### Reaching a server on the host from Compose

Inside a container, `localhost` is the container. Use
`http://host.docker.internal:<port>/v1`. On Linux that name needs
`extra_hosts: ["host.docker.internal:host-gateway"]` on the service.

## Using your own embedding server

The bundled server is Text Embeddings Inference on CPU, serving
`onnx-community/Qwen3-Embedding-0.6B-ONNX`. Replace it when you have a GPU,
or on Apple Silicon: the image is published for amd64 only, and emulated it
is slow. A native Ollama is the quick fix there.

Choose before the first upload. The model sets the width of every stored
vector, so changing it later means reindexing.
[Choosing models](models.md) covers which to pick.

**Compose.** Setting the URL stops the bundled server:

```bash
PRIMER_EMBEDDING_BASE_URL=http://host.docker.internal:11434/v1
PRIMER_EMBEDDING_MODEL=qwen3-embedding:0.6b
```

**Chart.** Turn the bundled server off and name yours. Setting both is
refused, because one of them would be silently ignored:

```yaml
inference:
  embeddings:
    bundled:
      enabled: false
    baseUrl: http://vllm.models:8000/v1
    model: Qwen/Qwen3-Embedding-0.6B
```

The model is the name your server answers to. Primer looks it up to find
the width and the query and passage prompts, so for a model it knows that is
all you set. For one it does not, set `dimensions` and the prefixes too.

To keep the bundled server and only change the model, set the model alone.
The bundled server pools by `last-token`, which is right for the Qwen3
models; most others want `mean` or `cls`
(`inference.embeddings.bundled.pooling`, or `PRIMER_EMBEDDING_POOLING`).

For vLLM, serve an embedding model as a pooling model:

```bash
vllm serve Qwen/Qwen3-Embedding-0.6B --runner pooling
```

Older releases spell that `--task embed`.

## Using your own reranker, or none

**Compose.** Setting `PRIMER_RERANK_BASE_URL` stops the bundled server.
Set it to your server's URL, or to nothing to search without reranking:

```bash
PRIMER_RERANK_BASE_URL=http://host.docker.internal:8081
PRIMER_RERANK_MODEL=BAAI/bge-reranker-base
```

**Chart.**

```yaml
inference:
  rerank:
    bundled:
      enabled: false
    baseUrl: http://tei.models:8080   # leave empty to search without one
    model: BAAI/bge-reranker-base
```

The bundled reranker accepts at most 32 passages per call, so the chart
refuses an `inference.rerank.candidates` above that while it is on.

TEI and vLLM read the passages from different fields and return scores
under different names. Primer sends and reads both, so either works, as do
Cohere's and Jina's hosted rerankers.

## Running the bundled servers yourself

[`examples/inference/`](https://github.com/isaiah-harville/Primer/tree/main/examples/inference)
has standalone manifests for the same two servers. Use them to share one
pair between several releases, or to run them somewhere other than
Primer's namespace.
