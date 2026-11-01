# Migrations

Primer never runs migrations at startup. Applying them is an explicit step,
so a rolling deployment cannot have several instances racing to alter the
same schema.

## Applying them

There are two, one per schema, and each runs from its own service
directory:

```bash
for service in control-api chat; do
  (cd "services/$service" \
    && PRIMER_DATABASE_URL=postgresql://user:pass@host/primer uv run alembic upgrade head)
done
```

Retrieval has none. Its vectors are in Qdrant, which creates the collection
the first time something is indexed.

Lab, when it is on, has another, in `services/lab`, for its saved
experiments. It runs from Lab's image, the only one that holds it: Compose's
`migrate-lab` and the chart's `migrate-lab` hook.

The Compose profile and the Helm chart both run them for you: Compose as
one-shot `migrate-*` services, the chart as pre-install and pre-upgrade hooks.

### The knowledge graph

A deployment with `PRIMER_GRAPH_URL` set has a third step. It is not an
Alembic migration - the graph's schema is a handful of idempotent
constraints and indexes with no history to record - but it runs where the
migrations do, and for the same reason:

```bash
(cd services/retrieval \
  && PRIMER_GRAPH_URL=bolt://host:7687 PRIMER_GRAPH_PASSWORD=... \
     uv run python -m primer_retrieval.graph_schema)
```

Safe to run on every release. Run with the graph off, it exits non-zero
rather than succeeding at nothing. It waits up to five minutes for Neo4j to
answer, since a Neo4j started alongside it may still be opening its store,
and fails at once on a wrong password. Compose runs it as `migrate-graph`
under the `graph` profile. The chart runs it as a hook when the graph is on:
beside these migrations for a Neo4j you run. For one the chart runs, it
runs after install, since that Neo4j does not exist before it, and before
upgrades, like a bundled PostgreSQL.

The URL comes from the environment, never from a file in the repository, so
no deployment's credentials live in version control. It may name an async
driver — the same string the services use — and migrations will rewrite the
scheme onto a synchronous one.

## Privileges

The migrations create schemas and tables. The running services never issue
DDL, which means the role they connect as does not need to be able to. No
extension is needed, so the database can be one an administrator hands you
without superuser rights.

## Checking where you are

```bash
PRIMER_DATABASE_URL=... uv run alembic current
PRIMER_DATABASE_URL=... uv run alembic history
```

## Rolling back

Every migration has a tested downgrade, verified by a round trip against a
real PostgreSQL in CI. A migration that cannot be rolled back cannot be
safely deployed.

```bash
PRIMER_DATABASE_URL=... uv run alembic downgrade -1
```

Downgrades do not drop the `control` schema itself, because it holds
Alembic's own version table.

## Schema layout

Control's tables live in a dedicated `control` schema and Chat's in `chat`.
Neither service's migrations touch the other's tables, so they can share one
PostgreSQL instance without coordinating releases. Retrieval has no schema:
its vectors are in Qdrant, which creates its collection on first use.

## Drift

CI asserts that the migrations and the models describe the same schema. A
model changed without a migration fails the build rather than surfacing in
production as a query against a column that was never created.
