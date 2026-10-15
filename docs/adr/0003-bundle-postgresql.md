# 0003. Offer a bundled PostgreSQL in the chart

- Status: Accepted
- Date: 2026-10-15

## Context

The Helm chart ran nothing stateful of its own. Its documentation gave the
reason: a chart that shipped its own database is one people run in
production by accident, and Primer assumes you already have the
infrastructure you want to keep your documents on.

The knowledge graph changed that for Neo4j. `graph.neo4j.enabled` runs one
in the release, and `graph.url` points at one you run yourself. The
exception was justified because the graph is derived: losing it costs a
reindex, not a library.

That left the chart inconsistent. The optional, rebuildable store could be
bundled, but PostgreSQL, which every deployment needs, could not. Someone
trying Primer on a fresh cluster had to stand up a PostgreSQL with pgvector
first, while Compose has always run one for them.

## Decision

The chart can run PostgreSQL the same way it runs Neo4j.
`postgresql.bundled.enabled` runs `pgvector/pgvector`, the image Compose
uses, as a one-replica StatefulSet with a persistent volume. It runs under
the chart's usual pod hardening, behind an `allow-postgresql` NetworkPolicy
that admits only the workloads given the database. It is off by default.
Naming a Secret that holds a connection URI is still the default path.

`postgresql.existingSecret` stays required either way. It holds the URI for
an external server, and the password for a bundled one. Both the server and
every client read the password from that Secret, so they cannot disagree.
Clients get it as `PGPASSWORD` instead of inside the URI. asyncpg and
psycopg both read that variable, so the password can contain characters a
URI would need escaped.

With a bundled server, the SQL migrations run `post-install,pre-upgrade`.
The server does not exist before the first install. After that it does, so
on upgrades the schema still changes before new code runs against it.

## Alternatives considered

- **Keep requiring an external PostgreSQL.** This is consistent with the old
  rule, but inconsistent with Neo4j, and it makes the first install the
  hardest step.
- **A PostgreSQL subchart (Bitnami or CloudNativePG).** That brings
  replication and backups, plus a dependency the chart would then have to
  track and harden. It is also the wrong place for a production database.
  An operator who wants one already runs an operator.
- **Bundled by default.** That would recreate exactly the accident the old
  rule warned about.

## Consequences

- The production-by-accident risk is real, so the chart says so where an
  operator will see it. The values file and the install notes say the
  bundled server holds the library itself and needs its volume backed up.
- One replica, with no failover or point-in-time recovery. Deployments that
  need those use `existingSecret` with a server that provides them.
- Switching between bundled and external is a data migration, not a values
  change: nothing copies the data across.
- The Secret sets the password once. PostgreSQL reads it when it first
  initialises its volume, as it does in Compose.
