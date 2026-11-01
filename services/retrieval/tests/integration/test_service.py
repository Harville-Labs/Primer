"""Index, verify, search, delete: the lifecycle a job drives."""

from __future__ import annotations

import uuid
from typing import Any

from fastapi.testclient import TestClient
from primer_contracts.identity import Principal

TOKEN_HEADER = {"X-Primer-Service-Token": "retrieval-service-token"}
MINE = uuid.uuid5(uuid.NAMESPACE_URL, "library-mine")
THEIRS = uuid.uuid5(uuid.NAMESPACE_URL, "library-theirs")
GENERATION = uuid.uuid5(uuid.NAMESPACE_URL, "gen-1")
NEXT_GENERATION = uuid.uuid5(uuid.NAMESPACE_URL, "gen-2")


def principal(subject: str = "reader") -> dict[str, Any]:
    return Principal(subject=subject, user_id=uuid.uuid5(uuid.NAMESPACE_URL, subject)).model_dump(
        mode="json"
    )


def chunk(library: uuid.UUID, generation: uuid.UUID, content: str, ordinal: int = 0):
    version = uuid.uuid5(uuid.NAMESPACE_URL, f"version-{library}")
    return {
        "chunk_id": str(uuid.uuid5(uuid.NAMESPACE_URL, f"{version}:{generation}:{ordinal}")),
        "ordinal": ordinal,
        "library_id": str(library),
        "document_id": str(uuid.uuid5(uuid.NAMESPACE_URL, f"document-{library}")),
        "document_version_id": str(version),
        "owner_user_id": str(uuid.uuid5(uuid.NAMESPACE_URL, f"owner-{library}")),
        "generation_id": str(generation),
        "content": content,
        "embedding_text": content,
        "locator": {"page": 2, "section": "Results"},
        "filename": "paper.pdf",
    }


def index(client: TestClient, library: uuid.UUID, generation: uuid.UUID, *chunks):
    version = chunks[0]["document_version_id"]
    return client.post(
        "/internal/v1/index",
        json={
            "principal": principal(),
            "library_id": str(library),
            "document_version_id": version,
            "generation_id": str(generation),
            "chunks": list(chunks),
        },
        headers=TOKEN_HEADER,
    )


def search(client: TestClient, library: uuid.UUID, generations, query: str, limit: int = 10):
    return client.post(
        "/internal/v1/search",
        json={
            "principal": principal(),
            "library_id": str(library),
            "generation_ids": [str(g) for g in generations],
            "query": query,
            "limit": limit,
        },
        headers=TOKEN_HEADER,
    )


def generation_body(library: uuid.UUID, generation: uuid.UUID) -> dict[str, Any]:
    return {
        "principal": principal(),
        "library_id": str(library),
        "document_version_id": str(uuid.uuid5(uuid.NAMESPACE_URL, f"version-{library}")),
        "generation_id": str(generation),
    }


def test_indexed_chunks_become_searchable_and_citable(client: TestClient) -> None:
    written = index(client, MINE, GENERATION, chunk(MINE, GENERATION, "attention is efficient"))
    assert written.status_code == 200
    assert written.json()["written"] == 1

    results = search(client, MINE, [GENERATION], "attention").json()["chunks"]

    assert [c["content"] for c in results] == ["attention is efficient"]
    assert results[0]["locator"] == {"page": 2, "section": "Results"}
    assert results[0]["index_generation"] == str(GENERATION)


def test_search_never_crosses_a_library(client: TestClient) -> None:
    """The same words in two libraries; only the filter tells them apart."""
    index(client, MINE, GENERATION, chunk(MINE, GENERATION, "budget forecast detail"))
    index(client, THEIRS, GENERATION, chunk(THEIRS, GENERATION, "budget forecast detail"))

    results = search(client, MINE, [GENERATION], "budget forecast").json()["chunks"]

    assert results
    assert {c["library_id"] for c in results} == {str(MINE)}


