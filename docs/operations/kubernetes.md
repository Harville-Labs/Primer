# Kubernetes

This is the multi-user deployment. Compose is deliberately single-user; here
is where the ingress, the identity provider, and the trusted-header boundary
live.

```bash
helm install primer deploy/helm/primer \
  --set ingress.host=primer.example.com \
  --set postgresql.existingSecret=primer-postgres \
  --set rabbitmq.existingSecret=primer-rabbitmq \
  --set auth.oidc.issuerUrl=https://auth.example.com/application/o/primer/ \
  --set auth.oidc.clientId=primer \
  --set auth.oidc.existingSecret=primer-oidc \
  --set inference.chat.baseUrl=http://vllm.ai.svc:8000/v1 \
  --set inference.chat.model=llama-3.1-8b-instruct \
  --set inference.embeddings.baseUrl=http://vllm.ai.svc:8000/v1 \
  --set inference.embeddings.model=Qwen/Qwen3-Embedding-0.6B
```

## Installing from the registry

Releases publish the chart to GHCR, so an install does not need a checkout:

```bash
helm install primer oci://ghcr.io/isaiah-harville/charts/primer \
  --version 1.4.0 \
  --set ingress.host=primer.example.com \
  ...
```

Only a release publishes a chart. Nothing is pushed from `main` or from a
pull request: a chart is how someone installs Primer, and there is no
version number that honestly describes "whatever landed this morning".

Images are different. Each push to `main` publishes `latest`, which is for
trying things rather than running them, alongside a `sha-<commit>` tag for
pinning one exact build. A release publishes its own version - `1.4.0`, and
`1.4` for the newest patch of that line - and moves no alias, so `latest`
never quietly becomes a release.

Installing the chart from a checkout therefore needs an image tag, because
the tag defaults to the chart's `appVersion` and a development `appVersion`
names nothing published:

```bash
helm install primer deploy/helm/primer \
  --set image.api.tag=latest \
  --set image.worker.tag=latest \
  --set image.web.tag=latest \
  ...
```

## Secrets you provide

The chart creates exactly one secret and references the rest. A chart that
generated database passwords would put them in the release, in `helm get
values`, and in whatever stores your release history.

| Secret | Keys |
| --- | --- |
| `postgresql.existingSecret` | `database-url`, or `postgres-password` with `postgresql.bundled.enabled` |
| `rabbitmq.existingSecret` | `broker-url` |
| `auth.oidc.existingSecret` | `client-secret`, `cookie-secret` |
| `inference.*.existingSecret` | API keys, if your endpoint needs them |
| `vectorStore.existingSecret` | `qdrant-api-key`, if your Qdrant needs one |
| `graph.existingSecret` | `graph-password`, when the graph is on |
| `graph.extraction.existingSecret` | `graph-extraction-api-key`, if the extraction endpoint needs one |

The one it does create is Primer's own cluster-internal service token, which
means nothing outside the release. It is generated once and preserved across
upgrades — regenerating it on every `helm upgrade` would leave in-flight
workers holding a credential the API had just stopped accepting.

## How identity works

Primer never validates a token. `oauth2-proxy` verifies the user against
your provider and injects headers naming them, which Primer trusts.

That trust is why two things in this chart matter more than anything else:

**Every service is `ClusterIP`.** Nothing is exposed outside the cluster.
Anything that could reach the Control API directly could set the identity
headers itself and be any user.

**The ingress strips inbound identity headers.** A request arriving with its
own `X-Forwarded-User` has it removed before it reaches the proxy. Both
are asserted by tests that render the chart and read the result.

!!! danger "If you replace the ingress, keep the header stripping"
    A different ingress controller needs the same rule expressed its own
    way. Without it, anyone who can reach the ingress is any user they care
    to name — and nothing about the deployment will look wrong.

Setting `auth.mode=disabled` removes the proxy entirely rather than putting
it in a permissive mode, so there is no half-configured edge that looks like
it is authenticating.

## Isolation inside the cluster

`ClusterIP` and the ingress keep strangers out of the cluster. Inside it,
every pod could reach every service: any workload in any namespace could
open a connection to Retrieval, whose internal API indexes and deletes
vectors and is guarded by a shared token and nothing else.

