# Configuration

Every setting is an environment variable prefixed `PRIMER_`. Unknown
settings are rejected at startup rather than ignored, so a typo fails loudly
instead of silently keeping a default.

## Control API

| Variable | Default | Notes |
| --- | --- | --- |
| `PRIMER_AUTH_MODE` | `disabled` | `disabled` or `oidc` |
| `PRIMER_DATABASE_URL` | local PostgreSQL | Migrations are applied out of band |
| `PRIMER_SOURCE_STORE_URL` | `file:///var/lib/primer/sources` | `file://` or `s3://`; see below |
| `PRIMER_MAX_UPLOAD_BYTES` | `104857600` | Enforced while reading. The web app's own `BODY_SIZE_LIMIT` (adapter-node, default 512K) must be raised above this too, or it rejects a large upload before Control ever sees it |
| `PRIMER_BROKER_URL` | unset | Unset leaves uploads queued |
| `PRIMER_INTERNAL_API_TOKEN` | unset | **Unset denies the internal API** |
| `PRIMER_SUBJECT_HEADER` | `X-Forwarded-User` | Set by the proxy |
| `PRIMER_JOB_LEASE_SECONDS` | `300` | Before another worker may re-claim |
| `PRIMER_MAX_JOB_ATTEMPTS` | `5` | Hard bound, independent of workers |

## Ingestion workers

| Variable | Default | Notes |
| --- | --- | --- |
| `PRIMER_BROKER_URL` | local RabbitMQ | Must match Control's |
| `PRIMER_CONTROL_URL` | `http://control-api:8000` | Cluster-internal |
| `PRIMER_RETRIEVAL_URL` | `http://retrieval:8000` | Cluster-internal |
| `PRIMER_SERVICE_TOKEN` | unset | Must match the services' tokens |
| `PRIMER_SOURCE_STORE_URL` | `file:///var/lib/primer/sources` | Same store as Control |
| `PRIMER_ENABLE_OCR` | `true` | Read text from images |
| `PRIMER_PARSE_DEVICE` | `auto` | Where Docling runs its models: `auto`, `cpu`, `cuda`, `mps` or `xpu` |
| `PRIMER_PARSE_THREADS` | `4` | CPU threads for Docling's models; match the CPU the worker is given |
| `PRIMER_EMBEDDING_MODEL` | unset | The model Retrieval embeds with; the parse worker finds its tokenizer from it |
| `PRIMER_CHUNK_TOKENIZER` | from the model | Set for a hosted model that publishes no tokenizer |
| `PRIMER_MAX_CHUNK_TOKENS` | `512` | Ignored without a tokenizer |
| `PRIMER_MAX_CHUNKS_PER_DOCUMENT` | `5000` | Refuse rather than half-index |
| `PRIMER_PARSE_DEADLINE_SECONDS` | `900` | Checked between phases |
| `PRIMER_MAX_RETRIES` | `4` | Control holds its own bound |
| `PRIMER_INDEX_BATCH_SIZE` | `64` | Passages per call to Retrieval; one timeout loses one batch |
| `PRIMER_INDEX_CONCURRENCY` | `4` | Batches of one document in flight at once |
| `PRIMER_GRAPH_EXTRACTION_BASE_URL` | unset | OpenAI-compatible endpoint for knowledge-graph extraction |
| `PRIMER_GRAPH_EXTRACTION_MODEL` | unset | Must support structured output. Unset, or no base URL, turns extraction off |
| `PRIMER_GRAPH_EXTRACTION_API_KEY` | unset | Sent as a placeholder when unset; many local servers need the header |
| `PRIMER_GRAPH_EXTRACTION_TIMEOUT_SECONDS` | `120` | Per passage |
| `PRIMER_GRAPH_EXTRACTION_CONCURRENCY` | `4` | Passages sent to the model at once |

An empty value counts as unset for the extraction settings, so Compose's
`${VAR:-}` leaves extraction off rather than turning it on with nowhere to
send it.

