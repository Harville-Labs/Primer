# 0004. Keep vectors in Qdrant, and only Qdrant

- Status: Accepted
- Date: 2026-10-30

## Context

Retrieval supported two vector stores, pgvector and Qdrant, and chose
between them with `PRIMER_VECTOR_STORE`. pgvector was the default, because
every deployment already runs PostgreSQL.

Supporting both had costs:

- **Every guarantee was implemented twice.** Library isolation, generation
  visibility, and delete semantics all depend on how each store applies a
  filter. The conformance suite ran each case against both stores, and it
  still found bugs specific to one integration. One example is pgvector
  naming its indexes with fixed defaults, so two Primer tables in one
  schema collided.
- **Errors were implemented twice too.** Each store reports a wrong
  embedding width in its own words. Only pgvector's message was recognised,
  so on Qdrant a model change came back as a retryable outage instead of
  "reindex".
- **The default was the weaker store at scale.** Primer is meant to index
  large libraries, into the terabytes of source documents. pgvector keeps
  vectors in the same database as conversations and job state, where an
  index build and a chat request compete for the same buffers and
  autovacuum. Its HNSW search applies a filter after finding neighbours,
  so a selective filter returns too few results unless iterative scans are
  tuned, and searching one library among many is always a selective
  filter.
- **Deployments needed a PostgreSQL extension.** Retrieval's migrations
  created the `vector` extension. A managed PostgreSQL or a locked-down one
  often does not allow that, so the database every deployment needs came
  with a precondition that only the vector store needed.

## Decision

Retrieval stores vectors in **Qdrant**. pgvector is removed, not deprecated:
the code, its migrations, the `vectors` schema and the setting that chose
between stores are all gone.

- One collection, named by `PRIMER_QDRANT_INDEX`. Libraries and generations
  are payload fields, and every read filters on them, as before.
- The collection is created with **payload indexes** on `library_id`,
  `generation_id` and `document_id`. Without them, every filtered search
  and every delete scans every point's payload.
- Compose and the chart each **run a Qdrant by default**, with telemetry
  off and the port reachable only from Retrieval. `PRIMER_QDRANT_URL`, or
  `vectorStore.url` in the chart, points at a Qdrant you run instead.
- Retrieval no longer receives database credentials, and nothing migrates a
  schema for it. The bundled PostgreSQL becomes the plain `postgres` image,
  because nothing needs the extension any more. This replaces the image
  named in [ADR 0003](0003-bundle-postgresql.md); the rest of that decision
  is unchanged.

## Alternatives considered

- **Keep both, default to Qdrant.** This avoids breaking anyone on pgvector,
  but keeps every cost above. A second backend is worth testing only if
  someone runs it, and a deployment with nothing indexed yet loses nothing
  by starting on Qdrant.
- **Keep pgvector only.** Fewer services, but it keeps the scale and
  extension problems, and those are the reasons to change.
- **Another dedicated store (Milvus, Weaviate).** Both are capable, and both
  are heavier to run than one Qdrant binary. Qdrant's Haystack integration
  is maintained alongside Haystack, and its payload filtering is applied
  inside the HNSW search rather than after it.
- **Bundle Qdrant off by default, as PostgreSQL is.** ADR 0003 kept a
  bundled PostgreSQL off because it holds the library. Qdrant holds only
  what can be rebuilt from the documents, so the risk of production by
  accident is a reindex, not data loss. Off by default would make every
  install name a vector store before it could start.

## Consequences

- **There is no migration from pgvector.** Point Primer at Qdrant and
  reindex each library. The old `vectors` schema and the `vector` extension
  stay in PostgreSQL until an operator drops them:
  `DROP SCHEMA vectors CASCADE;`.
- Payload indexes are created only when a collection is created. A
  collection made before this change has none, so delete it and reindex.
- Every deployment runs one more stateful service. It is derived data, so
  losing its volume costs a reindex rather than a library.
- The conformance suite runs once per case instead of twice. Each case still
  puts the same words in two libraries, so a filter that stopped working
  would fail it.
