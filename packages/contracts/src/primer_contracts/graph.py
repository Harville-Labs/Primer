"""What a passage says about the things it mentions, as a knowledge graph.

A chunk's graph is produced by a model reading the chunk, which makes every
field here untrusted output that has been parsed rather than text a person
wrote. The bounds are deliberately tight: a name long enough to be a
paragraph is a model that has stopped extracting and started summarising,
and accepting it would put that paragraph into every neighbourhood the
entity appears in.
"""

from __future__ import annotations

import re
from uuid import UUID

from pydantic import Field, model_validator

from primer_contracts.base import WireModel

_WHITESPACE = re.compile(r"\s+")


def entity_key(name: str) -> str:
    """The identity two mentions of an entity share.

    Case and spacing are not identity: "Marie Curie", "marie curie" and
    "Marie  Curie" are one person to a reader and must be one node to the
    graph, or a library's mentions of her split into islands nothing
    traverses between. Anything beyond that - aliases, abbreviations,
    "Curie" alone - is resolution, which is a judgement this function is
    not the place to make.
    """
    return _WHITESPACE.sub(" ", name).strip().casefold()


class GraphEntity(WireModel):
    name: str = Field(min_length=1, max_length=200)
    #: A free label such as "person" or "method". Not an enum: the useful
    #: set depends on what a library is about, and a fixed list would force
    #: a model to misfile whatever the list did not anticipate.
    type: str = Field(default="", max_length=64)
    description: str = Field(default="", max_length=1000)


class GraphRelation(WireModel):
    source: str = Field(min_length=1, max_length=200)
    target: str = Field(min_length=1, max_length=200)
    label: str = Field(min_length=1, max_length=100)
    description: str = Field(default="", max_length=1000)


class ChunkGraph(WireModel):
    """The entities one chunk mentions and the relations it states.

    A relation may only join entities the same chunk names. The chunk is
    the evidence for the relation, and a citation drawn through it has to
    be able to point at a passage that mentions both ends.
    """

    chunk_id: UUID
    entities: tuple[GraphEntity, ...] = Field(default=(), max_length=64)
    relations: tuple[GraphRelation, ...] = Field(default=(), max_length=128)

    @model_validator(mode="after")
    def _relations_join_named_entities(self) -> ChunkGraph:
        named = {entity_key(entity.name) for entity in self.entities}
        for relation in self.relations:
            for end in (relation.source, relation.target):
                if entity_key(end) not in named:
                    raise ValueError(
                        f"relation {relation.label!r} names {end!r}, which is not one of "
                        "this chunk's entities"
                    )
        return self