`networkPolicies.enabled` — on by default — closes that. A deny-ingress
policy covers every pod the chart runs, and one allow policy per service
names what actually calls it:

| Service | May be reached by |
| --- | --- |
| The proxy (or the web app, with auth disabled) | Any namespace, so the ingress controller can reach it |
| Web | The proxy |
| Control | Web, Chat, both workers |
| Chat | Web |
| Retrieval | Chat, both workers |
| Qdrant, when the chart runs it | Retrieval |

Those paths are asserted by tests that render the chart and read the
result, so a new dependency has to be declared rather than discovered in
production.

!!! warning "A policy needs a CNI that enforces it"
    On a cluster whose network plugin does not implement NetworkPolicy —
    plain kubenet, or Flannel without an add-on — these objects are accepted
    by the API server and do nothing whatsoever. Nothing in the chart can
    detect that, and an unenforced policy looks exactly like an enforced
    one. Check your plugin rather than assuming, and if it does not enforce,
    know that this section does not apply to you.

The one rule wider than it needs to be is the door: an ingress controller
runs in a namespace this chart cannot know the name of, so by default any
namespace may reach the entry point. Narrow it when you know where yours
runs:

```yaml
networkPolicies:
  ingress:
    - namespaceSelector:
        matchLabels:
          kubernetes.io/metadata.name: ingress-nginx
```

**These are ingress rules only.** An egress policy would have to name
PostgreSQL, the broker, the inference endpoint, and object storage — every
one of them an address you supply, most of them possibly outside the
cluster, none of them known here. Written blind it would either break the
deployment or allow everything, and an egress rule that allows everything is
a comment pretending to be a control. Add your own through
`networkPolicies.extra`, which takes whole policy objects.

## Who each workload runs as

Every component has its own ServiceAccount, and none of them is granted
anything: nothing Primer runs calls the Kubernetes API. They exist so that
one compromised workload does not inherit whatever the namespace default can
do, and so an audit log can tell a worker from the web app.

No pod mounts a service account token, for the same reason — a credential in
a filesystem nobody reads is one waiting for whoever gets into the container
next.

The reason to annotate one of these is usually cloud IAM, and only the two
workloads that open a source object have any business assuming a role that
can read the bucket:

```yaml
serviceAccounts:
  perComponent:
    control:
      eks.amazonaws.com/role-arn: arn:aws:iam::111122223333:role/primer-sources
    worker-parse:
      eks.amazonaws.com/role-arn: arn:aws:iam::111122223333:role/primer-sources
```

Set `serviceAccounts.create=false` for a cluster that manages its own
accounts. The pods fall back to the namespace default — still with no token
mounted, since the default account's token is the one worth mounting least.

## Migrations

Run as a `pre-install,pre-upgrade` hook, once per release. An init container
would run once per pod, so a three-replica rollout would have three
processes racing to alter the same schema.

A failed migration fails the release before any new pod starts. With a
bundled PostgreSQL the first install is the exception: the server does not
exist before the release does, so the hook is `post-install,pre-upgrade`
and waits up to five minutes for the server to accept connections.

## The database

Name a Secret holding the URI of a PostgreSQL you run. It needs no
extensions:

```yaml
postgresql:
  existingSecret: primer-postgres     # key: database-url
```

Or have the chart run one:

```yaml
postgresql:
  bundled:
    enabled: true
    persistence:
      size: 20Gi
  existingSecret: primer-postgres     # key: postgres-password
```

`postgresql.bundled.enabled` runs `postgres:17`, the image Compose uses, as
a one-replica StatefulSet. It runs under the same hardening as Primer's own
pods, and the `allow-postgresql` NetworkPolicy admits only the workloads that
are given the database: Control, Chat, the workers, and the migration hooks.
Retrieval keeps nothing in PostgreSQL and is not given it. Its user and database are both `primer`. The
password reaches the clients as `PGPASSWORD` rather than inside the URI, so
it may contain any characters.

- **It holds the library.** Unlike the bundled Neo4j, nothing rebuilds it:
  documents, conversations and settings live here. Back up its volume, or
  use a PostgreSQL you already back up. There is one replica and no
  failover; [ADR 0003](../adr/0003-bundle-postgresql.md) says why that is
  offered anyway.
