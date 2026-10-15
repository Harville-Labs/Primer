"""The knowledge graph, kept in Neo4j beside the vectors.

Scope works differently here than in the vector store, and the difference
is the point of having a graph. A chunk belongs to one generation of one
version, exactly as it does there. An entity does not: "Marie Curie" in two
papers of the same library is one node, because a traversal that could not
cross from one document to another would find nothing a vector search had
not already found.

So entities are scoped to a library, and everything that says where an
entity was seen - the chunk that mentions it, and every relation - carries
its generation. Reads follow only mentions and relations in the generations
the caller names, which makes a pending build invisible the same way the
vector filter does: an entity whose only mentions are pending is a node no
search can reach a passage through.

Every query names its library in the pattern it matches rather than in a
filter applied to the result. A library is never an optional parameter
here, for the same reason it is not one in `scope_filter`.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from neo4j import (
    Driver,
    GraphDatabase,
    ManagedTransaction,
    Query,
    RoutingControl,
    unit_of_work,
)
from primer_contracts.graph import ChunkGraph, entity_key

from primer_retrieval.config import Settings

#: The furthest a neighbourhood reaches from the entities a question names.
#: Written into `_NEIGHBOURHOOD` as a literal, because Cypher cannot take a
#: variable-length bound as a parameter; `hops` narrows it per call. Past
#: two, a well-connected entity reaches most of a library and the
#: neighbourhood stops meaning anything about the question.
MAX_HOPS = 2

#: Applied by the migration entrypoint, never at service startup: creating
#: a constraint takes schema privileges a serving process should not hold.
#: The entity constraint is also what makes concurrent writers converge on
#: one node per name instead of racing to create two.
SCHEMA = (
    "CREATE CONSTRAINT primer_entity_key IF NOT EXISTS "
    "FOR (entity:Entity) REQUIRE (entity.library_id, entity.key) IS UNIQUE",
    "CREATE CONSTRAINT primer_chunk_id IF NOT EXISTS "
    "FOR (chunk:Chunk) REQUIRE chunk.chunk_id IS UNIQUE",
    "CREATE INDEX primer_chunk_version IF NOT EXISTS "
    "FOR (chunk:Chunk) ON (chunk.library_id, chunk.document_version_id)",
    "CREATE INDEX primer_relation_chunk IF NOT EXISTS "
    "FOR ()-[relation:RELATES]-() ON (relation.chunk_id)",
    "CREATE INDEX primer_relation_version IF NOT EXISTS "
    "FOR ()-[relation:RELATES]-() ON (relation.library_id, relation.document_version_id)",
)

#: Rewriting a chunk replaces what it said rather than adding to it, so a
#: stage that ran twice leaves one copy - the same promise chunk ids make
#: to the vector store.
_CLEAR_RELATIONS = """
MATCH ()-[relation:RELATES {library_id: $library_id}]->()
WHERE relation.chunk_id IN $chunk_ids
DELETE relation
"""

_CLEAR_CHUNKS = """
UNWIND $chunk_ids AS chunk_id
MATCH (chunk:Chunk {chunk_id: chunk_id, library_id: $library_id})
DETACH DELETE chunk
"""

#: A chunk node is written even when it mentions nothing, so that counting
#: a generation's chunks counts what was extracted rather than what was
#: extracted and turned out to be interesting.
#:
#: An entity's name, type and description are whatever first described it.
#: Later mentions add evidence, not a rewrite: letting the latest chunk win
#: would make an entity's label depend on indexing order.
_WRITE_MENTIONS = """
UNWIND $chunks AS row
CREATE (chunk:Chunk {
    chunk_id: row.chunk_id,
    library_id: $library_id,
    document_version_id: $document_version_id,
    generation_id: $generation_id
})
WITH chunk, row
UNWIND row.entities AS item
MERGE (entity:Entity {library_id: $library_id, key: item.key})
  ON CREATE SET entity.name = item.name,
                entity.type = item.type,
                entity.description = item.description
