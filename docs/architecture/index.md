# Architecture

Primer is four processes, each owning exactly one thing.

<div class="primer-diagram" markdown>
![Primer system architecture](../assets/diagrams/architecture.svg#only-light)
![Primer system architecture](../assets/diagrams/architecture-dark.svg#only-dark)
</div>

| Process | Owns |
| --- | --- |
| Control API | What exists, and who may see it |
| Ingestion workers | Turning files into passages, and passages into graphs |
| Retrieval | The vector store and the knowledge graph — exclusively |
| Web app | What the user sees |

## The rules that shape it

**One writer per fact.** Control is the only process that writes job state.
Workers ask Control to move a job forward and are told whether it applied.
Two workers handed the same message therefore cannot both believe they are
the one doing the work.

**Workers hold no database credentials.** Every transition is a request to
Control. A compromised worker cannot read a library it was not handed.

**Retrieval is the only index client.** Nothing else names a collection, a
table, a filter syntax, or a Cypher query. Swapping pgvector for Qdrant is a
configuration change, and a
[conformance suite](retrieval.md#the-conformance-suite) proves both behave
identically where it matters. The workers extract a graph but hand it to
Retrieval rather than opening Neo4j themselves.

**The graph is optional and additive.** Without `PRIMER_GRAPH_URL`, Primer
never opens a connection to Neo4j and search returns exactly what it did
before the graph existed. With it, search fuses two rankings, and a graph
that goes down degrades search to vectors alone rather than failing it. See
[the knowledge graph](retrieval.md#the-knowledge-graph) and
[ADR 0002](../adr/0002-knowledge-graph-in-neo4j.md).

**Authorization is a SQL predicate, not a check.** Listing many libraries
applies the same rule as fetching one, because it is literally the same
expression. There is no second code path to drift out of agreement.

**Absence and denial look alike.** Everything a user may not see returns
404. See [Identity and access](identity.md).

## What is deliberately boring

Primer writes no parser, no chunking algorithm, no embedding model, no
vector-store abstraction, and no graph engine. Each is a solved problem with good libraries, and
a bespoke version would be a second thing to maintain that worked worse.

What Primer does own is the part nobody else can: which user may see which
passage, and making sure a half-finished rebuild never answers a question —
through the graph as much as through the vectors.