- **The Secret sets the password once.** PostgreSQL reads it when it first
  initialises its volume. To change it later, change it in PostgreSQL first,
  then update the Secret and restart the deployments:

    ```bash
    kubectl exec -it primer-postgresql-0 -- psql -U primer -d primer \
      -c "ALTER USER primer PASSWORD 'new'"
    ```

- **Switching is a data migration.** Moving between bundled and external
  does not move the data. Dump and restore it before changing the setting.

## The vector store

Passages and their embeddings live in Qdrant. By default the chart runs one,
so an install names nothing here. To use a Qdrant you run instead:

```yaml
vectorStore:
  url: https://qdrant.vectors.svc:6333
  existingSecret: primer-qdrant       # key: qdrant-api-key, if it needs one
  qdrant:
    enabled: false
```

Setting `url` while the bundled one is on, or turning the bundled one off
without a `url`, is refused at render time.

`vectorStore.qdrant.enabled` runs one replica with a persistent volume, under
the same hardening as Primer's own pods. Telemetry is off, only its REST
port is published, and the `allow-qdrant` NetworkPolicy admits Retrieval and
nothing else. With `existingSecret` set, the bundled Qdrant requires the key
and Retrieval sends it, both read from the same Secret.

- **It holds derived data.** Losing the volume costs a reindex, not a
  library, which is why it is on by default when PostgreSQL is not.
- **Size the volume for the library.** Each passage takes about 4 KiB per
  1024 dimensions, plus its text and index overhead.
- **Size memory for the passage count, not the volume.** By default
  (`vectorStore.layout: memory`) every vector is held in RAM at full
  precision, which is exact: allow about 4.5 KiB per passage at 1024
  dimensions, so the default 2 GiB limit holds some four hundred thousand
  passages. Passage text stays on disk either way.
- **Switch to `layout: disk` when the vectors will not fit.** Full vectors
  are then memory-mapped from disk and search runs against an int8 copy in
  RAM, about 1.5 KiB per passage: fifty million passages need around 80 GiB
  rather than 220. Qdrant rescores the shortlist against the full vectors,
  so ranking stays close, though not identical. Memory beyond that goes to
  the page cache, which makes rescoring faster.
- **Collections keep the layout they were created with.** Changing
  `layout` means deleting the collection and reindexing.
- **The embedding model is fixed once a collection exists.** The collection
  keeps the width it was created with. Changing
  `inference.embeddings.model` means deleting the collection and reindexing;
  Retrieval refuses mismatched vectors with a message that says so rather
  than storing them.
- **Upgrading from pgvector is a reindex.** Nothing copies vectors out of
  PostgreSQL. [ADR 0004](../adr/0004-qdrant-only.md) says what to drop
  afterwards.

## Scale

**Parsing and indexing are separate deployments.** Parsing is CPU-heavy and
holds whole documents in memory; embedding and indexing mostly wait on a
network call. Splitting them by queue lets each scale on what constrains it,
and stops a burst of uploads starving the stage that finishes them.

**Each worker states its own concurrency and pool.** `workers.<name>.concurrency`
is how many documents a pod works on at once, and `workers.<name>.pool` is
whether each one gets a process or a thread. Both are passed to Celery
explicitly. Left out, Celery sizes its pool from the node's CPU count — so a
worker scheduled onto a large machine forks a child per CPU against a memory
limit set for a couple of them, and is killed on startup for a reason that
has nothing to do with the work it was given.

Parsing runs one document at a time, in a process: it holds a whole document
in memory and loads Docling's layout models, and two of those in one
container is two copies of both. Scale that component with `replicas`.

Its models run on torch, which otherwise starts a thread per core on the
node rather than per core the pod was granted, and then contends with
itself. `workers.parse.accelerator.threads` sets that number; keep it
level with the CPU in `workers.parse.resources`. `accelerator.device` is
`auto`, which uses a GPU when the pod is scheduled with one — layout
analysis is several times faster there, and is the bulk of parsing a PDF.