def test_verify_counts_only_the_generation_asked_for(client: TestClient) -> None:
    """Activation refuses an incomplete index, so the count has to be exact."""
    index(
        client,
        MINE,
        GENERATION,
        chunk(MINE, GENERATION, "first passage", 0),
        chunk(MINE, GENERATION, "second passage", 1),
    )
    index(client, MINE, NEXT_GENERATION, chunk(MINE, NEXT_GENERATION, "rebuilt passage", 0))

    current = client.post(
        "/internal/v1/verify", json=generation_body(MINE, GENERATION), headers=TOKEN_HEADER
    )
    rebuilt = client.post(
        "/internal/v1/verify", json=generation_body(MINE, NEXT_GENERATION), headers=TOKEN_HEADER
    )

    assert current.json()["count"] == 2
    assert rebuilt.json()["count"] == 1


def test_a_rebuild_does_not_disturb_the_generation_in_use(client: TestClient) -> None:
    index(client, MINE, GENERATION, chunk(MINE, GENERATION, "the old answer"))
    index(client, MINE, NEXT_GENERATION, chunk(MINE, NEXT_GENERATION, "the new answer"))

    serving = search(client, MINE, [GENERATION], "answer").json()["chunks"]

    assert [c["content"] for c in serving] == ["the old answer"]


def test_reindexing_a_generation_overwrites_rather_than_doubles(client: TestClient) -> None:
    payload = chunk(MINE, GENERATION, "written twice")
    index(client, MINE, GENERATION, payload)
    index(client, MINE, GENERATION, payload)

    count = client.post(
        "/internal/v1/verify", json=generation_body(MINE, GENERATION), headers=TOKEN_HEADER
    )
    assert count.json()["count"] == 1


def test_delete_removes_one_generation_and_is_repeatable(client: TestClient) -> None:
    """A redelivered cleanup message must be safe, and report honestly."""
    index(client, MINE, GENERATION, chunk(MINE, GENERATION, "retire this"))
    index(client, MINE, NEXT_GENERATION, chunk(MINE, NEXT_GENERATION, "keep this"))

    first = client.post(
        "/internal/v1/delete", json=generation_body(MINE, GENERATION), headers=TOKEN_HEADER
    )
    second = client.post(
        "/internal/v1/delete", json=generation_body(MINE, GENERATION), headers=TOKEN_HEADER
    )

    assert first.json()["deleted"] == 1
    assert second.json()["deleted"] == 0
    assert search(client, MINE, [GENERATION], "retire").json()["chunks"] == []
    assert search(client, MINE, [NEXT_GENERATION], "keep").json()["chunks"]


def test_a_search_returns_no_more_than_its_limit(client: TestClient) -> None:
    index(
        client,
        MINE,
        GENERATION,
        *[chunk(MINE, GENERATION, f"passage number {n} about retrieval", n) for n in range(5)],
    )

    results = search(client, MINE, [GENERATION], "retrieval", limit=2).json()["chunks"]

    assert len(results) == 2


# Counting and deleting happen inside Qdrant, not in this service.
#
# Verify, delete, and purge each used to read a whole generation back - every
# passage, with its vector - only to count it or collect the ids to delete.
# That is correct at any size and unaffordable at a large one: a document of
# ten thousand passages at 1024 dimensions moved forty megabytes across the
# network to produce one integer. The results are the same either way, so
# only a store that refuses to be read back can tell the difference.


def two_generations(client: TestClient) -> None:
    for generation, passages in ((GENERATION, 3), (NEXT_GENERATION, 2)):
        chunks = [chunk(MINE, generation, f"passage {n}", ordinal=n) for n in range(passages)]
        assert index(client, MINE, generation, *chunks).status_code == 200


def test_verify_counts_without_reading_passages(unreadable: TestClient) -> None:
    two_generations(unreadable)

    response = unreadable.post(
        "/internal/v1/verify", json=generation_body(MINE, GENERATION), headers=TOKEN_HEADER
    )

    assert response.status_code == 200, response.text
    assert response.json()["count"] == 3


