# 0006. Answer library-wide questions from passages chosen across communities

- Status: Accepted
- Date: 2026-10-31

## Context

Some questions are about a library as a whole: "what are the main themes
across these papers?", "which methods do these studies use?". A search
returns the passages closest to the question. For a question like that,
the closest passages usually come from one corner of the library, so the
answer describes that corner as if it were the whole.

GraphRAG's global search addresses this. It clusters the entity graph into
communities, has a model summarise each community, and answers from the
summaries. Primer already keeps the entity graph ([ADR 0002](0002-knowledge-graph-in-neo4j.md)),
so clustering it is within reach.

The summaries are the problem. Every claim in a Primer answer cites the
passage it came from, and a reader can open that passage and check it. A
community summary has no passage behind it. Citing one is like citing a
model's notes, and the reader can't check those. There are two ways to give
a summary a citation, and neither works:

- **Cite the summary's source passages.** A summary of forty passages would
  cite all forty. A citation that points at forty passages doesn't show
  which one supports the sentence it's attached to.
- **Cite the summary itself, as a new kind of source.** The answer would
  then rest on text a model wrote. If the summary got something wrong, the
  answer repeats it as evidence.

Summaries also have to be rewritten whenever a library changes. That is a
model pass over every affected community, on every upload.

## Decision

Communities choose **which passages** a library-wide question reads. They
are never shown to the model and never cited. The model writes its
overview from passages, and every claim still cites one.

- **The user asks for it.** A question is focused by default. Asking across
  the library is a per-question choice in the interface. Guessing from the
  wording would get it wrong often, and the two kinds of answer differ
  enough that the user should know which one they got.
- **Communities come from the graph, not from a model.** Retrieval reads
  which passages mention which entities in the library's active
  generations, and clusters that graph with Louvain modularity. No model
  runs, so nothing has to be rewritten when the library changes.
- **They are computed when asked for, and cached by generation set.** A
  library's active generations change exactly when its contents do. So a
  cache keyed by them can't be stale, and needs no invalidation step.
- **The passages cover the communities.** From the largest communities,
  Retrieval takes the passages most central to each and scores them
  against the question. It then picks the best passage from each community,
  most relevant community first, before a second passage from any.
- **There's a size limit.** Clustering runs in Retrieval's memory, so a
  library with more mentions than the operator's limit is refused, with a
  message saying so. It doesn't silently fall back to a focused search,
  because the user asked about the whole library.
- **No graph means a focused search.** If the graph is down or not
  configured, a library-wide question gets the focused answer. That is the
  same rule the focused search follows: a graph outage makes an answer
  worse, not lost.

## Alternatives considered

**Summaries, cited by their source passages.** This is GraphRAG's approach,
and it gives the most fluent overviews. It was rejected because the
citations can't be checked, as described above. It also adds a model pass
on every change to a library.

**Summaries used only to pick communities.** A summary is a good thing to
match a question against. But it still has to be written and kept up to
date, and the passages already carry the same information. Scoring a few
central passages per community gets nearly the same ranking without a
model call.

**Diversify the focused search instead.** Maximal marginal relevance or a
similar re-ranking spreads the top results apart. But it can only choose
among passages that already resemble the question, and the corners of the
library the question doesn't resemble are the ones a library-wide answer
needs.

**Compute communities when indexing, in Neo4j.** Neo4j's Graph Data Science
library has Leiden and Louvain, and storing a community on each entity would
make large libraries cheap. It is a plugin that most Neo4j installs don't
have, and stored communities would need recomputing, versioned by
generation, whenever a library changed. This is the way to go when the size
limit becomes the thing people hit.

## Consequences

- A library-wide answer shows breadth, not depth. A community gets one or
  two passages, so the overview names a theme and cites an example of it.
  It doesn't exhaust the theme.
- Passages that mention no entities are in no community, so a library-wide
  question can't reach them.
- Library-wide questions skip the reranker and the relevance floor. Both
  measure how well a passage answers the question, and for a question about
  the library as a whole the point is to read passages that don't match it
  well.
- The first library-wide question after a library changes pays for the
  clustering. Later ones in the same state are served from the cache.
- Each Retrieval replica clusters and caches on its own.
