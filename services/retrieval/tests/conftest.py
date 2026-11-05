"""Settings every Retrieval test needs, whatever it is about.

The graph is required (ADR 0014), so a test of scoping or reranking still
has to name one. Port 1 refuses at once, and the driver connects on first
use, so a test that never reaches the graph never opens a connection.
"""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def graph_settings_for_every_test(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PRIMER_GRAPH_URL", "bolt://127.0.0.1:1")
    monkeypatch.setenv("PRIMER_GRAPH_PASSWORD", "test-graph-password")
