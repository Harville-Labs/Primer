# Choosing models

Primer runs the embedding model and reranker in the table below unless you
name your own, and brings no chat model. [Connecting model
providers](providers.md) says how to point it at your servers. This page is
about which models to choose, and why the defaults are what they are: these
decisions are mostly invisible until something is quietly worse than it
should be.

## The short version

These are the defaults Compose and the chart ship with, served by
llama.cpp's `llama-server` on CPU. If you want a working configuration to
copy rather than a discussion, use
[`values-selfhosted.yaml`](#a-reference-configuration) and read the rest
when something surprises you.

| Role | Model | Why |
| --- | --- | --- |
| Embedding | `Qwen/Qwen3-Embedding-0.6B`, from `Qwen/Qwen3-Embedding-0.6B-GGUF:Q8_0` | 1024-dim, strong retrieval, and small enough for a CPU |
| Chunk tokenizer | `Qwen/Qwen3-Embedding-0.6B` | must match the embedding model; the chart derives it |
| Reranker | `Qwen/Qwen3-Reranker-0.6B`, from `ggml-org/Qwen3-Reranker-0.6B-Q8_0-GGUF` | the embedder's partner, and much better than a vector ordering |
| Chat | whatever your GPUs fit | Primer is not opinionated here |

[ADR 0015](../adr/0015-bundle-llama-cpp-for-embeddings-and-reranking.md)
says why the bundled server is llama.cpp. If you already run vLLM, point
Primer at it instead: [Connecting model
providers](providers.md#using-your-own-embedding-server) has the commands.

## Embedding

### The model and the weights are named separately

`inference.embeddings.model` is the name Primer knows the model by. It
looks up the width, the query and passage prompts, and the tokenizer from
it. `inference.embeddings.bundled.gguf` is the file the bundled server
downloads, as `<Hugging Face repository>[:<quantization>]`. The server is
started with `--alias` set to the model's name, so it answers to that name
whatever file it loaded. Change the two together.

The default is Qwen's own Q8_0 build. Lower quantizations are smaller and
faster, and they change the vectors more. That damage to retrieval quality
is the kind nobody notices, so stay at Q8_0 unless memory forces otherwise.

### State the pooling mode

An embedding model turns its token states into one vector, and the
right way to do that belongs to the model. Qwen3-Embedding pools the **last
token**. Mean-pooling it returns plausible vectors that retrieve badly, and
nothing reports an error. The chart and Compose pass `--pooling last`
rather than trusting what the file declares. For another model,
`bundled.pooling` takes `last`, `mean` or `cls`; the model card says which.

vLLM reads the pooling mode from the model's own configuration, which for
`Qwen/Qwen3-Embedding-0.6B` is already last-token.

### The width comes from the model, and cannot be changed later

Primer knows the width of the models in its table, so naming the model is
enough. For a model it does not know, Retrieval refuses to start until
`inference.embeddings.dimensions` is set.

A width cannot be changed after documents are indexed: the vector store
keeps the width it was created with. Changing embedding models means
reindexing everything. Decide this once, before anyone uploads anything.

### A different model at the same width is refused too

Qdrant checks a vector's width and nothing else. Two models of the same
width produce vectors it accepts without complaint, and every search after
the swap would compare a question in one model's space with passages in
another's.

So a collection records the embedder that built it. Retrieval embeds a
fixed text when it creates the collection and keeps the vector in the
collection's metadata. Before its first search or write, each Retrieval
embeds the same text and compares. A different model, a different
quantization or a different pooling mode is refused with a `409 conflict`
naming `PRIMER_EMBEDDING_BASE_URL` and `PRIMER_QDRANT_INDEX`. Either point
back at the embedder that built the collection, or name a new index and
reindex into it. A collection made before Primer kept this record adopts
the embedder it first meets.

The check runs when a process first uses the collection. A server replaced
behind the same URL while Retrieval is running is caught at Retrieval's
next restart.

### Tell Primer what the server accepts

An OpenAI-compatible server says nothing about its limits until a request
breaks one. Primer stays inside two that you declare:

| Setting | Chart | Default |
| --- | --- | --- |
| Passages per call while indexing | `inference.embeddings.capabilities.batchSize` | 32 |
| Tokens in one input, its prompt included | `inference.embeddings.capabilities.maxInputTokens` | 2048 |

In Compose they are `PRIMER_EMBEDDING_BATCH_SIZE` and
`PRIMER_EMBEDDING_MAX_INPUT_TOKENS`. llama.cpp and vLLM refuse a longer
input outright rather than truncating it, so the parse worker refuses to
start if `ingestion.maxChunkTokens` and the passage prompt would not fit.
The bundled server is sized to `maxInputTokens`. For your own, set it to
what the server was started with: `--max-model-len` on vLLM.

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

## What a figure shows

OCR recovers the words printed in a chart and nothing else: not that it is
a chart, what it plots, or which way the line goes. A figure that carries a
paper's result is findable only by its axis labels.

With a vision model configured, the parse worker asks it what each figure
shows and indexes the answer where the figure stands, in PDFs, Word
documents, and slide decks alike. The model is the one Studio uses, set the
same way:

```yaml
inference:
  vision:
    baseUrl: http://vision.models.svc:8000/v1
    model: Qwen/Qwen2.5-VL-7B-Instruct
    concurrency: 4       # figures in flight at once
```

In Compose, set `PRIMER_VISION_BASE_URL` and `PRIMER_VISION_MODEL`. Studio
does not have to be on. To keep the model for Studio images only, set
`ingestion.describeFigures: false` (`PRIMER_DESCRIBE_FIGURES=false`).

What a reader sees:

- **A description is marked as one.** The passage reads
  `[Figure, as described by <model>]` before the description, so a citation
  never presents a model's reading of a chart as the author's words.
- **Text in a figure is described, never followed.** A figure is someone
  else's content, and the prompt says so.
- **It costs one request per figure**, bounded by the same
  `ingestion.maxPicturesPerDocument` as OCR. Figures smaller than 64 pixels
  on a side, or under a twentieth of their page, are not sent.
- **An unreachable model is retried, not skipped.** If every figure in a
  document fails, the parse is retried later rather than indexed without
  them. One figure failing on its own is logged and left out.

Changing the vision model makes every document's stored conversion stale,
so reindex the library for its figures to be described by the new one.


## Reranking

A vector search compares a question and a passage through two embeddings
made without knowing about each other. A cross-encoder reads the pair
together. It is much better and much more expensive, so Primer searches
widely and cheaply and has the reranker read a shortlist.

### The partner model, and its cost on a CPU

`Qwen3-Reranker-0.6B` is the natural companion to the Qwen3 embedding
model. It scores through its language-model head, comparing "yes" against
"no", rather than through a classification head. llama.cpp's `--reranking`
mode serves it as it is.

On CPU it is the slowest part of a search: about five seconds to read
twenty passages on fifteen cores. On a GPU it takes milliseconds. A node
that cannot spare the time can lower `inference.rerank.candidates`, or turn
`inference.rerank.bundled` off and search without a reranker.

`bundled.maxInputTokens` bounds one question-and-passage pair. A passage of
`ingestion.maxChunkTokens` and a long question fit with room to spare. A
pair that does not fit fails that search's reranking, which then keeps the
vector ordering.

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
last two. `Qwen3-Reranker-0.6B` scores close to binary: in a small check,
an answering passage scored near 1 and an unanswerable question's best
passage under 0.005. The reference configuration below therefore sets
`0.02`.

Err low. A floor that is too high discards correct answers, and a passage
that genuinely answers a question can still score modestly. Remove the
reranker and the floor is compared against cosine similarity instead,
which runs on a different scale entirely, so the two move together.

## Serving flags that matter

The chart's `templates/models.yaml` starts `llama-server` with these, and
its comment says what each one prevents. They matter again when you run the
server yourself.

`--alias` makes the server answer to the name Primer knows the model by.
Without it `/v1/models` reports a file name and Primer's configuration
disagrees with the server.

`--kv-unified-per-slot <tokens>` gives each of the `--parallel` slots the
whole input limit. `--ctx-size` alone is divided between the slots, so a
server started with `--ctx-size 2048 --parallel 4` refuses inputs over 512
tokens, and the parse worker was told 2048 was fine.

`--batch-size` and `--ubatch-size` must hold the longest input. An
embedding is computed over its whole input in one physical batch.

`--embedding --pooling last` for the embedder, `--reranking` for the
reranker.

## Sizing

The Q8_0 weights for each model are about 640 MB, and the chart gives each
server's download cache a 4 GiB volume. Memory grows with `parallel` times
the input limit, because each slot holds a full context. The defaults
request 2 GiB and limit at 4 GiB per server.

Set memory **requests** above the pod's idle footprint, not below it. A
request is what the scheduler packs a node by, so a request under
steady-state usage invites Kubernetes to place a pod somewhere it cannot
live.

On an NVIDIA GPU, change `inference.modelServer.image.tag` to
`server-cuda-<build>` (CUDA 12) or `server-cuda13-<build>` (CUDA 13, which a
GB10 needs), and add a `nvidia.com/gpu` limit to each role's
`bundled.resources`.

## A reference configuration

`deploy/helm/primer/values-selfhosted.yaml` is the configuration described
above, ready to copy:

```bash
helm install primer oci://ghcr.io/harville-labs/charts/primer \
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

The chart runs both model servers itself. To run them outside the release
instead, the same servers are in
[`examples/inference/`](https://github.com/Harville-Labs/Primer/tree/main/examples/inference);
[Connecting model providers](providers.md#using-your-own-embedding-server)
says what to set.