Graph extraction needs Retrieval to have a graph store. A worker extracting
for a Retrieval without `PRIMER_GRAPH_URL` fails the document permanently
and says which setting to change, rather than indexing a graph nobody keeps.

## Retrieval

| Variable | Default | Notes |
| --- | --- | --- |
| `PRIMER_QDRANT_URL` | `http://localhost:6333` | Compose and the chart point it at the Qdrant they run |
| `PRIMER_QDRANT_API_KEY` | unset | For a Qdrant that requires one |
| `PRIMER_QDRANT_INDEX` | `primer_chunks` | The collection every library's passages share |
| `PRIMER_QDRANT_LAYOUT` | `memory` | `memory` for exact vectors in RAM; `disk` for a quantized copy, when they will not fit. Read when the collection is created |
| `PRIMER_EMBEDDING_MODEL` | `text-embedding-3-small` | The name your server answers to |
| `PRIMER_EMBEDDING_DIMENSIONS` | from the model | **Required** for a model Primer does not know |
| `PRIMER_EMBEDDING_QUERY_PREFIX` | from the model | Prompt put before every query |
| `PRIMER_EMBEDDING_DOCUMENT_PREFIX` | from the model | Prompt put before every passage |
| `PRIMER_EMBEDDING_BASE_URL` | unset | Any OpenAI-compatible endpoint |
| `PRIMER_EMBEDDING_API_KEY` | unset | |
| `PRIMER_GRAPH_URL` | unset | Neo4j `bolt://` or `neo4j://` URL. Unset turns the knowledge graph off |
| `PRIMER_GRAPH_USERNAME` | `neo4j` | |
| `PRIMER_GRAPH_PASSWORD` | unset | |
| `PRIMER_GRAPH_DATABASE` | `neo4j` | Community edition has only this one |
| `PRIMER_GRAPH_TIMEOUT_SECONDS` | `30` | Per transaction, and for connecting |
| `PRIMER_GRAPH_HOPS` | `2` | How far search follows relations from the entities a question names. `0` to `2` |
| `PRIMER_GRAPH_COMMUNITIES` | `16` | Topics a library-wide question draws passages from. `1` to `100` |
| `PRIMER_GRAPH_COMMUNITY_MAX_MENTIONS` | `200000` | Largest library a library-wide question is answered for, counted in entity mentions. About 1 KB of Retrieval memory each |
| `PRIMER_GRAPH_CANDIDATES` | `20` | Passages the graph adds to a search before fusion |
| `PRIMER_INTERNAL_API_TOKEN` | unset | **Unset denies the internal API** |


## Source storage

Uploaded files go wherever `PRIMER_SOURCE_STORE_URL` points, through
`fsspec`. Two schemes are usable, because those are the backends Primer
installs:

| Scheme | For |
| --- | --- |
| `file://` | One machine. A `ReadWriteOnce` volume cannot be shared between nodes, so this does not survive a second replica. |
| `s3://` | Anything with more than one replica: S3, MinIO, Ceph, R2. |

`fsspec` imports a backend on first use rather than at startup, so a scheme
whose package is not installed fails on somebody's first upload rather than
when the service starts. Adding another one means adding its package to
`primer-storage` and rebuilding the images; naming it in a URL is not
enough.

