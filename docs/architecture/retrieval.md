# Retrieval

Retrieval is the only process that touches a vector store. Nothing else
names a collection, a table, or a filter syntax.

## The scope filter

Every read and every delete carries a filter built in one place, from the
request's library and generations. It is never assembled at a call site.

Isolation that depends on each caller remembering to add a condition holds
right up until the first caller forgets.

Scope is required and has no default. A request that could omit its library
is refused before any store is reached, because on the day someone forgot,
an empty filter would return another user's documents.

## The principal is audit context

Requests carry the acting principal, but Retrieval does not use it to decide
access. Whether that caller may read that library was decided by Control
before the request was made; re-deciding it here, without Control's data,
would be guessing.

## Generations

A **generation** is one build of one document version's index.

<div class="primer-diagram" markdown>
![Index generations](../assets/diagrams/generations.svg#only-light)
![Index generations](../assets/diagrams/generations-dark.svg#only-dark)
</div>

Searches read only the generation Control marks active, so a rebuild in
progress is invisible rather than partially answering questions. Activation
is a single row update, so every answer changes at the same instant.

A build that is short of the passages it should hold never activates.
Activating it would drop the missing passages from every future answer, with
nothing to show that anything was lost — so the index stage's whole job is
to count, compare, and refuse.

## Cleanup

Retiring a superseded build and erasing a deleted document are the same
operation with one parameter different, so they share a code path rather
than two that must stay in agreement about what "gone" means.

Cleanup addresses a *version* rather than a generation, because the builds a
version has been through are not recorded anywhere and the point of cleanup
is to leave nothing behind.

Superseded generations are retired **after** activation, never before. Until
Control has switched, the older generation is what searches are reading.

## The knowledge graph

When `PRIMER_GRAPH_URL` is set, Retrieval also keeps a knowledge graph in
Neo4j: the entities each passage mentions, and the relations between them.
See [ADR 0002](../adr/0002-knowledge-graph-in-neo4j.md).

The graph is scoped differently from the vectors, on purpose. A chunk
belongs to one generation, as it does in the vector store. An entity
belongs to the library, so one name mentioned in two documents is one node
and a traversal can cross from one document to the other. That crossing is
what a graph finds and a vector search does not.

Everything that records where an entity was seen carries its generation:
the chunk that mentions it, and every relation. A traversal follows only
mentions and relations in the generations it was given, so a pending build
is invisible here as well. That includes its relations: a path through a
pending relation cannot lead to an active passage.

Deleting a generation or purging a version erases its graph in the same
request, along with any entity no remaining passage mentions. An
unreachable Neo4j fails that request with a 503 that names the graph store,
so cleanup is retried rather than reported done.

## No Primer vector adapter

Haystack's `DocumentStore` protocol is already that abstraction. Wrapping it
would add a layer whose only purpose would be to become a second place for
isolation bugs to live.

## The conformance suite

pgvector and Qdrant are both supported, so both run the same tests: write,
filtered search, cross-library and cross-owner isolation, duplicate
indexing, pending-generation invisibility, generation swap, delete, repeated
delete, dimension mismatch, and citation round-trip.

The isolation cases put the *same words* in two libraries, so nothing but
the filter separates them. A backend that ignored it fails there rather than
in production.

That suite has already earned its place: it caught the pgvector integration
naming its indexes with fixed defaults, which makes two Primer tables in one
schema collide on creation.
