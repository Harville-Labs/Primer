# 0011. Run a Neo4j in every chart release by default

- Status: Superseded by [0014](0014-knowledge-graph-is-always-on.md)
- Date: 2026-11-02

## Context

The chart runs Qdrant, an embedder and a reranker unless you name your own,
because search does not work without them. The knowledge graph
([ADR 0002](0002-knowledge-graph-in-neo4j.md)) was the exception: a release
had no graph until an operator chose a Neo4j, named a Secret for its
password, and named a model to extract with. Few did, so most installs
answered library-wide questions without the communities those questions are
answered from ([ADR 0006](0006-library-wide-questions-from-passages.md)).

[ADR 0003](0003-bundle-postgresql.md) kept the bundled PostgreSQL off by
default because a database a chart started is one people run in production
by accident. That argument is about the library itself. The graph is
derived from passages Primer already keeps: losing it costs a reindex, not a
library, which is the same reason Qdrant is bundled.

## Decision

`graph.neo4j.enabled` defaults to `true`.

- **The release makes the password when nobody names one.** It is generated
  into the release's internal Secret and carried forward across upgrades,
  like the service token. `graph.existingSecret` still wins when it is set,
  and is still required with `graph.url`, since the chart cannot know the
  password of a server it did not start.
- **Extraction falls back to the chat model.** With `graph.extraction`
  empty, the index worker extracts with `inference.chat.baseUrl` and
  `inference.chat.model`, and the chat key. With neither an extraction
  model nor a named chat model, the graph is empty and the install notes
  say so. A named model is required: extraction has no "first served model"
  to fall back on the way chat does.
- **The bundled graph's schema hook runs post-install and post-upgrade.**
  A pre-upgrade hook waits for a StatefulSet the same upgrade is about to
  create. That is the case for every existing release taking this default,
  so the hook would time out and fail the upgrade. A lookup cannot tell
  whether the StatefulSet exists under `helm template` or a dry run, so the
  hook cannot choose its phase from the cluster. The schema is constraints
  and indexes, and Retrieval does not depend on them being in place to
  start; a release that later adds a uniqueness constraint has to account
  for the code running briefly before it.

## Alternatives considered

- **Keep it off and document it better.** The setting was documented; the
  graph still went unused, and the feature it powers is invisible until it
  is on.
- **Bundle it, but keep the pre-upgrade hook.** Correct for new installs and
  broken for every existing one, which is most of the people who would get
  the new default.
- **Require `graph.existingSecret` for the bundled server too.** One more
  Secret before a first install works, for a password nothing outside the
  release ever needs to know.

## Consequences

- Every release runs one more StatefulSet with a volume, and an existing
  release gains one on upgrade. `graph.neo4j.enabled: false` keeps the old
  behaviour.
- Indexing calls the chat model once per passage for extraction. On a small
  shared endpoint that competes with conversations; name a separate
  `graph.extraction` model, or turn the graph off, where that matters.
- Libraries indexed before the graph was on have no graph until they are
  reindexed.
- Neo4j Community is still one replica with one database and one user.