Indexing runs eight documents on threads, because every task on that worker
waits on a model endpoint, and a process per wait is an interpreter spent
on a socket. Each document also sends four batches at once
(`PRIMER_INDEX_CONCURRENCY`), so the embedding endpoint sees up to
thirty-two requests from each index pod. Size that endpoint for it, or
lower one of the two numbers. Raise concurrency only alongside the
`resources` beside it.

**The parse worker keeps a model cache.** Docling downloads its layout
models on first use, and without a persistent volume every pod restart
re-downloads about half a gigabyte from a third-party host.

**Source storage must be shared.** More than one replica means object
storage: a `ReadWriteOnce` volume cannot be mounted across nodes.

### Staying up while a node goes away

A Deployment says how many pods there should be. Nothing said how many
there must be while a node is drained, and a drain evicts as fast as the
pods will go — so a two-replica service could lose both at once.

Every component running more than one replica gets a `PodDisruptionBudget`
of one pod at a time, the proxy included. Components running a single
replica get none, deliberately: any budget at all makes that pod
unevictable, and a drain that never finishes is a worse failure than the
restart it was avoiding. The rule is the replica count rather than the
component, so a worker scaled to three is protected like anything else.

`podTopologySpread` can place each component's replicas on separate topology
domains. It is off by default; `ScheduleAnyway` is the safe default for a
single-node cluster. Set `whenUnsatisfiable: DoNotSchedule` when the cluster
has enough nodes to satisfy the rule. On Kubernetes 1.27 or newer,
`matchLabelKeys: [pod-template-hash]` keeps pods from an older ReplicaSet from
blocking a rollout.

### Autoscaling

Off unless you ask for it, and then only for the services whose load is
requests:

```yaml
autoscaling:
  chat:
    enabled: true
    minReplicas: 2
    maxReplicas: 8
```

Per component, merged over the shared defaults at the top of
`autoscaling`, so turning one service on does not mean restating every
threshold. **It scales on CPU utilisation**, against the requests in
`chat.resources`; a Deployment under an autoscaler stops stating its own
replica count, because setting both means every `helm upgrade` writes the
configured number back and the autoscaler undoes it.

Memory is available as a second metric and left off. A Python service that
has grown does not give the memory back, so it scales up readily and down
almost never — turn it on if you have measured your own workload, not
because it sounds thorough.

#### The workers scale on their queues