def test_delete_removes_a_generation_without_reading_it(unreadable: TestClient) -> None:
    two_generations(unreadable)

    response = unreadable.post(
        "/internal/v1/delete", json=generation_body(MINE, GENERATION), headers=TOKEN_HEADER
    )

    assert response.status_code == 200, response.text
    assert response.json()["deleted"] == 3
    kept = unreadable.post(
        "/internal/v1/verify", json=generation_body(MINE, NEXT_GENERATION), headers=TOKEN_HEADER
    )
    assert kept.json()["count"] == 2


def test_purge_spares_the_kept_generation_without_reading_any(unreadable: TestClient) -> None:
    two_generations(unreadable)
    body = generation_body(MINE, GENERATION)
    body.pop("generation_id")

    response = unreadable.post(
        "/internal/v1/purge",
        json={**body, "keep_generation_id": str(NEXT_GENERATION)},
        headers=TOKEN_HEADER,
    )

    assert response.status_code == 200, response.text
    assert response.json()["deleted"] == 3
    for generation, expected in ((GENERATION, 0), (NEXT_GENERATION, 2)):
        verified = unreadable.post(
            "/internal/v1/verify", json=generation_body(MINE, generation), headers=TOKEN_HEADER
        )
        assert verified.json()["count"] == expected


def test_purge_with_nothing_to_keep_erases_the_version(unreadable: TestClient) -> None:
    two_generations(unreadable)
    body = generation_body(MINE, GENERATION)
    body.pop("generation_id")

    response = unreadable.post("/internal/v1/purge", json=body, headers=TOKEN_HEADER)

    assert response.status_code == 200, response.text
    assert response.json()["deleted"] == 5


def test_readiness_does_not_count_the_collection(unreadable: TestClient) -> None:
    """Every probe on every replica, every few seconds: an exact count of
    tens of millions of points is a full scan each time."""
    two_generations(unreadable)

    assert unreadable.get("/health/ready").status_code == 200


def passages(client: TestClient, library: uuid.UUID, generations, limit: int = 500):
    return client.post(
        "/internal/v1/passages",
        json={
            "principal": principal(),
            "library_id": str(library),
            "generation_ids": [str(g) for g in generations],
            "limit": limit,
        },
        headers=TOKEN_HEADER,
    )


def test_passages_are_listed_in_reading_order_from_one_library_and_generation(
    client: TestClient,
) -> None:
    """Written backwards, beside another library and a pending build of this one.

    Eight, because Qdrant hands back a filter's matches in id order, and the
    ids are hashes: two passages come back in reading order half the time
    with no sorting at all.
    """
    index(
        client,
        MINE,
        GENERATION,
        *(chunk(MINE, GENERATION, f"passage {i}", ordinal=i) for i in reversed(range(8))),
    )
    index(client, MINE, NEXT_GENERATION, chunk(MINE, NEXT_GENERATION, "pending", ordinal=0))
    index(client, THEIRS, GENERATION, chunk(THEIRS, GENERATION, "theirs", ordinal=0))

    listed = passages(client, MINE, [GENERATION]).json()

    assert [p["content"] for p in listed["passages"]] == [f"passage {i}" for i in range(8)]
    assert listed["total"] == 8
    assert listed["passages"][0]["filename"] == "paper.pdf"
    assert listed["passages"][0]["locator"] == {"page": 2, "section": "Results"}


def test_a_listing_cut_short_says_how_many_the_library_holds(client: TestClient) -> None:
    index(
        client,
        MINE,
        GENERATION,
        *(chunk(MINE, GENERATION, f"passage {i}", ordinal=i) for i in range(3)),
    )

    listed = passages(client, MINE, [GENERATION], limit=2).json()

    assert [p["ordinal"] for p in listed["passages"]] == [0, 1]
    assert listed["total"] == 3
