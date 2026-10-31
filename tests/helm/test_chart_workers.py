"""How each ingestion worker runs its tasks: processes or threads.

Kept apart from `test_chart.py`, which already holds the rest of the
worker assertions and is past the size a file should be.
"""

from __future__ import annotations

from test_chart import containers, named, render


def pool_of(worker: str) -> str:
    command = containers(named(render(), "Deployment", f"-worker-{worker}"))[0]["command"]
    assert "--pool" in command, worker
    return command[command.index("--pool") + 1]


def test_the_index_worker_waits_on_threads() -> None:
    """Every index task waits on a model endpoint.

    On the prefork default each document in flight is a whole interpreter,
    with its own copy of every client, so eight documents cost eight times
    the memory to do nothing but wait on sockets.
    """
    assert pool_of("index") == "threads"


def test_the_parse_worker_parses_in_processes() -> None:
    """Parsing holds the CPU, and threads would share one interpreter lock."""
    assert pool_of("parse") == "prefork"
