"""Repository-wide pytest configuration."""

from __future__ import annotations

from pathlib import Path

import pytest

# Test helper modules are named for their suite - control_support,
# conformance_support - not "support". pytest puts every test directory on
# sys.path, so two files sharing a name shadow each other and whichever is
# collected first wins.

#: Directories whose tests need Docker. Marking by location rather than by
#: decorator means a new test file cannot forget to declare itself.
CONTAINER_DIRECTORIES = {"integration", "vector_conformance"}


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    """Mark everything that starts a container automatically.

    These tests run real PostgreSQL, Qdrant and Neo4j, so a contributor without
    Docker can run `pytest -m "not integration"`. CI runs the whole suite.
    """
    for item in items:
        if CONTAINER_DIRECTORIES & set(Path(str(item.fspath)).parts):
            item.add_marker("integration")
