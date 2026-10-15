# Migrations

Primer never runs migrations at startup. Applying them is an explicit step,
so a rolling deployment cannot have several instances racing to alter the
same schema.

## Applying them

There are three, one per schema, and each runs from its own service
directory:

```bash
for service in control-api chat retrieval; do
  (cd "services/$service" \
    && PRIMER_DATABASE_URL=postgresql://user:pass@host/primer uv run alembic upgrade head)
done
```

All three must run. Retrieval's is easy to overlook because its schema holds
no Primer-defined tables — the vector integration creates its own table — but
nothing else creates the schema to put that table in, or the `vector`
extension its embedding column needs. Skip it and the retrieval service never
becomes ready, which shows up as an unhealthy container rather than as a
missing migration.

The Compose profile and the Helm chart both run all three for you: Compose as
one-shot `migrate-*` services, the chart as pre-install and pre-upgrade hooks.

### The knowledge graph

A deployment with `PRIMER_GRAPH_URL` set has a fourth step. It is not an
Alembic migration - the graph's schema is a handful of idempotent
constraints and indexes with no history to record - but it runs where the
migrations do, and for the same reason:

```bash
(cd services/retrieval \
  && PRIMER_GRAPH_URL=bolt://host:7687 PRIMER_GRAPH_PASSWORD=... \
     uv run python -m primer_retrieval.graph_schema)
```

Safe to run on every release. Run with the graph off, it exits non-zero
rather than succeeding at nothing. Compose runs it as `migrate-graph` under
the `graph` profile; the chart runs it as a hook when `graph.url` is set.

The URL comes from the environment, never from a file in the repository, so
no deployment's credentials live in version control. It may name an async
driver — the same string the services use — and migrations will rewrite the
scheme onto a synchronous one.

## Privileges

`CREATE EXTENSION` needs rights an application role should not hold, so the
retrieval migration is where it happens. The running services never issue
DDL, which means the role they connect as does not need to be able to.

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

Control's tables live in a dedicated `control` schema, Chat's in `chat`, and
Retrieval's vectors in `vectors`. No service's migrations touch another's
tables, so they can share one PostgreSQL instance without coordinating
releases.

## Drift

CI asserts that the migrations and the models describe the same schema. A
model changed without a migration fails the build rather than surfacing in
production as a query against a column that was never created.

The vector schema is deliberately outside this check. Its table belongs to
the vector integration, so there are no Primer models to compare against, and
a hand-written copy of that table here would drift silently the first time
the integration changed a column.
