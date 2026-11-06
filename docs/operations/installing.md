# Installing

This page takes you from nothing to a Primer that answers a question with
citations. It covers the decisions in order and links to the page that
explains each one in depth. It does not repeat those pages.

There are two ways to run Primer:

| | Docker Compose | Kubernetes |
| --- | --- | --- |
| Who it is for | One person on one machine | Several people, signing in |
| Authentication | None. Every request is one fixed local user | OIDC through `oauth2-proxy` |
| Bundled | PostgreSQL, RabbitMQ, Qdrant, Neo4j, embedder, reranker | Qdrant, Neo4j, embedder, reranker; PostgreSQL optionally |
| You bring | A chat model | A chat model, RabbitMQ, object storage, an identity provider |

Compose stays single-user, and it does not get a login screen. If more than
one person will use the deployment, use Kubernetes.
[Deploying](deploying.md#running-it-with-compose) explains why.

## What every deployment needs

- **A chat model.** Any OpenAI-compatible endpoint: vLLM, Ollama,
  llama.cpp's `llama-server`, or a hosted API. Primer doesn't run one for
  you. The same model reads the knowledge graph out of each passage unless
  you name a separate one, so it must support structured output.
- **Disk for the models Primer does run.** The bundled embedder and
  reranker download about 1.3 GB on first start. The parse worker
  downloads Docling's layout models, about half a gigabyte.
- **Time for the first start.** The downloads above can take minutes on a
  slow link. Later starts read them from a volume.

Everything else is optional and can be added later:

| Addition | What it adds | Where it is explained |
| --- | --- | --- |
| A vision model | Figures are found by what they show, not just by the text in them. Studio images can be added to a library | [What a figure shows](models.md#what-a-figure-shows) |
| Lab | A tab for looking inside a small open model | [Lab](lab.md) |
| Studio | Making and editing images, through an image server you run | [Studio](studio.md) |
| A web search MCP server | The model can search the web, with each call approved by the person asking | [Tools](tools.md) |
| Your own embedder or reranker | A vLLM or other model server you already run, or a GPU | [Connecting model providers](providers.md#using-your-own-embedding-server) |

## Docker Compose

You need Docker with Compose v2 and a checkout of this repository. Run
every command from the repository root.

**On an arm64 machine** — a DGX Spark, another GB10 box, or Apple Silicon —
everything a default install runs is native: Primer's images are built for
arm64, and so are PostgreSQL, RabbitMQ, Qdrant, Neo4j and the bundled
model server, llama.cpp's `llama-server`. Nothing needs replacing.

**Where data is kept.** Uploads and each database live in named Docker
volumes unless you name a directory for them in `.env`, for example on a
NAS mount or a larger disk. [Storage](storage.md) covers the choices.

### 1. Fill in `.env`

```bash
cp deploy/compose/env.example .env
```

Four values are required, and Compose refuses to start without them. Use a
different random value for each:

```bash
openssl rand -hex 32
```

| Variable | What it is |
| --- | --- |
| `POSTGRES_PASSWORD` | The bundled PostgreSQL's password. Not needed if `PRIMER_DATABASE_URL` names your own |
| `RABBITMQ_PASSWORD` | The bundled RabbitMQ's password |
| `PRIMER_GRAPH_PASSWORD` | The knowledge graph's Neo4j password. Required even with your own Neo4j, as that server's password |
| `PRIMER_INTERNAL_API_TOKEN` | Shared by Primer's services for their internal APIs. Without it ingestion fails |

!!! warning "Choose the passwords before the first start"
    PostgreSQL and Neo4j read their passwords only when they first create
    their volumes. Changing one in `.env` later leaves the stored password
    as it was, and the services that use it fail to connect.
    [Deploying](deploying.md#running-it-with-compose) has the recovery.

Keep `.env` at the repository root. Compose reads its own settings, such as
`COMPOSE_PROFILES`, only from the directory it was started in.

### 2. Point it at a chat model

```bash
PRIMER_CHAT_BASE_URL=http://host.docker.internal:11434/v1   # Ollama on the host
PRIMER_CHAT_API_KEY=                                       # only if it needs one
```

A container reaches a server on your machine as `host.docker.internal`,
not `localhost`. On Linux that name needs an `extra_hosts` entry; see
[Reaching a server on the host from
Compose](providers.md#reaching-a-server-on-the-host-from-compose).

`PRIMER_CHAT_MODEL` is optional. Primer asks the endpoint what it serves.
You can also leave the base URL unset and add the model on the settings
page once Primer is running. [Connecting model
providers](#connecting-model-providers) below compares the two.

### 3. Turn on what you want

The optional parts are switched on in `.env`:

```bash
# A vision model, for figures and Studio images. Any OpenAI-compatible
# endpoint that accepts images in chat messages.
PRIMER_VISION_BASE_URL=http://host.docker.internal:11434/v1
PRIMER_VISION_MODEL=qwen2.5vl:7b

# Lab. The profile starts it; the URL tells Control it is there.
COMPOSE_PROFILES=lab
PRIMER_LAB_SERVICE_URL=http://lab:8000

# Studio. It also needs an image server: see Studio.
PRIMER_STUDIO_ENABLED=true
PRIMER_IMAGES_BASE_URL=http://host.docker.internal:8188
```

Lab is the only optional part that runs as a Compose profile. On an NVIDIA
GPU, add `-f deploy/compose/compose.gpu.yaml` to every `docker compose`
command; [Lab](lab.md#docker-compose) has the details. Studio's
`PRIMER_IMAGES_CAPABILITIES` declares what your image server can do, and
[Studio](studio.md#declaring-what-it-serves) explains it.

Web search tools are configured on the chart. Compose doesn't pass
`PRIMER_TOOLS` to Chat.

### 4. Start it

```bash
docker compose up --wait
```

`--wait` returns once every service passes its health check. The first time,
that includes downloading the embedder and reranker. Then open
<http://localhost:3000>.

## Kubernetes

You need a cluster with an ingress controller (the chart's rules are written
for ingress-nginx), Helm 3, and an OIDC provider. Authentik is the tested
provider, but any OIDC provider works.
[Kubernetes](kubernetes.md) is the full reference. This section is the
shortest path through it.

### 1. Prepare what the chart references

The chart makes no secrets except its own internal token, so create these
first. [Secrets you provide](kubernetes.md#secrets-you-provide) lists every
key.

- **PostgreSQL.** Either a database you run, as a Secret holding
  `database-url`, or the bundled one (`postgresql.bundled.enabled=true`),
  as a Secret holding `postgres-password`. The bundled one holds every
  library, so back up its volume.
  [The database](kubernetes.md#the-database) compares the two.
- **RabbitMQ.** The chart doesn't run one. Create a Secret holding
  `broker-url`, an `amqp://` URL.
- **Object storage.** `sourceStore.url` is an fsspec URL, `s3://` by
  default. Its credentials go in the Secret named by
  `sourceStore.existingSecret`. See [Object
  storage](kubernetes.md#object-storage).
- **An OIDC client.** Register a confidential client with the redirect URL
  `https://<your host>/oauth2/callback`, and make sure the provider sends a
  `groups` claim. Create a Secret holding `client-secret` and
  `cookie-secret`. The cookie secret must be 16, 24, or 32 bytes:
  `openssl rand -hex 16`.

```bash
kubectl create secret generic primer-oidc \
  --from-literal=client-secret='...' \
  --from-literal=cookie-secret="$(openssl rand -hex 16)"
```

### 2. Write a values file

```yaml
ingress:
  host: primer.example.com
  tlsSecret: primer-tls

auth:
  # Members of this group may open the settings page. Unset means nobody.
  adminGroup: primer-admins
  oidc:
    issuerUrl: https://auth.example.com/application/o/primer/
    clientId: primer
    existingSecret: primer-oidc

postgresql:
  existingSecret: primer-postgres
rabbitmq:
  existingSecret: primer-rabbitmq
sourceStore:
  url: s3://primer-sources
  existingSecret: primer-s3

inference:
  chat:
    baseUrl: http://vllm.models.svc:8000/v1
    model: Qwen/Qwen3-8B          # optional
    existingSecret: ""            # a Secret with chat-api-key, if it needs one
```

`inference.chat` can be left out and the model added on the settings page
instead; see [Connecting model providers](#connecting-model-providers).

That is a complete deployment. The release runs Qdrant, Neo4j, the embedder,
and the reranker itself, and generates Neo4j's password when you name none.

To add the optional parts, put them in the same file:

```yaml
inference:
  vision:                         # figures and Studio images
    baseUrl: http://vision.models.svc:8000/v1
    model: Qwen/Qwen2.5-VL-7B-Instruct

lab:
  enabled: true                   # see Lab for GPU nodes

tools:
  enabled: true
  search:
    enabled: true                 # runs mcp-searxng; sends queries to public search engines

studio:
  enabled: true                   # also needs inference.images; see Studio
```

[Lab](lab.md#kubernetes), [Tools](tools.md#bundled-search-server), and
[Studio](studio.md#kubernetes) each describe their part.

### 3. Install

```bash
helm install primer oci://ghcr.io/harville-labs/charts/primer \
  --version <release> \
  --namespace primer --create-namespace \
  -f values.yaml
```

Pick `<release>` from the repository's releases. A chart installed from a
checkout needs image tags, since a development `appVersion` names nothing
that was published. See [Installing from the
registry](kubernetes.md#installing-from-the-registry).

Migrations run as a pre-install hook, before any service starts.
[Migrations](migrations.md) covers running them yourself and rolling back.

!!! danger "Keep Control behind the proxy"
    Primer trusts the identity headers `oauth2-proxy` sets. Every service
    is `ClusterIP`, and the ingress strips inbound copies of those headers.
    If you replace the ingress, keep that rule.
    [How identity works](kubernetes.md#how-identity-works) explains why.

## Connecting model providers

A *provider* is an OpenAI-compatible endpoint Primer can answer from. There
are two ways to give Primer one, and a deployment can use both.

| | In `.env` or the values file | On the settings page |
| --- | --- | --- |
| Set by | Whoever deploys it | An administrator, in the browser |
| Changed by | Editing configuration and restarting | Adding, disabling, or removing it on the page |
| Used for the knowledge graph | Yes | No. The index worker reads only configuration |
| API key kept | In `.env` or a Secret | Encrypted in Primer's database |

A provider from configuration is listed on the settings page too, marked
*From the deployment*. It can't be changed there.

### From configuration

This is step 2 of the Compose install, or `inference.chat` in the values
file. Use it for the model the deployment can't work without, because it is
also the model that reads the knowledge graph out of each passage.

[Connecting model providers](providers.md#chat) has the serving flags for
vLLM, Ollama, and llama.cpp, and the URLs for hosted APIs. [Choosing
models](models.md) covers which models to pick.

### On the settings page

Open **Settings**. Under **Inference providers**, the form below the list
adds one. It asks for:

- **A name**, which is what people see when they choose a model.
- **The base URL**, ending in `/v1`. From Compose, a server on your machine
  is `http://host.docker.internal:<port>/v1`.
- **An API key**, if the endpoint needs one. Most local servers don't.
- **Whether it serves chat.** Turn this off for an endpoint that only makes
  images for [Studio](studio.md).
- **Whether it returns logprobs.** Declare it only if the server does;
  Primer doesn't probe for it. See [Declaring
  logprobs](configuration.md#declaring-logprobs).

A key typed here is encrypted before it is stored. The chart generates the
encryption key. Compose doesn't, so add one to `.env` before you save a
provider that needs a key:

```bash
PRIMER_SETTINGS_ENCRYPTION_KEY=$(openssl rand -hex 32)
```

Without it the page refuses to store a key rather than keep it in the
clear. Keep the value: if it changes, stored keys can't be read and have
to be typed in again.

A provider added here answers questions, but the index worker can't read
it. If it is your only chat model, the knowledge graph needs
`PRIMER_GRAPH_EXTRACTION_*`; see [The knowledge graph with
Compose](deploying.md#the-knowledge-graph-with-compose).

### Embeddings, reranking, and vision

The embedder and reranker are bundled, so a fresh install needs neither.
To use your own, see [Using your own embedding
server](providers.md#using-your-own-embedding-server) and [Using your own
reranker](providers.md#using-your-own-reranker-or-none). The vision model is
set only in configuration: step 3 of the Compose install, or the optional
values in step 2 of the Kubernetes one. [What a figure
shows](models.md#what-a-figure-shows) explains what it adds.

## Checking that it works

1. **Every service is up.** In Compose, `docker compose ps` shows each
   service `healthy`. On Kubernetes, `kubectl get pods -n primer` shows each
   pod `Running` and ready.
2. **Sign in.** On Kubernetes, the first visit goes through your identity
   provider. In Compose you are the local user straight away, and a banner
   says authentication is off.
3. **Read the settings page.** Open **Settings** in the sidebar. You need
   to be in `auth.adminGroup`, unless authentication is off. It lists what
   Primer is connected to and whether each one is answering, and the chat
   providers it can answer from. If no chat provider is listed, step 2 of
   your install didn't take. Under **Suggestions** it lists the optional
   parts this deployment doesn't have yet, what each would add, and the
   settings that turn it on.
4. **Index a document and ask about it.** Create a library, upload a PDF,
   and wait for its status to reach `ready`. Then ask a question it answers, and
   check that the answer cites a passage. [Getting
   started](../guide/getting-started.md) walks through this from a reader's
   side.

In Compose, `deploy/compose/scripts/smoke.sh` runs the last step for you.
It creates a library, indexes a document, asks a question, checks the
citation, and deletes the library.

If a document stays `queued`, ingestion can't reach its broker or a worker
isn't running. If it fails, the document shows why. [When something goes
wrong](../guide/troubleshooting.md) lists the common causes.

## Next steps

- **Choose better models.** [Choosing models](models.md) covers the
  embedder, the chunk size, reranking, and what each one costs.
- **Change the embedding model before your first upload.** It fixes the
  width of every stored vector, so changing it later means reindexing.
  See [Changing the embedding model](configuration.md#changing-the-embedding-model).
- **Scale it.** [Scale](kubernetes.md#scale) covers replicas, disruption
  budgets, and autoscaling the workers on their queues.
- **Upgrade it.** Read [Upgrading](upgrading.md) before moving to a new
  release. It lists the changes that need a step from you.
- **Every setting** is in [Configuration](configuration.md).