MERGE (chunk)-[:MENTIONS]->(entity)
"""

_WRITE_RELATIONS = """
UNWIND $relations AS row
MATCH (source:Entity {library_id: $library_id, key: row.source})
MATCH (target:Entity {library_id: $library_id, key: row.target})
CREATE (source)-[:RELATES {
    library_id: $library_id,
    document_version_id: $document_version_id,
    generation_id: $generation_id,
    chunk_id: row.chunk_id,
    label: row.label,
    description: row.description
}]->(target)
"""

#: Every relation on the path must belong to the library and to a named
#: generation, not only the chunk at the end of it. Checking the chunk alone
#: would let a pending build's relations steer which active passages come
#: back, which is a pending build answering questions by another route.
_NEIGHBOURHOOD = """
MATCH (seed:Entity {library_id: $library_id})
WHERE seed.key IN $keys
MATCH path = (seed)-[:RELATES*0..2]-(entity:Entity)
WHERE length(path) <= $hops
  AND all(relation IN relationships(path)
          WHERE relation.library_id = $library_id
            AND relation.generation_id IN $generation_ids)
WITH entity, min(length(path)) AS distance
MATCH (chunk:Chunk {library_id: $library_id})-[:MENTIONS]->(entity)
WHERE chunk.generation_id IN $generation_ids
WITH chunk, min(distance) AS distance, count(DISTINCT entity) AS support
RETURN chunk.chunk_id AS chunk_id, distance, support
ORDER BY distance ASC, support DESC, chunk_id ASC
LIMIT $limit
"""

#: `only` and `keep` mirror the vector store's delete and purge: one
#: generation of a version, or all of a version but the generation in use.
#: The library and version are matched in the pattern and are never
#: optional, so the widest this can reach is one version.
_ERASE_RELATIONS = """
MATCH ()-[relation:RELATES {
    library_id: $library_id,
    document_version_id: $document_version_id
}]->()
WHERE ($only IS NULL OR relation.generation_id = $only)
  AND ($keep IS NULL OR relation.generation_id <> $keep)
DELETE relation
"""

_ERASE_CHUNKS = """
MATCH (chunk:Chunk {library_id: $library_id, document_version_id: $document_version_id})
WHERE ($only IS NULL OR chunk.generation_id = $only)
  AND ($keep IS NULL OR chunk.generation_id <> $keep)
