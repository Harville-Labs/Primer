# Ingestion

Turning an uploaded file into searchable passages takes minutes, can fail
halfway, and must never run twice. The design is shaped almost entirely by
those three facts.

<div class="primer-diagram" markdown>
![Ingestion pipeline states](../assets/diagrams/ingestion-pipeline.svg#only-light)
![Ingestion pipeline states](../assets/diagrams/ingestion-pipeline-dark.svg#only-dark)
</div>

## Messages carry a job id and nothing else

Everything a worker needs arrives when it claims the job. A message that sat
in a queue while the document was replaced therefore cannot act on a stale
copy of what it was published with.

## Stages, entry states, and leases

Each stage names three states: the one it must be claimed from, the one its
holder sets while working, and the one completion advances to — which is the
next stage's entry state. A redelivered message finds the job past its
stage's entry state and is told the stage is already done.

A claim is held by a **lease**, not by the state alone. State alone cannot
exclude a second worker: one that crashed mid-stage leaves the job marked
active, and a rule that refused to re-enter an active stage would strand it
forever. The lease expires instead, and the stage becomes claimable again
with no operator involved.

Every transition is a single conditional update. Read-then-write would let
two workers both observe a claimable job and both proceed.

## Publishing happens after the commit

An upload is committed before its message is published. A message sent
first could outlive a rolled-back upload, leaving a worker to claim a job
that does not exist.

The opposite failure is possible and is the one worth having: a crash
between committing and publishing leaves a job queued with no message. That
is visible, re-enqueueable, and harms nothing meanwhile.

## Retries

A worker reports whether a failure was transient, because it is the only
party that knows. Transient failures release the lease and return the job to
its stage's entry state; a document Primer cannot read fails terminally
without consuming the retry budget.

Control holds the hard attempt bound rather than trusting a worker's own
counter, which a broker redelivery resets. Backoff is jittered, so a broker
outage does not return as a synchronised herd of retries.

Messages the broker gives up on go to a dead-letter queue, where an operator
can find them. Dropping them would leave a document stuck with nothing to
explain why.

## Parsing

Conversion and chunking are [Docling](https://docling-project.github.io/docling/).
Primer adds no parser and no splitter of its own.

Conversion is the first code to touch bytes a stranger uploaded, so it runs
against a private, read-only copy in a directory that is removed on every
path out.

Its time budget is checked between phases rather than by interrupting the
converter: that work happens inside native extensions, where killing a
thread risks a corrupt process. The hard stop is the worker's task time
limit, which kills the process and lets the job's lease expire.

### Chunking is sized against the embedding model

A chunk is both what gets embedded and what gets quoted back in a citation,
so its size is a retrieval decision and a legibility one at the same time.
Docling's `HybridChunker` bounds chunks by tokens and merges the small
pieces that a document's structure leaves behind, and to do either it needs
the tokenizer of the model that will embed them. The parse worker is told
that model as `PRIMER_EMBEDDING_MODEL` and looks up its tokenizer in
`primer_contracts.embedding_models`, the same table Retrieval reads its
width and prompts from. `PRIMER_CHUNK_TOKENIZER` overrides it for a hosted
model that publishes no tokenizer.

Without a tokenizer, chunking falls back to document structure alone. That
is not a milder version of the same thing. Structure alone emits one chunk
per text item and merges none of them, so a document whose layout is a long
list of short items - a form, a statement, most scanned PDFs - is stored one
line at a time. Those fragments embed to nothing meaningful, they retrieve
badly, and the citation under an answer quotes a couple of words. Measured
on a W2-shaped document: 20 chunks averaging 13 characters, against a single
274-character passage once peers are merged.

The fallback exists for the cases that genuinely have no tokenizer to fetch
- a test that must not reach the network, a hosted embedding model that
publishes none - and the worker warns on startup whenever it is in that
state, because the symptom points nowhere near the cause.

A tokenizer that is configured but cannot be loaded fails the worker at
startup rather than falling back, since falling back would answer a
misconfiguration by silently doing the thing the setting exists to prevent.

Chunk size is changed by reindexing, never in place: chunks are decided at
parse time, so existing documents keep the passages they were split into
until they are parsed again.

## Deletion, and shared bytes

Deleting tombstones the document first. That is the deletion the user asked
for and it takes effect at once; everything after is cleanup, scheduled
separately so a slow or failing cleanup never leaves a deleted document
answering questions.

Cleanup then removes passages, then metadata, then the stored bytes — bytes
last, and only for content nothing references any more.

Which sources are free is decided by PostgreSQL rather than by counting. The
foreign key from versions to sources is `RESTRICT`, so deleting bytes that
another library still points at simply fails, and that failure is the
answer. It cannot race with an upload either, because the version insert
holds a lock the delete waits on. A count would have to guess about
transactions it cannot see.

The remaining gap is a worker that dies between the database freeing an
object and the bytes being removed. That leaks storage rather than losing
data, and is recoverable by a sweep that does not exist yet.

## Chunks carry their scope

Every passage carries its library, document, version, owner, and generation.
Retrieval authorizes by filtering on those fields, and a filter cannot
consult a database.

Chunk ids are derived from the version, the generation, and the position, so
re-running a stage rewrites the same rows rather than doubling the index.

Each passage keeps two texts: the document's own words, which is what a
citation quotes, and a version with its section heading prepended, which is
what gets embedded. A heading helps a passage keep the subject it is about;
putting it in the citation would quote words the document does not contain.

## The knowledge graph

When an extraction model is configured, the embed stage also asks it, for
each passage, which entities the passage names and which relations it
states. The answers go to Retrieval batch by batch, into the same pending
generation as the vectors, so the two are activated together.

Extraction runs in the worker rather than inside Retrieval's index call. It
is a model call per passage: slow, and failing in ways worth retrying. A
worker has a retry budget; a synchronous request that timed out would throw
the work away.

The model's reply is untrusted. The passage is sent as data after the
instructions, never as them. The reply is fitted to the graph contract
before it leaves the worker: an entity name too long to be a name is
dropped, descriptions are shortened, and a relation whose ends the passage
did not name is discarded. One odd reply costs an edge, not the document.

The index stage holds the graph to the same standard as the vectors: it
refuses to activate a generation whose graph holds fewer passages than were
parsed. A passage that names nothing still counts, so a document of
passages with no entities in them is complete rather than empty.
