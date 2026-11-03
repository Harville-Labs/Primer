"""Retrieval started before its Qdrant was listening.

The chart's bundled Qdrant and Retrieval start together on every install and
upgrade, so Retrieval's first touch of the store routinely lands while Qdrant
is still refusing connections. That has to cost one failed probe, not the pod.
"""

from __future__ import annotations

import socket
from collections.abc import Callable

import pytest
from primer_retrieval.app import READINESS_FILTER
from primer_retrieval.config import Settings
from primer_retrieval.stores import build_document_store
from testcontainers.community.qdrant import QdrantContainer


def unused_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def test_a_store_first_used_before_qdrant_is_up_creates_its_collection_once_it_is(
    qdrant_on_port: Callable[[int], QdrantContainer],
) -> None:
    """The collection must be created by the first use that reaches Qdrant.

    The integration creates the collection lazily and caches its client
    before doing so. If that first setup failed, the client stayed cached and
    setup never ran again: every later call, the readiness probe included,
    answered that the collection did not exist, until someone deleted the pod.
    """
    port = unused_port()
    store = build_document_store(
        Settings(qdrant_url=f"http://127.0.0.1:{port}", qdrant_index="late_qdrant")
    )

    with pytest.raises(Exception):  # noqa: B017 - any transport error; nothing is listening
        store.count_documents_by_filter(READINESS_FILTER)

    with qdrant_on_port(port):
        assert store.count_documents_by_filter(READINESS_FILTER) == 0
