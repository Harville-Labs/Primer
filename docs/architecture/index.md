# Architecture

Primer is five processes, each owning exactly one thing, and a sixth that
an operator can add.

<div class="primer-diagram" markdown>
![Primer system architecture](../assets/diagrams/architecture.svg#only-light)
![Primer system architecture](../assets/diagrams/architecture-dark.svg#only-dark)
</div>

| Process | Owns |
| --- | --- |
| Control API | What exists, and who may see it |
| Chat | Conversations, and the answers in them |
| Ingestion workers | Turning files into passages, and passages into graphs |
| Retrieval | The vector store and the knowledge graph — exclusively |
| Web app | What the user sees |
| Lab (optional) | A research model it loads itself, and the edits made to it |

## The rules that shape it

**One writer per fact.** Control is the only process that writes job state.
Workers ask Control to move a job forward and are told whether it applied.
Two workers handed the same message therefore cannot both believe they are
the one doing the work.

**Workers hold no database credentials.** Every transition is a request to
Control, made with a token only the workers hold. A compromised worker
cannot read a library it was not handed, and cannot claim to be a user.

**Each service reaches only its own schema.** Control, Chat and Lab share a
PostgreSQL but, with the bundled one, each connects as a role granted the
rows of its own schema and nothing else. See
[a role per service](../operations/migrations.md#a-role-per-service).

**Retrieval is the only index client.** Nothing else names a collection, a
filter syntax, or a Cypher query, and only Retrieval holds Qdrant's address.
A [conformance suite](retrieval.md#the-conformance-suite) pins down what the
store must guarantee, and
[ADR 0004](../adr/0004-qdrant-only.md) says why there is one store rather
than a choice of them. The workers extract a graph but hand it to
Retrieval rather than opening Neo4j themselves.

**The graph is part of every deployment, and search does not depend on
it.** Retrieval refuses to start without `PRIMER_GRAPH_URL`, as it does
without a vector store. Search fuses two rankings, and a graph that goes
down degrades search to vectors alone rather than failing it. A question
that names nothing in the graph is answered from the vectors exactly as it
would be without one. See
[the knowledge graph](retrieval.md#the-knowledge-graph),
[ADR 0002](../adr/0002-knowledge-graph-in-neo4j.md) and
[ADR 0014](../adr/0014-knowledge-graph-is-always-on.md).

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
