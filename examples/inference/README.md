# Inference servers

The chart and Compose run these two servers themselves by default. These
manifests are the same servers standing alone, for running them outside a
Primer release: shared between several, or in a namespace of their own.

Both are llama.cpp's `llama-server`, the image Primer bundles, published
for amd64 and arm64. They run on CPU as written; each manifest says which
image tag and limit to change for an NVIDIA GPU.

| File | Serves | Model |
| --- | --- | --- |
| `embeddings.yaml` | `/v1/embeddings` | `Qwen/Qwen3-Embedding-0.6B-GGUF:Q8_0` |
| `reranker.yaml` | `/rerank` | `ggml-org/Qwen3-Reranker-0.6B-Q8_0-GGUF` |

The flags mirror the chart's own `templates/models.yaml`, whose comment says
what each one prevents.
[docs/operations/models.md](../../docs/operations/models.md) covers how to
pick different models.

If you already run vLLM, you do not need these at all: point Primer at it
instead. [docs/operations/providers.md](../../docs/operations/providers.md)
has the `vllm serve` lines for both roles.

Neither manifest carries a `namespace`, so apply them into whichever one
Primer runs in:

```bash
kubectl -n primer apply -f embeddings.yaml -f reranker.yaml
```

Then turn the chart's own servers off and point it at these:

```yaml
inference:
  embeddings:
    bundled: { enabled: false }
    baseUrl: http://primer-embeddings:8080/v1
  rerank:
    bundled: { enabled: false }
    baseUrl: http://primer-reranker:8080
```

If you rename the services, change the URLs to match.
