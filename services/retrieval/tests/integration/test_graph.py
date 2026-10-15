"""The knowledge graph against a real Neo4j.

Entities are shared across a library's documents, which is the one way the
graph is scoped differently from the vectors. Most of these tests exist to
prove that sharing stops at the library and at the generations a caller
names.

Every test makes up its own libraries, so the session's one database is
shared without any test seeing another's writes - unless scoping is broken,
which is what the tests are here to catch.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable, Iterator
from typing import Any, LiteralString
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from primer_contracts.graph import ChunkGraph, GraphEntity, GraphRelation
from primer_contracts.identity import Principal
from primer_retrieval.graph import GraphStore
from testcontainers.community.neo4j import Neo4jContainer

TOKEN_HEADER = {"X-Primer-Service-Token": "retrieval-service-token"}


def entities(*names: str) -> tuple[GraphEntity, ...]:
    return tuple(GraphEntity(name=name, type="thing") for name in names)


def relate(source: str, target: str, label: str = "related to") -> GraphRelation:
    return GraphRelation(source=source, target=target, label=label)


def chunk_graph(names: tuple[str, ...], *relations: GraphRelation) -> ChunkGraph:
    return ChunkGraph(chunk_id=uuid.uuid4(), entities=entities(*names), relations=relations)


def reached(
    graph: GraphStore,
    library: UUID,
    generations: list[UUID],
    *names: str,
    hops: int = 2,
) -> set[UUID]:
    hits = graph.neighbourhood(library, generations, names, hops=hops, limit=50)
    return {hit.chunk_id for hit in hits}


Count = Callable[..., int]


@pytest.fixture
def count(neo4j: Neo4jContainer) -> Iterator[Count]:
    """Counts read straight from Neo4j, not through the store under test."""
    with neo4j.get_driver() as driver:

        def run(query: LiteralString, **parameters: Any) -> int:
            return driver.execute_query(query, parameters).records[0]["n"]

        yield run


def test_a_chunk_is_reached_through_the_entity_it_names(graph: GraphStore) -> None:
    library, version, generation = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    curie = chunk_graph(("Marie Curie", "Polonium"), relate("Marie Curie", "Polonium"))
    graph.write(library, version, generation, [curie])

    assert reached(graph, library, [generation], "marie curie") == {curie.chunk_id}


def test_traversal_crosses_documents_through_a_shared_entity(graph: GraphStore) -> None:
    """The reason entities are library-scoped rather than per document.

    The answering passage never names the entity in the question. It is
    found only because another document links the two.
    """
    library, generation = uuid.uuid4(), uuid.uuid4()
    linking = chunk_graph(("Marie Curie", "Pierre Curie"), relate("Marie Curie", "Pierre Curie"))
    answering = chunk_graph(("Pierre Curie", "Piezoelectricity"))
    graph.write(library, uuid.uuid4(), generation, [linking])
    graph.write(library, uuid.uuid4(), generation, [answering])

    assert reached(graph, library, [generation], "Marie Curie") == {
        linking.chunk_id,
        answering.chunk_id,
    }


def test_the_same_name_in_two_libraries_never_links_them(graph: GraphStore) -> None:
    """Identical words in both libraries, so only scoping separates them."""
    mine, theirs, generation = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    my_chunk = chunk_graph(("Marie Curie",))
    their_chunk = chunk_graph(("Marie Curie", "Radium"), relate("Marie Curie", "Radium"))
    graph.write(mine, uuid.uuid4(), generation, [my_chunk])
    graph.write(theirs, uuid.uuid4(), generation, [their_chunk])

    assert reached(graph, mine, [generation], "Marie Curie", "Radium") == {my_chunk.chunk_id}


def test_a_pending_generation_is_invisible_until_named(graph: GraphStore) -> None:
    library, active, pending = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    current = chunk_graph(("Marie Curie",))
    rebuilt = chunk_graph(("Marie Curie", "Radium"), relate("Marie Curie", "Radium"))
    graph.write(library, uuid.uuid4(), active, [current])
    graph.write(library, uuid.uuid4(), pending, [rebuilt])

    assert reached(graph, library, [active], "Marie Curie") == {current.chunk_id}
    assert reached(graph, library, [active, pending], "Marie Curie") == {
        current.chunk_id,
        rebuilt.chunk_id,
    }


def test_a_pending_relation_cannot_steer_traversal_to_an_active_chunk(
    graph: GraphStore,
) -> None:
    """Checking only the chunk at the end of a path is not enough.

    Here the active chunk mentioning Radium is reachable from Marie Curie
    only across a relation the pending build wrote. Following it would let
    an unactivated build decide which active passages answer a question.
    """
    library, active, pending = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    radium = chunk_graph(("Radium",))
    curie = chunk_graph(("Marie Curie",))
    bridge = chunk_graph(("Marie Curie", "Radium"), relate("Marie Curie", "Radium"))
    graph.write(library, uuid.uuid4(), active, [radium, curie])
    graph.write(library, uuid.uuid4(), pending, [bridge])

    assert reached(graph, library, [active], "Marie Curie") == {curie.chunk_id}


def test_hops_bound_how_far_a_neighbourhood_reaches(graph: GraphStore) -> None:
    library, generation = uuid.uuid4(), uuid.uuid4()
    first = chunk_graph(("A", "B"), relate("A", "B"))
    second = chunk_graph(("B", "C"), relate("B", "C"))
    far = chunk_graph(("C",))
    graph.write(library, uuid.uuid4(), generation, [first, second, far])

    hits = graph.neighbourhood(library, [generation], ["A"], hops=2, limit=50)
    distances = {hit.chunk_id: hit.distance for hit in hits}
    assert distances == {first.chunk_id: 0, second.chunk_id: 1, far.chunk_id: 2}
    assert far.chunk_id not in reached(graph, library, [generation], "A", hops=1)


def test_more_hops_than_the_query_can_express_is_refused(graph: GraphStore) -> None:
    with pytest.raises(ValueError, match="hops"):
        graph.neighbourhood(uuid.uuid4(), [uuid.uuid4()], ["A"], hops=3, limit=5)


def test_no_generations_finds_nothing_rather_than_everything(graph: GraphStore) -> None:
    library = uuid.uuid4()
    graph.write(library, uuid.uuid4(), uuid.uuid4(), [chunk_graph(("A",))])
    assert reached(graph, library, [], "A") == set()


def test_rewriting_a_chunk_replaces_what_it_said(graph: GraphStore, count: Count) -> None:
    library, version, generation = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    chunk_id = uuid.uuid4()
    before = ChunkGraph(
        chunk_id=chunk_id, entities=entities("A", "B"), relations=(relate("A", "B"),)
    )
    after = ChunkGraph(chunk_id=chunk_id, entities=entities("A"))
    graph.write(library, version, generation, [before])
    graph.write(library, version, generation, [before])
    chunks = "MATCH (:Chunk {chunk_id: $c}) RETURN count(*) AS n"
    relations = "MATCH ()-[r:RELATES {chunk_id: $c}]->() RETURN count(r) AS n"
    assert count(chunks, c=str(chunk_id)) == 1
    assert count(relations, c=str(chunk_id)) == 1

    graph.write(library, version, generation, [after])
    assert count(relations, c=str(chunk_id)) == 0
    assert reached(graph, library, [generation], "B") == set()


def library_size(count: Count, library: UUID) -> int:
    return count("MATCH (n {library_id: $library}) RETURN count(n) AS n", library=str(library))


def test_erasing_one_generation_leaves_the_others(graph: GraphStore) -> None:
    library, version, old, new = uuid.uuid4(), uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    kept = chunk_graph(("Marie Curie",))
    graph.write(library, version, old, [chunk_graph(("Marie Curie", "Radium"))])
    graph.write(library, version, new, [kept])

    graph.erase(library, version, only=old)
    graph.erase(library, version, only=old)

    assert reached(graph, library, [old, new], "Marie Curie", "Radium") == {kept.chunk_id}


def test_purging_a_version_spares_the_generation_kept(graph: GraphStore) -> None:
    library, version, old, current = uuid.uuid4(), uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    kept = chunk_graph(("A",))
    graph.write(library, version, old, [chunk_graph(("A", "B"), relate("A", "B"))])
    graph.write(library, version, current, [kept])

    graph.erase(library, version, keep=current)

    assert reached(graph, library, [old, current], "A", "B") == {kept.chunk_id}


def test_erasing_a_document_takes_its_entities_with_it(graph: GraphStore, count: Count) -> None:
    """An entity is a name someone's document contained.

    One that outlived its last mention would keep part of a deleted
    document in the library, and in Neo4j, indefinitely.
    """
    library, version, generation = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    graph.write(library, version, generation, [chunk_graph(("A", "B"), relate("A", "B"))])
    assert library_size(count, library) > 0

    graph.erase(library, version)

    assert library_size(count, library) == 0


def test_erasing_one_document_keeps_entities_another_still_mentions(graph: GraphStore) -> None:
    library, generation = uuid.uuid4(), uuid.uuid4()
    gone, staying = uuid.uuid4(), uuid.uuid4()
    remaining = chunk_graph(("Marie Curie",))
    graph.write(library, gone, generation, [chunk_graph(("Marie Curie",))])
    graph.write(library, staying, generation, [remaining])

    graph.erase(library, gone)

    assert reached(graph, library, [generation], "Marie Curie") == {remaining.chunk_id}


def delete_body(library: UUID, version: UUID, generation: UUID) -> dict[str, Any]:
    reader = Principal(subject="reader", user_id=uuid.uuid5(uuid.NAMESPACE_URL, "reader"))
    return {
        "principal": reader.model_dump(mode="json"),
        "library_id": str(library),
        "document_version_id": str(version),
        "generation_id": str(generation),
    }


def test_deleting_a_generation_erases_its_graph(graph_client: TestClient, count: Count) -> None:
    library, version, generation = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    graph = graph_client.app.state.retrieval.graph  # ty: ignore[unresolved-attribute]
    graph.write(library, version, generation, [chunk_graph(("A",))])

    response = graph_client.post(
        "/internal/v1/delete", json=delete_body(library, version, generation), headers=TOKEN_HEADER
    )

    assert response.status_code == 200, response.text
    assert library_size(count, library) == 0


def test_purging_a_version_erases_its_graph(graph_client: TestClient) -> None:
    library, version, old, current = uuid.uuid4(), uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    graph = graph_client.app.state.retrieval.graph  # ty: ignore[unresolved-attribute]
    kept = chunk_graph(("A",))
    graph.write(library, version, old, [chunk_graph(("A", "B"))])
    graph.write(library, version, current, [kept])
    body = delete_body(library, version, old)
    body.pop("generation_id")

    response = graph_client.post(
        "/internal/v1/purge",
        json={**body, "keep_generation_id": str(current)},
        headers=TOKEN_HEADER,
    )

    assert response.status_code == 200, response.text
    assert reached(graph, library, [old, current], "A", "B") == {kept.chunk_id}
