# Inference servers

The chart and Compose run these two servers themselves by default. These
manifests are the same servers standing alone, for running them outside a
Primer release: shared between several, or in a namespace of their own.

Both are CPU-only. That is a constraint worth stating rather than a
recommendation: they are shaped for a deployment whose GPUs are committed to
chat models. With a GPU free, most of the tuning here becomes irrelevant and
you should reach for larger models.

| File | Serves | Model |
| --- | --- | --- |
| `embeddings.yaml` | `/v1/embeddings` | `onnx-community/Qwen3-Embedding-0.6B-ONNX` |
| `reranker.yaml` | `/rerank` | `BAAI/bge-reranker-base` |

The flags in both are load-bearing and the reasoning is in the comments.
[docs/operations/models.md](../../docs/operations/models.md) covers the
measurements behind them and how to pick different models.

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
