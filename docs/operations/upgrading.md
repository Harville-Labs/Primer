# Upgrading

Most upgrades are a new image tag and [the migrations](migrations.md). The
exceptions are listed here, newest first: changes where something already
running has to be dealt with before the new version will start.

## Every deployment has a knowledge graph

**Affects:** a chart install with `graph.neo4j.enabled: false` and no
`graph.url`, and every Compose stack.

There is no graph-off mode any more
([ADR 0014](../adr/0014-knowledge-graph-is-always-on.md)). The chart refuses
a release that turns the bundled Neo4j off without naming another; turn it
back on, or set `graph.url`.

Compose now runs Neo4j and its schema step without a profile, and refuses
to start until `.env` sets `PRIMER_GRAPH_PASSWORD`. Remove `graph` from
`COMPOSE_PROFILES`. A stack that already ran the graph profile can delete
`PRIMER_GRAPH_URL=bolt://neo4j:7687`, which is now the default; keep it
set only for a Neo4j you run yourself. The index worker extracts with the
chat model when no extraction model is set.

Libraries indexed without a graph need a reindex to get one.

## The chart runs a Neo4j by default

**Affects:** a chart install that leaves `graph.neo4j.enabled` and
`graph.url` unset, and one that sets `graph.url`.

An upgrade now starts a one-replica Neo4j with a 10Gi volume, makes its
password, and applies the graph schema once Neo4j answers. With
`inference.chat.model` set, the index worker extracts a graph with the chat
model as it indexes. Nothing already indexed has a graph until its library
is reindexed.

An install that sets `graph.url` is refused until it turns the bundled one
off, since one of the two would be ignored:

```yaml
graph:
  neo4j:
    enabled: false
```

Running without a graph was possible until the change above.

## The embedder and reranker are bundled

**Affects:** a chart install that sets `inference.embeddings.baseUrl` or
`inference.rerank.baseUrl`, and a Compose stack that leaves either unset.

The chart now runs its own embedding and reranking servers, and refuses an
install that names a URL as well, since one of the two would be silently
ignored. To keep your own servers, turn the bundled ones off:

```yaml
inference:
  embeddings:
    bundled: { enabled: false }
  rerank:
    bundled: { enabled: false }
```

An install that named no rerank URL had no reranker and now gets one. To
keep searching without, set `inference.rerank.bundled.enabled=false`.

Compose keeps the embedding URL and model an existing `.env` sets, and
starts no embedder of its own while one is set. It had no reranker setting
before, so an existing stack now starts the bundled reranker; set
`PRIMER_RERANK_BASE_URL=` (empty) to keep searching without one. Before
removing your embedding settings to use the bundled embedder, note that a
library indexed with a different model has to be reindexed.

## Ingestion queues became quorum queues

**Affects:** any deployment whose broker already has the `ingestion.*`
queues, which is every deployment that has run a worker.

RabbitMQ will not redeclare an existing queue with a different type. A worker
started against the old queues fails with `PRECONDITION_FAILED` and cannot
consume anything, so the queues have to be removed first.

The change is worth the interruption twice over. Quorum queues replicate, so
queued ingestion work survives a broker restart properly rather than by
luck. They also settle a deprecation: RabbitMQ 4.1 deprecated channel-wide
prefetch, and Celery only stops asking for it once it sees a queue declared
this way — until then every worker start writes errors into the broker log.

### Compose

Nothing durable lives in these queues between runs on a single-machine
profile, so removing the broker's data is the simplest route:

```bash
docker compose down
docker volume rm primer_rabbitmq-data
docker compose up --wait
```

### Kubernetes, or any broker with work in flight

Let the queues drain first, then delete them. A document whose job is deleted
mid-flight is not lost — Control still holds it in a non-terminal state — but
it will need reindexing, so draining is the kinder order.

```bash
# Stop consuming and publishing.
kubectl scale deployment -l 'app.kubernetes.io/component in (worker-parse,worker-index)' --replicas=0

# Confirm nothing is waiting, then remove them.
rabbitmqctl list_queues name messages | grep ingestion
for queue in ingestion.parse ingestion.embed ingestion.index ingestion.delete ingestion.dead; do
  rabbitmqctl delete_queue "$queue"
done

kubectl scale deployment -l 'app.kubernetes.io/component in (worker-parse,worker-index)' --replicas=1
```

The workers redeclare them on the next start. Any document left mid-flight
shows as failed and can be reindexed from its library.
