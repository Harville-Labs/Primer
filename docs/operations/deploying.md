# Deploying

## What you need

| Component | Required | Notes |
| --- | --- | --- |
| PostgreSQL | yes | With `pgvector` if using it as the vector store; Compose and the chart can each run one |
| Object storage | yes | A directory is fine for a single node |
| RabbitMQ | for ingestion | Without it, uploads stay `queued` |
| Embedding endpoint | for retrieval | Any OpenAI-compatible API |
| `oauth2-proxy` | for multi-user | Not needed with auth off |
| Qdrant | optional | Alternative to pgvector |
| Neo4j | optional | For the knowledge graph; Compose and the chart can each run one |

## Running it with Compose

```bash
cp deploy/compose/env.example deploy/compose/.env
# fill in the passwords and your model endpoint, then:
docker compose -f deploy/compose/compose.yaml up --wait
deploy/compose/scripts/smoke.sh
```

!!! danger "The Compose profile is for one person, and stays that way"
    It runs with authentication off: every request is the same fixed local
    user, so anyone who can reach the published ports *is* that user. Ports
    are bound to `127.0.0.1` for that reason.

    Multi-user Primer is the [Kubernetes deployment](kubernetes.md), which
    is where the ingress and the identity provider live. A proxy bolted onto
    Compose would look multi-user while one forgotten published port made
    the authentication decorative.

!!! warning "Changing a password later needs the volume gone"
    PostgreSQL reads `POSTGRES_PASSWORD` only when it first initialises its
    data directory. Editing it afterwards leaves the stored password
    unchanged, and every service then fails with `password authentication
    failed for user "primer"`.

    ```bash
    docker compose -f deploy/compose/compose.yaml down -v
    ```

    That deletes the database along with the volume, so only do it on a stack
    you can recreate.

## Using your own PostgreSQL with Compose

Compose runs PostgreSQL for you unless `PRIMER_DATABASE_URL` is set. Set it
in `.env` and every service, and every migration, uses that server instead.
The bundled one does not start, and `POSTGRES_PASSWORD` is not needed:

```bash
PRIMER_DATABASE_URL=postgresql://primer:...@db.example.internal:5432/primer
```

The server needs pgvector installed. The migrations create the `vector`
extension and the `control`, `chat` and `vectors` schemas, so the user needs
to own the database, and either be allowed to create the extension or find
it already created by an administrator. Write the URL as plain `postgresql://`; each service
chooses its own driver from it. Characters such as `@`, `/` and `:` in the
password must be percent-encoded.

A container reaches a server on the host as `host.docker.internal`, not
`localhost`.

## Turning on the knowledge graph with Compose

The graph runs under a Compose profile, so a stack without one starts no
Neo4j. Uncomment the graph block in `.env`:

```bash
COMPOSE_PROFILES=graph
PRIMER_GRAPH_URL=bolt://neo4j:7687
PRIMER_GRAPH_PASSWORD=...   # openssl rand -hex 32
PRIMER_GRAPH_EXTRACTION_BASE_URL=http://host.docker.internal:11434/v1
PRIMER_GRAPH_EXTRACTION_MODEL=qwen2.5:7b-instruct
```

Neo4j's port is not published; only Retrieval and the `migrate-graph` step
reach it. Like PostgreSQL, it reads its password only when it first creates
its volume. Documents indexed before the graph was on need a reindex to get
one.

## Published images

Each push to `main` publishes three images to GHCR, so nothing has to be
built locally:

| Image | Contains |
| --- | --- |
| `ghcr.io/isaiah-harville/primer-api` | Control, Chat, Retrieval |
| `ghcr.io/isaiah-harville/primer-worker` | The ingestion worker |
| `ghcr.io/isaiah-harville/primer-web` | The web app |

Tagged `latest` and by commit SHA. Pull requests build them but do not push:
a broken Dockerfile fails review rather than after merge, and an image
pushed from a pull request would let anyone who can open one publish under
this repository's name.

The Compose profile builds from source rather than pulling, because a local
checkout is usually one you are changing.

## The images



Two Python images, not one.

| Image | Base | Contains |
| --- | --- | --- |
| `Dockerfile.python` | Alpine + uv | Control, Chat, Retrieval |
| `Dockerfile.worker` | Debian slim + uv | The ingestion worker |
| `Dockerfile.gui` | Node slim | The web app |

The split is not tidiness. Docling's layout models run on torch, and torch
publishes no musl wheels — so the worker needs a glibc base and a machine
learning stack, while the three API services need neither. Sharing one image
would put roughly two gigabytes into every service that never imports it.

Both Python images are a single stage. A build/runtime split earns its keep
when the build needs compilers or caches the runtime should not have; here
everything installs from a wheel and uv's cache is a mount rather than a
layer. Each `uv sync` writes a complete virtualenv that stays in its layer,
so a separate dependency-install layer nearly *doubled* the API image.

## Running the processes directly

```bash
uv run --package primer-control uvicorn primer_control.app:create_app --factory
uv run --package primer-retrieval uvicorn primer_retrieval.app:create_app --factory
uv run --package primer-chat uvicorn primer_chat.app:create_app --factory
uv run --package primer-ingestion celery -A primer_ingestion.worker worker \
  -Q ingestion.parse,ingestion.embed,ingestion.index,ingestion.delete
```

Workers can be split by queue. Parsing is CPU-heavy and holds whole
documents in memory; embedding is mostly waiting on a network call. Running
them as separate deployments lets each scale on what actually constrains it.

## Order of operations

1. Create the database and run [migrations](migrations.md).
2. Start Retrieval, so workers have somewhere to send passages.
3. Start Control.
4. Start workers.

Starting out of order is not harmful — jobs wait, and stages retry — but
documents will sit in `queued` until the chain is complete.

## Putting it behind a proxy

This applies to the Kubernetes deployment, not to Compose.

Primer trusts the identity headers it receives.

!!! danger "Bind so that only the proxy can reach Control"
    If the Control API is reachable directly, anyone who can reach it can
    set the identity headers themselves and act as any user. This is the
    single most important thing to get right in a shared deployment.

The proxy must also not route `/internal` from outside. Those endpoints are
guarded by a service credential, but they are not meant to be exposed.

## Health checks

Both services expose `/health/live`. Control also exposes `/health/ready`,
which reports unready when it cannot reach PostgreSQL.

Use liveness for restarts and readiness for load-balancer membership: a
Control instance that cannot reach its database should stop receiving
traffic without being killed and restarted in a loop.

## Scaling

Every service is stateless and can run several replicas. The design assumes
it:

- Two workers handed the same message cannot both do the work; the loser is
  told the stage is already claimed.
- A worker that dies mid-stage has its lease expire, and another picks the
  job up.
- Concurrent uploads of identical bytes converge on one stored object.