Credentials are the backend's own environment rather than Primer settings —
`AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, and `FSSPEC_S3_ENDPOINT_URL`
for anything that is not AWS. In Kubernetes they come from
`sourceStore.existingSecret`, mounted whole into Control and the ingestion
workers and nothing else: those are the only processes that open a source
object.


## Chat

| Variable | Default | Notes |
| --- | --- | --- |
| `PRIMER_CHAT_BASE_URL` | unset | Any OpenAI-compatible endpoint |
| `PRIMER_CHAT_MODEL` | unset | Optional. Which model a new chat starts on |
| `PRIMER_CHAT_API_KEY` | unset | Many local servers ignore it |
| `PRIMER_CHAT_TIMEOUT_SECONDS` | `120` | Per request to the model |
| `PRIMER_CONTROL_URL` | `http://control-api:8000` | Cluster-internal |
| `PRIMER_RETRIEVAL_URL` | `http://retrieval:8000` | Cluster-internal |
| `PRIMER_RETRIEVAL_LIMIT` | `8` | Passages per question; bounds prompt size |
| `PRIMER_CHAT_HISTORY_MESSAGES` | `20` | Earlier turns a follow-up may recall |
| `PRIMER_CHAT_CONTEXT_TOKENS` | `8192` | Window assumed for a model not named below |
| `PRIMER_CHAT_MODEL_CONTEXT_TOKENS` | unset | Per-model windows, as JSON |
| `PRIMER_CHAT_REPLY_TOKENS` | `1024` | Held back out of the window for the answer |
| `PRIMER_CHAT_CHARACTERS_PER_TOKEN` | `4.0` | How the prompt's size is estimated |
| `PRIMER_CHAT_COMPACT_HISTORY` | `true` | Summarize turns instead of dropping them |
| `PRIMER_CHAT_SUMMARY_TOKENS` | `512` | Room reserved for that summary |
| `PRIMER_SERVICE_TOKEN` | unset | Must match the services' tokens |

### Offering more than one model

Chat's model picker lists whatever the configured endpoints report from
`/models`. There is no separate list of Primer's own to keep in step with
them: point the endpoint at the models you want offered, and that is what
the picker shows.

`PRIMER_CHAT_MODEL` is **not required**, and setting it is not how a
deployment is made to work. Its only job is to say which of the offered
models a new conversation starts on - a preference, worth having when an
endpoint serves several and the order it lists them in is not something to
depend on. Left unset, Primer asks the providers what they serve and starts
on the first, and a question always goes to whatever the picker is showing.

Naming a model here that the endpoint does not serve is worse than naming
none: it is offered as though it were available, and every question that
lands on it is refused by the endpoint.

Every model offered this way shares one endpoint, one API key, and one
`PRIMER_RETRIEVAL_LIMIT`. They need not share a context window: see below.


### Fitting the prompt into the context window

A conversation that remembers itself grows, and retrieved passages are
added on top of it. Primer estimates how large a prompt has become and
cuts it down to fit rather than letting the endpoint refuse the turn.

`PRIMER_CHAT_CONTEXT_TOKENS` is the window Primer assumes. Name the models
that differ in `PRIMER_CHAT_MODEL_CONTEXT_TOKENS`, a JSON object keyed by
model name:

```bash
PRIMER_CHAT_CONTEXT_TOKENS=8192
PRIMER_CHAT_MODEL_CONTEXT_TOKENS='{"llama3.1:70b": 131072, "mistral-small": 32768}'
```

`PRIMER_CHAT_REPLY_TOKENS` is held back out of that window so the answer
has somewhere to go. What is left is spent on retrieved passages first and
the conversation's history second, so a long thread loses its oldest turns
before it loses the evidence the current question is grounded in. If not
even one passage fits, the turn fails rather than answering ungrounded.

Primer measures nothing: it estimates `PRIMER_CHAT_CHARACTERS_PER_TOKEN`
characters to the token and charges a small overhead per message. There is
no tokenizer that is right for every OpenAI-compatible endpoint, and the
usual ones fetch vocabularies over the network, which a self-hosted Primer
may not have. The default of 4.0 is deliberately conservative for English
prose; lower it if your users write in a language or a notation that
tokenizes less kindly, and only raise it if you have measured your own
model and want the room back.


### Compacting a long conversation

Dropping the oldest turns keeps a thread inside the window, and what it
costs is the beginning of the thread — the document someone named ten
messages ago, the constraint they gave once and never repeated. With
`PRIMER_CHAT_COMPACT_HISTORY` on, which is the default, the turns that fall
out are summarized on their way out and the summary is carried in their
place.

