"""The contract a vector backend must meet to be usable by Primer.

Every case here runs against each supported backend. A backend that cannot
pass one of them cannot be offered: these are not preferences, they are the
properties the rest of Primer assumes when it decides what a user may read.
"""

from __future__ import annotations

import uuid

import pytest
from conformance_support import HashingDocumentEmbedder, HashingTextEmbedder, embed_text, make_chunk
from haystack.document_stores.types import DocumentStore, DuplicatePolicy
from primer_retrieval.config import Settings
from primer_retrieval.pipelines import (
    build_retriever,
    chunks_filter,
    scope_filter,
    to_documents,
    to_retrieved,
)

FIRST_LIBRARY = uuid.uuid5(uuid.NAMESPACE_URL, "library-one")
SECOND_LIBRARY = uuid.uuid5(uuid.NAMESPACE_URL, "library-two")
GENERATION = uuid.uuid5(uuid.NAMESPACE_URL, "generation-one")
NEXT_GENERATION = uuid.uuid5(uuid.NAMESPACE_URL, "generation-two")


def write(store: DocumentStore, *chunks) -> int:
    return store.write_documents(
        to_documents(tuple(chunks), HashingDocumentEmbedder()),
        policy=DuplicatePolicy.OVERWRITE,
    )


def search(store: DocumentStore, settings: Settings, library, generations, query, limit=10):
    retriever = build_retriever(store, settings)
    hits = retriever.run(
        query_embedding=HashingTextEmbedder().run(query)["embedding"],
        filters=scope_filter(library, generations),
        top_k=limit,
    )
    return [to_retrieved(document) for document in hits["documents"]]


def test_written_chunks_come_back(store: DocumentStore, settings: Settings) -> None:
    written = write(
        store,
        make_chunk(
            library_id=FIRST_LIBRARY,
            generation_id=GENERATION,
            content="transformer attention mechanisms",
        ),
    )
    assert written == 1

    results = search(store, settings, FIRST_LIBRARY, (GENERATION,), "attention mechanisms")

    assert [chunk.content for chunk in results] == ["transformer attention mechanisms"]
    assert results[0].index_generation == GENERATION


def test_search_never_returns_another_library(store: DocumentStore, settings: Settings) -> None:
    """The property everything else rests on.

    Both libraries hold the same words, so nothing but the filter separates
    them. A backend that ignores the filter fails here rather than in
    production.
    """
    write(
        store,
        make_chunk(
            library_id=FIRST_LIBRARY, generation_id=GENERATION, content="shared term in mine"
        ),
        make_chunk(
            library_id=SECOND_LIBRARY, generation_id=GENERATION, content="shared term and a secret"
        ),
    )

    results = search(store, settings, FIRST_LIBRARY, (GENERATION,), "shared term")

    assert results
    assert {chunk.library_id for chunk in results} == {FIRST_LIBRARY}
    assert all("secret" not in chunk.content for chunk in results)


def test_search_never_returns_another_owners_chunks(
    store: DocumentStore, settings: Settings
) -> None:
    """Two users, same words, different libraries: neither sees the other."""
    mine = make_chunk(
        library_id=FIRST_LIBRARY,
        generation_id=GENERATION,
        content="quarterly revenue analysis",
        owner_user_id=uuid.uuid5(uuid.NAMESPACE_URL, "owner-a"),
    )
    theirs = make_chunk(
        library_id=SECOND_LIBRARY,
        generation_id=GENERATION,
        content="quarterly revenue analysis",
        owner_user_id=uuid.uuid5(uuid.NAMESPACE_URL, "owner-b"),
    )
    write(store, mine, theirs)

    results = search(store, settings, SECOND_LIBRARY, (GENERATION,), "quarterly revenue")

    assert [chunk.chunk_id for chunk in results] == [theirs.chunk_id]


def test_reindexing_the_same_generation_does_not_duplicate(
    store: DocumentStore, settings: Settings
) -> None:
    """Chunk ids are derived, so a redelivered stage rewrites its own rows."""
    chunk = make_chunk(
        library_id=FIRST_LIBRARY, generation_id=GENERATION, content="idempotent write"
    )
    write(store, chunk)
    write(store, chunk)

    results = search(store, settings, FIRST_LIBRARY, (GENERATION,), "idempotent write")

    assert len(results) == 1


def test_a_pending_generation_is_invisible_until_it_is_named(
    store: DocumentStore, settings: Settings
) -> None:
    """A rebuild in progress must not answer questions."""
    write(
        store,
        make_chunk(library_id=FIRST_LIBRARY, generation_id=GENERATION, content="the old answer"),
        make_chunk(
            library_id=FIRST_LIBRARY, generation_id=NEXT_GENERATION, content="the new answer"
        ),
    )

    old = search(store, settings, FIRST_LIBRARY, (GENERATION,), "answer")

    assert [chunk.content for chunk in old] == ["the old answer"]


