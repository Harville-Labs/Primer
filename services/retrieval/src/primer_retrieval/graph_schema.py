"""Apply the knowledge graph's constraints and indexes, then exit.

    python -m primer_retrieval.graph_schema

Run once per release by the deployment's migration step, beside the
Alembic migrations, and never by Retrieval on startup. Every replica
creating the same constraint at once is the race a migration step exists
to avoid, and a service that will not start until Neo4j answers is worse
than one that starts and searches its vectors.

Not an Alembic migration, though it runs where they do: Alembic versions a
SQL schema, and the graph's is a handful of idempotent `IF NOT EXISTS`
statements with no history to record.
"""

from __future__ import annotations

from primer_retrieval.config import Settings
from primer_retrieval.graph import build_graph_store


def apply(settings: Settings) -> None:
    store = build_graph_store(settings)
    if store is None:
        # A step that only runs when the graph is on, finding it off, means
        # the deployment's two halves disagree. Succeeding quietly would
        # leave Retrieval writing into a graph with no constraints.
        raise SystemExit(
            "PRIMER_GRAPH_URL is not set, so there is no graph to apply a schema to. "
            "Set it to the Neo4j URL Retrieval uses, or do not run this step."
        )
    try:
        store.apply_schema()
    finally:
        store.close()


if __name__ == "__main__":
    apply(Settings())
