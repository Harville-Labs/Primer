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

import logging
import time
from collections.abc import Callable

from neo4j.exceptions import AuthError

from primer_retrieval.config import Settings
from primer_retrieval.errors import GRAPH_OUTAGES
from primer_retrieval.graph import build_graph_store

logger = logging.getLogger(__name__)

#: How long to wait for Neo4j to answer. A Neo4j installed in the same
#: release starts alongside this step, not before it - Helm cannot order a
#: hook after a StatefulSet becoming ready - and on slow storage it takes
#: minutes rather than seconds to open its store.
READY_DEADLINE_SECONDS = 300.0
RETRY_INTERVAL_SECONDS = 2.0


def apply(settings: Settings, deadline_seconds: float = READY_DEADLINE_SECONDS) -> None:
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
        until_reachable(store.apply_schema, deadline_seconds, time.sleep, time.monotonic)
    except AuthError as error:
        # Neo4j keeps the password it first started with, so a Secret changed
        # afterwards lands here rather than at Neo4j.
        raise SystemExit(
            f"Neo4j at {settings.graph_url} refused PRIMER_GRAPH_USERNAME and "
            "PRIMER_GRAPH_PASSWORD. If the password was changed after Neo4j first "
            "started, change it in Neo4j too."
        ) from error
    except GRAPH_OUTAGES as error:
        raise SystemExit(
            f"Neo4j at {settings.graph_url} did not answer within {deadline_seconds:.0f} "
            "seconds. Check that it is running and that Retrieval's network can reach it."
        ) from error
    finally:
        store.close()


def until_reachable(
    work: Callable[[], None],
    deadline_seconds: float,
    sleep: Callable[[float], None],
    clock: Callable[[], float],
) -> None:
    """Run `work`, retrying while the graph is unreachable and only then.

    A wrong password or a bad statement fails at once: waiting five minutes
    to report a typo is how a deployment step earns a reputation for hanging.
    """
    give_up = clock() + deadline_seconds
    while True:
        try:
            work()
            return
        except GRAPH_OUTAGES:
            if clock() >= give_up:
                raise
            logger.warning("Neo4j is not answering yet; retrying")
            sleep(RETRY_INTERVAL_SECONDS)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    apply(Settings())