It is incremental: each pass folds the newly dropped turns into what was
already remembered, so compacting an hour-long conversation does not cost
more each time. The summary is written by the same endpoint that answers, so
it costs a model call — but only on a turn that would otherwise have dropped
something, and never on a conversation that still fits.

`PRIMER_CHAT_SUMMARY_TOKENS` is the room set aside for it, and the length
the summarizer is held to; anything longer is cut. The room is reserved
whether or not a summary exists yet, so writing the first one never costs
the history a turn it was already shown. It is also capped at a quarter of
the window however large you set it: the passages a question is answered
from are the point, and a small model must not spend half its context
remembering a conversation it can no longer ground.

If the summarizer fails, the turn is still answered. The conversation simply
forgets, which is what would have happened anyway.

Turning it off is reasonable for a metered endpoint, or where the extra
latency before the first token matters more than a long thread's memory.
The summary of a conversation already compacted is still carried; forgetting
it would lose the turns it stands for.

Summaries are Primer's own note to itself. They are not shown in the
transcript and no endpoint returns them — the messages they stand for are
all still there, and are what a reader is shown.


## Lab

Lab is optional: the `lab` Compose profile, or `lab.enabled` in the chart.
[Lab](lab.md) says what hardware it needs. An empty value means unset, as
Compose passes one.

| Variable | Chart value | Default | Notes |
| --- | --- | --- | --- |
| `PRIMER_LAB_SERVICE_URL` | set by `lab.enabled` | unset | Read by Control. Set, the web app shows the Lab section. In Compose, set it to `http://lab:8000` with the profile |
| `PRIMER_LAB_MODEL` | `lab.model` | `{"source": "hub", "repository": "Qwen/Qwen3-1.7B"}` | JSON. `{"source": "local", "path": "..."}` loads a directory inside the weights volume |
| `PRIMER_LAB_MODEL_CHOICE` | `lab.modelChoice` | `users` | Who may switch the model every run uses: `users`, `admins`, or `operator` |
| `PRIMER_ADMIN_GROUP` | `auth.adminGroup` | unset | Who counts as an admin for `admins` |
| `PRIMER_LAB_REPOSITORY_PATTERNS` | `lab.repositoryPatterns` | `["*/*"]` | JSON list of shell-style patterns a repository must match |
| `PRIMER_LAB_MAX_MODEL_BYTES` | `lab.maxModelBytes` | `20000000000` | Checked against the hub's file list before anything downloads |
| `PRIMER_LAB_SAES` | `lab.saes` | Qwen3-1.7B and Qwen3-1.7B-Base to Qwen-Scope | JSON object from a model repository to the SAE repository read with it. `{}` maps none. See [SAE features](lab.md#sae-features) |
| `PRIMER_LAB_HUB_ENDPOINT` | `lab.hubEndpoint` | the public hub | A mirror |
| `PRIMER_LAB_DEVICE` | `lab.device` | `auto` | `auto` uses CUDA when torch can see a GPU |
| `PRIMER_LAB_DTYPE` | `lab.dtype` | `auto` | `auto` is `bfloat16` on CUDA and `float32` on CPU |
| `PRIMER_LAB_WEIGHTS_DIR` | | `/models` | Where the volume is mounted; fetched weights go under `hub/` |
| | `lab.gpu` | `0` | GPUs to request. Above zero, the `-cuda` image is used |
| | `lab.persistence.size` | `50Gi` | Nothing is evicted; see [Lab](lab.md) |
| | `lab.nodeSelector`, `lab.tolerations` | none | To place Lab on a GPU node |

## Settings that fail at startup

Some combinations are refused rather than accepted and worked around later:

- `PRIMER_AUTH_MODE=oidc` with an empty subject header. The alternative is a
  deployment that believes it is authenticated but authenticates nobody.

## Changing the embedding model

Changing the model or its dimensions invalidates every existing vector.
Vectors from different models are not comparable, and a store will reject
the wrong width outright.

Reindex your documents after such a change. Primer does not yet do this for
you, and will not detect it on your behalf.
