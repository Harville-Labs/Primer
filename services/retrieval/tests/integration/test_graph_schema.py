"""The migration step that prepares Neo4j, against a real one."""

from __future__ import annotations

import re
from typing import LiteralString, cast

import pytest
from primer_retrieval.config import Settings
from primer_retrieval.graph import SCHEMA
from primer_retrieval.graph_schema import apply
from testcontainers.community.neo4j import Neo4jContainer

#: Read out of the statements themselves, so a constraint added to the
#: schema is a constraint this test checks without being told about it.
DECLARED = re.compile(r"CREATE (CONSTRAINT|INDEX) (\w+) IF NOT EXISTS")


def declared() -> dict[str, str]:
    found = {}
    for statement in SCHEMA:
        match = DECLARED.match(statement)
        assert match, f"cannot read a name out of {statement!r}"
        found[match.group(2)] = match.group(1)
    return found


def present(neo4j: Neo4jContainer) -> set[str]:
    with neo4j.get_driver() as driver:
        constraints = driver.execute_query("SHOW CONSTRAINTS YIELD name").records
        indexes = driver.execute_query("SHOW INDEXES YIELD name").records
    return {record["name"] for record in [*constraints, *indexes]}


def test_the_step_creates_every_constraint_and_index_the_store_declares(
    neo4j: Neo4jContainer, neo4j_settings: Settings
) -> None:
    """From nothing, and twice over: a release reruns it on every upgrade.

    The session's database already has the schema, so it is dropped first;
    otherwise this would pass on a step that did nothing at all.
    """
    names = declared()
    assert names, "the store declares no schema, so this test would check nothing"
    with neo4j.get_driver() as driver:
        for name, kind in names.items():
            # Names read out of Primer's own schema constant, not input.
            driver.execute_query(cast(LiteralString, f"DROP {kind} {name} IF EXISTS"))
    assert not names.keys() & present(neo4j)

    apply(neo4j_settings)
    apply(neo4j_settings)

    assert names.keys() <= present(neo4j)


def test_the_step_refuses_to_run_with_the_graph_off() -> None:
    """Succeeding would leave a deployment believing its graph was prepared."""
    with pytest.raises(SystemExit) as raised:
        apply(Settings(graph_url=None))

    assert "PRIMER_GRAPH_URL" in str(raised.value)
