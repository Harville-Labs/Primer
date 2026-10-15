"""A chunk's graph, as it crosses from extraction to the graph store."""

from __future__ import annotations

from uuid import uuid4

import pytest
from primer_contracts.graph import ChunkGraph, GraphEntity, GraphRelation, entity_key
from pydantic import ValidationError


def test_case_and_spacing_do_not_make_two_entities() -> None:
    assert entity_key("Marie Curie") == entity_key("  marie\n  CURIE ")


def test_different_names_stay_different_entities() -> None:
    """Resolution beyond case and spacing is a judgement, not normalisation."""
    assert entity_key("Curie") != entity_key("Marie Curie")


def test_a_relation_may_join_entities_its_chunk_names_in_any_case() -> None:
    graph = ChunkGraph(
        chunk_id=uuid4(),
        entities=(GraphEntity(name="Marie Curie"), GraphEntity(name="Polonium")),
        relations=(GraphRelation(source="marie curie", target="POLONIUM", label="discovered"),),
    )
    assert len(graph.relations) == 1


def test_a_relation_to_an_entity_the_chunk_never_named_is_refused() -> None:
    """The chunk is the evidence for the relation.

    A relation whose end the passage does not mention would be cited to a
    passage that cannot support it.
    """
    with pytest.raises(ValidationError, match="not one of this chunk's entities"):
        ChunkGraph(
            chunk_id=uuid4(),
            entities=(GraphEntity(name="Marie Curie"),),
            relations=(GraphRelation(source="Marie Curie", target="Radium", label="discovered"),),
        )


def test_an_entity_name_that_has_become_a_paragraph_is_refused() -> None:
    with pytest.raises(ValidationError):
        GraphEntity(name="x" * 201)