DETACH DELETE chunk
"""

#: Swept across the whole library rather than only the entities the erased
#: chunks mentioned. It costs a scan of one library's entities, and in
#: return an entity orphaned by a rewrite, or by an erase that was
#: interrupted, is collected by the next erase instead of never. An entity
#: is a name someone's document contained, and deleting the document has to
#: delete it.
_COLLECT_ORPHANS = """
MATCH (entity:Entity {library_id: $library_id})
WHERE NOT EXISTS { (entity)--() }
DELETE entity
"""


@dataclass(frozen=True)
class GraphHit:
    """A chunk reached from the entities a question names."""

    chunk_id: UUID
    #: Hops from the nearest seed. Zero means the chunk names a seed itself.
    distance: int
    #: How many of the entities reached this chunk mentions. A passage that
    #: names three of them is more likely about the question than one that
    #: names one.
    support: int


class GraphStore:
    """Scoped reads, writes and erasure over one Neo4j database."""

    def __init__(self, driver: Driver, database: str, timeout_seconds: float) -> None:
        self._driver = driver
        self._database = database
        self._timeout = timeout_seconds

    def apply_schema(self) -> None:
        for statement in SCHEMA:
            self._driver.execute_query(
                Query(statement, timeout=self._timeout), database_=self._database
            )

    def write(
        self,
        library_id: UUID,
        document_version_id: UUID,
        generation_id: UUID,
        graphs: Sequence[ChunkGraph],
    ) -> None:
        """Replace what these chunks say, in one transaction.

        One transaction because a half-written chunk is worse than none: its
        mentions without its relations would answer a traversal with a
        graph that is missing exactly the edges the passage stated.
        """
        scope = {
            "library_id": str(library_id),
            "document_version_id": str(document_version_id),
            "generation_id": str(generation_id),
        }
        chunk_ids = [str(graph.chunk_id) for graph in graphs]
        chunks = [
            {
                "chunk_id": str(graph.chunk_id),
                "entities": [
                    {
                        "key": entity_key(entity.name),
                        "name": entity.name,
                        "type": entity.type,
                        "description": entity.description,
                    }
                    for entity in graph.entities
                ],
            }
            for graph in graphs
        ]
        relations = [
            {
                "chunk_id": str(graph.chunk_id),
                "source": entity_key(relation.source),
                "target": entity_key(relation.target),
                "label": relation.label,
                "description": relation.description,
            }
            for graph in graphs
            for relation in graph.relations
        ]

        def work(tx: ManagedTransaction) -> None:
            tx.run(_CLEAR_RELATIONS, {**scope, "chunk_ids": chunk_ids})
            tx.run(_CLEAR_CHUNKS, {**scope, "chunk_ids": chunk_ids})
            tx.run(_WRITE_MENTIONS, {**scope, "chunks": chunks})
            tx.run(_WRITE_RELATIONS, {**scope, "relations": relations})

        self._in_write(work)

    def neighbourhood(
        self,
        library_id: UUID,
        generation_ids: Sequence[UUID],
        names: Sequence[str],
        hops: int,
        limit: int,
    ) -> list[GraphHit]:
        """Chunks that mention the named entities or their neighbours.

        Ordered nearest first, then by how many reached entities a chunk
        mentions. An empty generation set finds nothing rather than
        everything, because "no active generation" means nothing is
        searchable, not that the filter is off.
        """
        if not 0 <= hops <= MAX_HOPS:
            raise ValueError(f"hops must be between 0 and {MAX_HOPS}, not {hops}")
        if not generation_ids or not names:
            return []
        records = self._driver.execute_query(
            Query(_NEIGHBOURHOOD, timeout=self._timeout),
            parameters_={
                "library_id": str(library_id),
                "generation_ids": [str(generation) for generation in generation_ids],
                "keys": sorted({entity_key(name) for name in names}),
                "hops": hops,
                "limit": limit,
            },
            routing_=RoutingControl.READ,
            database_=self._database,
        ).records
        return [
            GraphHit(
                chunk_id=UUID(record["chunk_id"]),
                distance=record["distance"],
                support=record["support"],
            )
            for record in records
        ]

    def erase(
        self,
        library_id: UUID,
        document_version_id: UUID,
        *,
        only: UUID | None = None,
        keep: UUID | None = None,
    ) -> None:
        """Remove a version's graph, or one generation of it, or all but one.

        Erasing nothing is success, so a redelivered cleanup message is
        safe to act on twice.
        """
        parameters = {
            "library_id": str(library_id),
            "document_version_id": str(document_version_id),
            "only": str(only) if only else None,
            "keep": str(keep) if keep else None,
        }

        def work(tx: ManagedTransaction) -> None:
            tx.run(_ERASE_RELATIONS, parameters)
            tx.run(_ERASE_CHUNKS, parameters)
            tx.run(_COLLECT_ORPHANS, parameters)

        self._in_write(work)

    def close(self) -> None:
        self._driver.close()

    def _in_write(self, work: Callable[[ManagedTransaction], Any]) -> None:
        with self._driver.session(database=self._database) as session:
            session.execute_write(unit_of_work(timeout=self._timeout)(work))


def build_graph_store(settings: Settings) -> GraphStore | None:
    """The configured graph, or nothing at all.

    The driver connects on first use rather than here, so a deployment
    whose Neo4j is down still starts and still answers from its vectors.
    """
    if not settings.graph_url:
        return None
    password = settings.graph_password.get_secret_value() if settings.graph_password else None
    driver = GraphDatabase.driver(
        settings.graph_url,
        auth=(settings.graph_username, password) if password else None,
        connection_timeout=settings.graph_timeout_seconds,
        connection_acquisition_timeout=settings.graph_timeout_seconds,
    )
    return GraphStore(driver, settings.graph_database, settings.graph_timeout_seconds)