Not on CPU. An embedding worker spends its time waiting on a network call,
so its CPU is near zero precisely when the backlog is longest: a CPU
autoscaler would add pods while they compute and none while they fall
behind. Queue length describes them, and [KEDA](https://keda.sh) reads it:

```yaml
workers:
  index:
    queueScaling:
      enabled: true
      minReplicas: 1
      maxReplicas: 8
```

This needs KEDA installed first. The chart cannot detect it, and a
`ScaledObject` on a cluster without the CRD fails the install. KEDA reads
the broker URL from the same Secret the workers use (`rabbitmq.existingSecret`),
so there is no second credential to rotate.

A pod is added for every `concurrency` documents waiting — that is, when
the pods already running are full. `messagesPerReplica` overrides it. A
scaled worker stops stating `replicas`, for the same reason the services
do, and its disruption budget follows `minReplicas`.

The parse worker's model cache is sized for `maxReplicas`, not the pods
running now: above one it is `ReadWriteMany`, and the chart refuses a
`ReadWriteOnce` cache that KEDA could be asked to share.

## The knowledge graph

Off until you choose a Neo4j for it. The chart can run one for you:

```yaml
graph:
  neo4j:
    enabled: true
    persistence:
      size: 10Gi
  existingSecret: primer-neo4j        # key: graph-password, 8+ characters
  extraction:
    baseUrl: http://vllm.models.svc:8000/v1
    model: Qwen/Qwen2.5-7B-Instruct
```

Or point it at one you already run, with `graph.url` in place of
`graph.neo4j`. Setting both is refused.

```yaml
graph:
  url: bolt://neo4j.graph.svc:7687
  existingSecret: primer-neo4j
```

Either way, Retrieval is given the graph and a `migrate-graph` hook applies
its constraints and indexes on every install and upgrade. Nothing else is
told the graph exists: the index worker hands each extracted graph to
Retrieval rather than writing it, so the password stays with the one
service that owns the store.

`graph.extraction` is the model that reads entities and relations out of
each passage, and it runs on the index worker. It is optional - without it
the graph stays empty and search answers from vectors alone - but setting it
with the graph off is refused at render time, because every document would
then extract a graph that Retrieval has nowhere to put and fail to index.
Documents indexed before extraction was turned on have no graph until they
are reindexed.

Search tolerates the graph being down: it answers from vectors and says so
in its logs. Indexing and deletion do not, since a graph missing what the
vectors hold would answer from passages that no longer exist; they fail and
are retried.

### The bundled Neo4j

`graph.neo4j.enabled` runs Neo4j Community as a one-replica StatefulSet with
a persistent volume for its store. It runs under the same hardening as
Primer's own pods - non-root, read-only root filesystem, no capabilities -
with its HTTP interface and browser turned off, so its only port is Bolt.
The `allow-neo4j` NetworkPolicy admits Retrieval and the schema hook to that
port and nothing else; the workers extract graphs but never open one.

- **Size the heap and page cache with the limit.** Neo4j sizes its memory
  from the node rather than the container, so `graph.neo4j.heapSize` and
  `pageCacheSize` are set explicitly and must fit inside
  `graph.neo4j.resources.limits.memory` with room for the JVM itself.
- **The schema hook runs after install, and before upgrades.** A
  pre-install hook would run before the StatefulSet exists. With a bundled
  Neo4j the hook is `post-install,pre-upgrade`, as it is for a bundled
  PostgreSQL, and waits up to five minutes for Neo4j to answer; with
  `graph.url` it stays beside the SQL migrations.
- **Turning a bundled Neo4j on for an existing release takes two
  upgrades.** The pre-upgrade hook runs before the new StatefulSet exists,
  so it waits five minutes and fails the upgrade. Create Neo4j first without
  hooks, then upgrade again so the schema is applied:

    ```bash
    helm upgrade primer deploy/helm/primer -f values.yaml --no-hooks
    helm upgrade primer deploy/helm/primer -f values.yaml
    ```

    `--no-hooks` skips the SQL migrations too, so do it with the chart
    version you are already running.
- **The Secret sets the password once.** Neo4j reads it on first start, when
  its store is empty. To change it later, change it in Neo4j first, then
  update the Secret and restart Retrieval:

    ```bash
    kubectl exec -it primer-neo4j-0 -- cypher-shell -u neo4j \
      "ALTER CURRENT USER SET PASSWORD FROM 'old' TO 'new'"
    ```

- **One user and one database, both `neo4j`.** That is what Community has;
  any other `graph.username` or `graph.database` is refused.

An operator-run Neo4j is outside the chart's policies. If yours runs in the
cluster, give it an ingress policy that admits only Primer's Retrieval pods
and the schema hook:

```yaml
- podSelector:
    matchExpressions:
      - key: app.kubernetes.io/component
        operator: In
        values: [retrieval, migrate-graph]
  namespaceSelector:
    matchLabels:
      kubernetes.io/metadata.name: primer
```

## What is not here

No RabbitMQ or model server. Qdrant is run by the release unless you name
your own. PostgreSQL and Neo4j can each be run by it too, and both are off
by default. Primer assumes you already have the
infrastructure you want to keep your documents on, and a database you
already run and back up is a better home for a library than one this chart
started.

## Object storage

`sourceStore.url` points at the bucket, and `sourceStore.existingSecret`
holds the credentials for it:

```bash
kubectl create secret generic primer-object-store \
  --from-literal=AWS_ACCESS_KEY_ID=... \
  --from-literal=AWS_SECRET_ACCESS_KEY=... \
  --from-literal=FSSPEC_S3_ENDPOINT_URL=https://minio.internal   # non-AWS only
```

Every key in that Secret is passed through, because the names belong to the
storage backend rather than to Primer, and enumerating them here would mean
a chart release each time a provider wanted one this chart had not heard of.

It is mounted into Control and the ingestion workers only. Retrieval, Chat
and the web app never open a source object, and giving them the bucket's
credentials would widen what a compromise of any of them reaches.

For anything else this chart does not model, `extraEnv` is added to every
Primer workload.
