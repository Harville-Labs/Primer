# 0014. Make the knowledge graph part of every deployment

- Status: Accepted
- Date: 2026-11-02
- Supersedes [0011](0011-bundle-neo4j-by-default.md), and the "off unless
  `PRIMER_GRAPH_URL` is set" part of [0002](0002-knowledge-graph-in-neo4j.md)

## Context

[ADR 0002](0002-knowledge-graph-in-neo4j.md) added the knowledge graph as
something a deployment could have. [ADR 0011](0011-bundle-neo4j-by-default.md)
turned it on by default in the chart, and kept `graph.neo4j.enabled: false`
as a way back. Compose kept it behind a profile, with three variables to set
before it did anything.

That left the graph as two products. Retrieval carried a vector-only mode
and a hybrid one, and so did the tests. A library-wide question
([ADR 0006](0006-library-wide-questions-from-passages.md)) fell back to a
focused search without the graph, and nothing told the person asking that it
had. A user could not tell whether a deployment had a graph at all. The mode
nobody chose was the one most local installs ran.

## Decision

The knowledge graph is part of Primer, as the vector store is. Every
deployment has one, and there is no setting that removes it.

- **Retrieval refuses to start without a graph.** `PRIMER_GRAPH_URL` and
  `PRIMER_GRAPH_PASSWORD` are required, like `PRIMER_QDRANT_URL`. A
  deployment that lost its graph setting fails at startup with a message
  naming the variable, not with search that quietly stopped following
  relations.
- **The chart chooses where the graph is, not whether there is one.**
  `graph.neo4j.enabled` still means "this release runs Neo4j". Turning it off
  without setting `graph.url` is refused at render time. Everything else in
  ADR 0011 stands: the generated password, the chat model as the extraction
  fallback, and the post-install schema hook.
- **Compose runs Neo4j and its schema step with no profile.**
  `PRIMER_GRAPH_PASSWORD` is required beside `POSTGRES_PASSWORD`, and
  `PRIMER_GRAPH_URL` defaults to the bundled server. Setting it to a Neo4j
  you run leaves the bundled one stopped, as `PRIMER_QDRANT_URL` does for
  Qdrant. Extraction falls back to `PRIMER_CHAT_BASE_URL` and
  `PRIMER_CHAT_MODEL`, as it does in the chart.
- **Extraction stays optional.** A graph needs a model to read it out of
  passages, and Compose does not require a chat model: a provider can be
  added on the settings page instead, and the index worker cannot use one
  from there. With no model named, the graph exists and stays empty. That is
  a state the library page shows, not a mode Retrieval has.
- **Users see the graph's part in an answer.** Search says, for each passage,
  whether vector search found it, the graph did, or both. A citation the
  graph contributed is marked as such in Chat. The library page shows how
  much of the library the graph covers, and which documents need a reindex
  to be in it.

## Alternatives considered

- **Keep the opt-outs as an escape hatch.** Cheap to keep, but it keeps the
  vector-only mode alive in Retrieval, the tests and the UI, and the UI has
  to explain "no graph" as a normal state. The cost of running Neo4j is one
  StatefulSet or container. An operator who does not want extraction cost
  can leave the extraction model unset.
- **Have Compose generate the graph password.** Compose has no way to
  generate a value and hand it to two containers without a one-off step
  writing it to a shared volume, and every service reading it from a file
  instead of the environment. The other bundled credentials are already set
  in `.env`, so one more line there is the smaller change.
- **Run Neo4j in Compose without authentication.** The Compose stack is
  single-user and unauthenticated already. But Neo4j's credentials are what
  stop a container that should not reach it from reading every library's
  graph, and PostgreSQL is given a password for the same reason.
- **Require an extraction model too.** That would make a model mandatory for
  the first `docker compose up`, which today needs none. An empty graph that
  says so is more useful than a stack that does not start.

## Consequences

- A chart release with `graph.neo4j.enabled: false` and no `graph.url` fails
  to upgrade until one of them is set. The failure says which.
- A Compose stack with no `PRIMER_GRAPH_PASSWORD` in `.env` refuses to start
  until it is set. Neo4j reads it only when it first creates its volume, as
  PostgreSQL does.
- Libraries indexed while the graph was off have no graph until they are
  reindexed. The library page lists them.
- Retrieval's vector-only branches, and the 503 an index request got when
  there was no graph store, are removed.
