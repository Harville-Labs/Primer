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
  --set inference.embeddings.model=bge-large-en-v1.5 \
  --set inference.embeddings.dimensions=1024
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
| `postgresql.existingSecret` | `database-url` |
| `rabbitmq.existingSecret` | `broker-url` |
| `auth.oidc.existingSecret` | `client-secret`, `cookie-secret` |
| `inference.*.existingSecret` | API keys, if your endpoint needs them |

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

A failed migration fails the release before any new pod starts.

## Scale

**Parsing and indexing are separate deployments.** Parsing is CPU-heavy and
holds whole documents in memory; embedding and indexing mostly wait on a
network call. Splitting them by queue lets each scale on what constrains it,
and stops a burst of uploads starving the stage that finishes them.

**Each worker states its own concurrency.** `workers.parse.concurrency` and
`workers.index.concurrency` are the number of processes in a pod, and they
are passed to Celery explicitly. Left out, Celery sizes its pool from the
node's CPU count — so a worker scheduled onto a large machine forks a child
per CPU against a memory limit set for a couple of them, and is killed on
startup for a reason that has nothing to do with the work it was given.

Parsing runs one at a time: it holds a whole document in memory and loads
Docling's layout models, and two of those in one container is two copies of
both. Scale that component with `replicas`. Indexing runs four, because
embedding is a network call and a process waiting on a socket is not using
the memory the limit is protecting. Raise either only alongside the
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

!!! note "The workers are not here, and that is deliberate"
    Their work arrives on a queue, and CPU does not describe a queue. An
    embedding worker spends its time waiting on a network call, so its CPU
    is near zero precisely when the backlog is longest: a CPU autoscaler
    would add pods while they compute and none while they fall behind.

    Queue depth is the signal that describes them, and reading it needs an
    adapter this chart does not ship — KEDA against RabbitMQ is the usual
    answer. Until then `workers.parse.replicas` and `workers.index.replicas`
    are set by hand.

## What is not here

No PostgreSQL, RabbitMQ, or model server. A chart that shipped its own
database is one people run in production by accident, and Primer's whole
premise is that you already have the infrastructure you want to keep your
documents on.

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