def test_a_generation_swap_changes_every_answer_at_once(
    store: DocumentStore, settings: Settings
) -> None:
    """Activation is a change of which generation is asked, not a data migration."""
    write(
        store,
        make_chunk(library_id=FIRST_LIBRARY, generation_id=GENERATION, content="the old answer"),
        make_chunk(
            library_id=FIRST_LIBRARY, generation_id=NEXT_GENERATION, content="the new answer"
        ),
    )

    new = search(store, settings, FIRST_LIBRARY, (NEXT_GENERATION,), "answer")

    assert [chunk.content for chunk in new] == ["the new answer"]


def test_deleting_a_generation_leaves_the_others_intact(
    store: DocumentStore, settings: Settings
) -> None:
    write(
        store,
        make_chunk(library_id=FIRST_LIBRARY, generation_id=GENERATION, content="retire me"),
        make_chunk(library_id=FIRST_LIBRARY, generation_id=NEXT_GENERATION, content="keep me"),
    )

    retiring = store.filter_documents(filters=scope_filter(FIRST_LIBRARY, (GENERATION,)))
    store.delete_documents([document.id for document in retiring])

    assert search(store, settings, FIRST_LIBRARY, (GENERATION,), "retire") == []
    assert [
        c.content for c in search(store, settings, FIRST_LIBRARY, (NEXT_GENERATION,), "keep")
    ] == ["keep me"]


def test_deleting_twice_is_not_an_error(store: DocumentStore, settings: Settings) -> None:
    """A redelivered cleanup message asks for exactly this."""
    write(
        store,
        make_chunk(library_id=FIRST_LIBRARY, generation_id=GENERATION, content="delete me"),
    )
    documents = store.filter_documents(filters=scope_filter(FIRST_LIBRARY, (GENERATION,)))
    ids = [document.id for document in documents]

    store.delete_documents(ids)
    store.delete_documents(ids)

    assert store.filter_documents(filters=scope_filter(FIRST_LIBRARY, (GENERATION,))) == []


def test_a_wrong_sized_vector_is_rejected(store: DocumentStore, settings: Settings) -> None:
    """A dimension change must fail loudly.

    Silently accepting the wrong width would leave a library whose old and
    new chunks cannot be compared, and whose search results would be
    meaningless rather than merely wrong.
    """
    from haystack import Document

    mismatched = Document(
        id=str(uuid.uuid4()),
        content="wrong width",
        embedding=embed_text("wrong width", settings.embedding_dimensions + 8),
        meta={"library_id": str(FIRST_LIBRARY), "generation_id": str(GENERATION)},
    )

    with pytest.raises(Exception):  # noqa: B017 - each backend raises its own type
        store.write_documents([mismatched], policy=DuplicatePolicy.OVERWRITE)


def test_the_locator_survives_a_round_trip(store: DocumentStore, settings: Settings) -> None:
    """A citation is only useful if it still points somewhere after storage."""
    write(
        store,
        make_chunk(library_id=FIRST_LIBRARY, generation_id=GENERATION, content="cited passage"),
    )

    results = search(store, settings, FIRST_LIBRARY, (GENERATION,), "cited passage")

    assert results[0].locator is not None
    assert results[0].locator.page == 1
    assert results[0].locator.section == "Findings"


def test_naming_a_chunk_never_widens_the_scope(store: DocumentStore, settings: Settings) -> None:
    """How search reads the passages the graph points at.

    The graph hands back ids, and an id is not permission. Here the caller
    names one chunk in its own library, one in another library and one in a
    pending generation, and only the first may come back - on every backend,
    because each translates an `id` condition its own way.
    """
    mine = make_chunk(library_id=FIRST_LIBRARY, generation_id=GENERATION, content="named term")
    other = make_chunk(library_id=FIRST_LIBRARY, generation_id=GENERATION, content="x", ordinal=1)
    theirs = make_chunk(library_id=SECOND_LIBRARY, generation_id=GENERATION, content="named term")
    pending = make_chunk(
        library_id=FIRST_LIBRARY, generation_id=NEXT_GENERATION, content="named term", ordinal=2
    )
    write(store, mine, other, theirs, pending)
    named = [str(chunk.chunk_id) for chunk in (mine, theirs, pending)]

    hits = build_retriever(store, settings).run(
        query_embedding=HashingTextEmbedder().run("named term")["embedding"],
        filters=chunks_filter(FIRST_LIBRARY, (GENERATION,), named),
        top_k=10,
    )["documents"]

    assert [to_retrieved(hit).chunk_id for hit in hits] == [mine.chunk_id]
    assert hits[0].score is not None
