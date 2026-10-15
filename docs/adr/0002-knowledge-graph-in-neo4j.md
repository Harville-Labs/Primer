# 0002. Keep a knowledge graph in Neo4j, owned by Retrieval

- Status: Accepted
- Date: 2026-10-14

## Context

Primer retrieves passages that are close to a question in embedding space.
That works when the answer is in a passage that resembles the question. It
fails when the answer depends on how things connect across passages. Say a
paper names a method, and a second paper says who extended it. The question
"who extended this method?" finds the first passage and nothing that links
it to the second.

Graph-augmented retrieval addresses this. A model extracts the entities each
passage mentions and the relations it states. At query time, the entities a
question names are expanded to their neighbours, and the passages that
mention those neighbours are ranked alongside the vector results.

Two properties of Primer's existing index constrain the design:

- Every read is scoped to one library and its active generations. A pending
  rebuild is invisible, and isolation comes from one filter applied in the
  store, never from callers remembering a condition.
- Deleting a document erases everything stored for it, including builds
  nobody remembers.

## Decision

Retrieval keeps a knowledge graph in **Neo4j**, beside the vector store and
owned by the same service. Nothing else connects to it.

- **Entities are scoped to a library**, so that one name mentioned in two
  documents is one node and traversal can cross between documents. Entity
  identity is the name with case and whitespace folded, and nothing more.
- **Mentions and relations carry their generation and version**, as chunks
  do. Every relation on a traversed path has to belong to a named
  generation, not only the chunk at the end of it. Otherwise a pending
  build could steer which active passages come back.
- **Erasure follows the vector store's semantics**: one generation, or a
  version except the generation in use. Entities left with no mention are
  removed in the same transaction.
- The graph is **off unless `PRIMER_GRAPH_URL` is set**. A deployment
  without it never opens a connection.
- Constraints and indexes are applied by the migration entrypoint, never at
  service startup.
- Graph content is model output. It is validated as a wire contract with
  tight bounds before it reaches the store, and it reaches Cypher only as
  parameters, never as query text.

The work is tracked in #127 (store), #128 (extraction), #129 (hybrid
search), #130 (deployment) and #131 (global search, deferred).

## Alternatives considered

- **Tables in PostgreSQL, traversed with recursive CTEs.** No new
  infrastructure, and Postgres is already present in every deployment.
  Rejected in favour of a database built for traversal: multi-hop
  neighbourhoods with per-edge predicates are what Cypher expresses
  directly. Recursive SQL needs hand-written cycle handling to do the same,
  and every query then becomes Primer's own graph engine.
- **The `neo4j-haystack` document-store integration.** It stores Haystack
  documents with vectors in Neo4j. It does not model entities and
  relations, and it would replace the vector store rather than add a graph
  beside it. Primer uses the official `neo4j` driver directly.
- **Entities scoped per document or per generation.** Isolation would be
  simpler, since a generation's graph could be deleted wholesale. But no
  traversal could leave the document it started in, which is the main
  reason to have a graph.
- **Global, community-summary search (Microsoft GraphRAG).** Deferred to
  #131. It re-summarises whenever a library changes, and Primer's citation
  model needs an answer for a summary that no single passage supports.

## Consequences

- A deployment that enables the graph runs and backs up a second stateful
  service. Neo4j Community is enough: the schema uses only property
  uniqueness constraints and range indexes.
- Isolation now has two implementations to keep correct, the vector filter
  and the graph's Cypher. The graph's tests use identical names in two
  libraries, so that scoping is the only thing that separates them.
- A document's graph is erased in the same request as its vectors. If
  Neo4j is unreachable, cleanup fails with a 503 and is retried rather than
  reporting the document gone.
- Entity resolution stays deliberately shallow. "Curie" and "Marie Curie"
  are different nodes until something better than string folding decides
  otherwise.
