# Choosing models

Primer ships no model. It points at endpoints you already run, which means
the interesting decisions are yours to make and mostly invisible until
something is quietly worse than it should be.

Everything here is measured on a CPU-only deployment: two consumer GPUs
committed to chat models, embedding and reranking on a 72-core host. Where a
number appears, it came from that machine rather than from a model card.

## The short version

If you want a working configuration to copy rather than a discussion, use
[`values-selfhosted.yaml`](#a-reference-configuration) and read the rest
when something surprises you.

| Role | Model | Why |
| --- | --- | --- |
| Embedding | `onnx-community/Qwen3-Embedding-0.6B-ONNX` | 1024-dim, strong retrieval, and an ONNX build that is 3-4x faster on CPU than the safetensors |
| Chunk tokenizer | `Qwen/Qwen3-Embedding-0.6B` | must match the embedding model; the chart derives it |
| Reranker | `BAAI/bge-reranker-base` | ~0.2s for ten passages on CPU, against ~20s for a 0.6B reranker |
| Chat | whatever your GPUs fit | Primer is not opinionated here |

## Embedding

### Pick the ONNX build, not the original repo

Text Embeddings Inference prefers its ONNX Runtime backend and silently
falls back to Candle when a repository publishes no ONNX. That fallback is
the single largest performance decision in this stack, and nothing reports
it beyond one line in the server's startup log.

Measured on the same model, same host, same settings:

| batch | Candle | ONNX |
| --- | --- | --- |
| 1 | 1.1 chunks/sec | **2.9 chunks/sec** |
| 8 | 0.9 chunks/sec | **3.8 chunks/sec** |
| 32 | 1.6 chunks/sec | **4.2 chunks/sec** |

Candle is flat: batching buys nothing, and neither does concurrency. Eight
concurrent clients took eight times as long as one, because it serialises
completely. If your ingestion feels slow and adding workers does not help,
this is why - and more workers cannot help, because the queue is not where
the time goes.

`Qwen/Qwen3-Embedding-0.6B` publishes no ONNX.
`onnx-community/Qwen3-Embedding-0.6B-ONNX` does, at the same 1024
dimensions, and produced vectors identical to four decimal places in a
side-by-side comparison. Use the second.

Take the `fp32` `onnx/model.onnx`, not the `int8` or `q4` builds beside it.
Quantisation changes the vectors materially, and its damage to retrieval
quality is the kind nobody notices.

### The ONNX build needs the pooling mode stated

Qwen's own repository ships `1_Pooling/config.json`; the ONNX export does
not. TEI refuses to start without it rather than guessing:

```
Error: The `--pooling` arg is not set and we could not find a pooling
configuration (`1_Pooling/config.json`) for this model.
```

That refusal is doing you a favour. Qwen3-Embedding pools the **last
token** (`"pooling_mode_lasttoken": true` upstream), and mean-pooling it
returns plausible vectors that retrieve badly. Pass `--pooling last-token`.

### The width comes from the model, and cannot be changed later

Primer knows the width of the models in its table, so naming the model is
enough. For a model it does not know, Retrieval refuses to start until
`inference.embeddings.dimensions` is set.

A width cannot be changed after documents are indexed: the vector store
keeps the width it was created with. Changing embedding models means
reindexing everything. Decide this once, before anyone uploads anything.

## Chunking

`PRIMER_CHUNK_TOKENIZER` decides whether chunks are bounded by tokens and
merged, or split on document structure alone. The parse worker derives it
from the embedding model, including from Ollama's names, so usually you
set nothing.

Set `ingestion.chunkTokenizer` explicitly when your embedding model is
hosted - `text-embedding-3-small` names no repository and publishes no
tokenizer - and name the nearest open model instead.

Getting this wrong is not subtle in effect but is very subtle in
appearance. Structural chunking emits one chunk per text item and merges
none of them, so a form or a statement is stored one line at a time.
Measured on a W2-shaped document: **20 chunks averaging 13 characters**,
against a single 274-character passage with peers merged. On a clean
markdown table the two chunkers agree exactly, which is why this survives
casual testing.

The symptom is a citation that quotes two words. The parse worker warns at
startup when it is in this state, because nothing downstream will.

### Fragments survive a correct tokenizer too

A tokenizer is necessary and not sufficient. `HybridChunker` merges
undersized chunks only when they are **peers** - items sharing a heading -
and a form defeats that completely: Docling recognises its labels as
headings, so every value sits alone under a heading of its own with no peer
to merge with.

Measured on a W2-shaped document with the tokenizer correctly configured:
five chunks averaging **10 characters**, one of them the bare string
`52,000.00`. A passage like that answers nothing, and it drags the rest of
the index down with it - a bare `Homeowners` is short enough to score
against any question mentioning a person, which is how a fragment becomes
the top hit for a question it cannot answer.

`ingestion.minChunkChars` (default 40) joins such passages to the ones
beside them, across the heading boundary the chunker will not cross. The
contents are joined to each other and the contextualized texts to each
other, so a citation stays quotable from its source while the headings that
name the values survive into what is embedded. Never across a page: a
passage carries one page number, and slides are the sharp case.

Leave it alone unless you have a reason. It is far below prose on purpose -
the fragments it exists for measure 8 to 15 characters, a real sentence 60
or more - and raised towards sentence length it merges passages that were
fine and takes their section citations with them. At 200 it merged an
ordinary two-section paper into one chunk. `0` switches it off.

### Changing any of this means reindexing

Everything indexed under the old settings is stale together. **Reindex
all**, at the top of a library, rebuilds every document in one press. The
library keeps answering from its current index while that runs, and
switches over a document at a time as each finishes; nothing is deleted.
Documents already being rebuilt are reported separately rather than
restarted, so pressing it twice is safe.

## Text inside pictures

Docling runs OCR in its PDF pipeline only. Every other format Primer accepts
is converted by a pipeline that has no OCR setting at all, so a slide deck of
pasted charts, or a report whose figures carry its numbers, converts to its
headings and a row of empty images.

Primer reads those pictures itself, with the same RapidOCR engine the PDF
pipeline uses. `ingestion.maxPicturesPerDocument` bounds it, because each
picture costs a full pass and a deck built entirely of screenshots is the
slowest thing the parse worker does. Past the ceiling the remaining images
are left unread rather than the document left unfinished; `0` turns picture
reading off while leaving OCR on for PDFs.

Pictures smaller than 64 pixels on a side are skipped. Bullets, logos and
spacers are most of the images in a real document and there is nothing in
them to read.

The usual caveat applies and is worth repeating: recognized text is a
transcription rather than the document's own characters, so a citation drawn
from it can be subtly wrong in a way a reader cannot see. Chunks carry no
marker for this.

## Reranking

A vector search compares a question and a passage through two embeddings
made without knowing about each other. A cross-encoder reads the pair
together. It is much better and much more expensive, so Primer searches
widely and cheaply and has the reranker read a shortlist.

### Not the obvious partner model

`Qwen3-Reranker-0.6B` looks like the natural companion to the Qwen3
embedding model, and on a CPU-only deployment it is the wrong choice:

- Qwen publishes it as `Qwen3ForCausalLM`. It scores by comparing the
  logits of "yes" and "no" rather than through a classification head, and
  TEI's `/rerank` needs a sequence classifier.
- The community conversion that does have one
  (`tomaarsen/Qwen3-Reranker-0.6B-seq-cls`) publishes no ONNX, so it runs
  on Candle.
- Candle at that model's shape measures ~1 sequence/sec. Twenty passages is
  **twenty seconds on every question**.

`BAAI/bge-reranker-base` is 12 layers and 768 hidden rather than 28 and
1024, publishes `onnx/model.onnx`, and measured **0.17-0.27s for ten
passages** - so a shortlist of twenty costs well under a second. Its
514-position limit is not a practical constraint when chunks are bounded at
512 tokens.

With a GPU free, revisit this. Without one, spending twenty seconds to
spare the model fourteen extra passages is not a trade worth making.

### Rerankers are silent when they fail

A failed rerank falls back to the vector ordering rather than failing the
search, which is right - a reranker that is down should not lose the
answer. It also means a misconfigured reranker looks exactly like a working
one. After configuring it, ask a question whose answer sits outside the
first few vector hits and check that it moves.

## The relevance floor

`inference.minScore` drops passages scoring below it, even when that leaves
nothing. Returning nothing is the point: a vector search always returns its
top k, so a question your library cannot answer comes back with a full set
of passages that are only the closest of a bad lot, and the model is handed
them as evidence.

Measured on one library before this existed, against a corpus of mortgage
paperwork:

| question | score |
| --- | --- |
| How much were the total closing costs? | 0.364 |
| *Describe the plot of the film Casablanca.* | **0.392** |

There is no default, because the right value belongs to whatever produced
the score. Cosine from one embedding model is not comparable to cosine from
another, and neither is comparable to a reranker's output.

**Measure it.** Ask a question your corpus answers well, one it answers
poorly, and one it cannot answer at all, then put the floor between the
last two. On the reference configuration below, an unanswerable question
tops out around 0.0003 and the weakest genuine answer measured 0.143, so
`0.02` sits comfortably between them.

Err low. A floor that is too high discards correct answers, and a passage
that genuinely answers a question can still score modestly.

## Serving flags that matter

These are Text Embeddings Inference specifics that cost real debugging
time.

`--max-client-batch-size` must be at least what the client sends. Primer's
document embedder sends 32 per request; a lower ceiling rejects every
ingestion batch with a 422 and the document fails with nothing wrong on
Primer's side.

`--max-concurrent-requests` is **counted in inputs, not requests**. A
`POST /embed` carrying 32 inputs enqueues 32 tasks against it, so any value
below the client batch size answers every ingestion batch with a 429 before
anything is inferred. Set it well above `batch size x concurrent clients`.

`--max-batch-tokens` is what warmup allocates against, so it is what an
OOM-at-startup is usually made of. It is not what bounds a queue.

`--tokenization-workers` defaults to the host's core count. Each worker
holds its own tokenizer, which is nearly free for a BPE vocabulary and
expensive for SentencePiece: `bge-reranker-base` is XLM-RoBERTa with a
~250k-entry vocabulary, and 64 copies of it was 3.4GB of resident memory. A
handful of workers is plenty for reranking, which runs once per search.

`--auto-truncate` trims an over-long input rather than failing the whole
batch it arrived in.

## Sizing

Set memory **requests** above the pod's idle footprint, not below it. A
request is what the scheduler packs a node by, so a request under
steady-state usage invites Kubernetes to place a pod somewhere it cannot
live. The embedding server here idles at ~4.4GiB; it was requesting 3GiB.

Set **limits** with room for the warmup batch, not from the parameter
count. A 278M-parameter reranker was OOM-killed at a 6GiB ceiling that a
600M-parameter embedder lived inside, because the reranker had been given
twice the batch tokens.

## A reference configuration

`deploy/helm/primer/values-selfhosted.yaml` is the configuration described
above, ready to copy:

```bash
helm install primer oci://ghcr.io/isaiah-harville/charts/primer \
  --version <release> \
  -f values-selfhosted.yaml \
  --set ingress.host=primer.example.com \
  --set auth.oidc.issuerUrl=https://auth.example.com/application/o/primer/ \
  --set auth.oidc.clientId=primer \
  --set auth.oidc.existingSecret=primer-oidc \
  --set auth.adminGroup='Primer Admins' \
  --set postgresql.existingSecret=primer-postgres \
  --set rabbitmq.existingSecret=primer-rabbitmq \
  --set sourceStore.existingSecret=primer-s3
```

It assumes you run the two inference servers yourself. Manifests for both,
matching the flags discussed above, are in
[`examples/inference/`](https://github.com/isaiah-harville/Primer/tree/main/examples/inference).
