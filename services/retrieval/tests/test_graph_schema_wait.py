"""Waiting for a Neo4j that is still starting, without one."""

from __future__ import annotations

import pytest
from neo4j.exceptions import AuthError, ServiceUnavailable
from primer_retrieval import graph_schema
from primer_retrieval.config import Settings
from primer_retrieval.graph_schema import apply, until_reachable


class Clock:
    def __init__(self) -> None:
        self.now = 0.0
        self.slept: list[float] = []

    def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.now += seconds

    def __call__(self) -> float:
        return self.now


def failing(times: int, error: Exception):
    calls: list[int] = []

    def work() -> None:
        calls.append(1)
        if len(calls) <= times:
            raise error

    return work, calls


def test_a_neo4j_that_answers_late_is_waited_for() -> None:
    """A Neo4j installed in the same release is still booting when this runs."""
    clock = Clock()
    work, calls = failing(3, ServiceUnavailable("starting"))

    until_reachable(work, 60, clock.sleep, clock)

    assert len(calls) == 4
    assert len(clock.slept) == 3


def test_waiting_stops_at_the_deadline() -> None:
    clock = Clock()
    work, calls = failing(1000, ServiceUnavailable("down"))

    with pytest.raises(ServiceUnavailable):
        until_reachable(work, 10, clock.sleep, clock)

    assert clock.now >= 10
    assert len(calls) < 1000


def test_a_wrong_password_is_not_waited_out() -> None:
    """Retrying an authentication failure only delays the message that fixes it."""
    clock = Clock()
    work, calls = failing(1000, AuthError("wrong password"))

    with pytest.raises(AuthError):
        until_reachable(work, 300, clock.sleep, clock)

    assert len(calls) == 1
    assert clock.slept == []


class UnreachableStore:
    closed = False

    def apply_schema(self) -> None:
        raise ServiceUnavailable("down")

    def close(self) -> None:
        self.closed = True


def test_giving_up_names_the_url_it_was_waiting_for(monkeypatch: pytest.MonkeyPatch) -> None:
    store = UnreachableStore()
    monkeypatch.setattr(graph_schema, "build_graph_store", lambda settings: store)

    with pytest.raises(SystemExit) as raised:
        apply(Settings(graph_url="bolt://neo4j.graph:7687"), deadline_seconds=0)

    assert "bolt://neo4j.graph:7687" in str(raised.value)
    assert store.closed


class RefusingStore(UnreachableStore):
    def apply_schema(self) -> None:
        raise AuthError("unauthorized")


def test_a_refused_password_says_which_one_to_check(monkeypatch: pytest.MonkeyPatch) -> None:
    """A Neo4j keeps the password it first started with.

    Changing the Secret afterwards is the usual way to get here, and a bare
    driver traceback does not say that the Secret is the thing to look at.
    """
    store = RefusingStore()
    monkeypatch.setattr(graph_schema, "build_graph_store", lambda settings: store)

    with pytest.raises(SystemExit) as raised:
        apply(Settings(graph_url="bolt://neo4j.graph:7687"))

    assert "PRIMER_GRAPH_PASSWORD" in str(raised.value)
    assert store.closed
