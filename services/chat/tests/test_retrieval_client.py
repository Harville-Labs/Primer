"""How Chat reads Retrieval's refusals.

A 422 is either Retrieval declining a question it understood, which the user
should be told about, or a request Chat built wrongly, which is a bug here.
Only the problem's code tells them apart.
"""

from __future__ import annotations

import uuid

import httpx2
import pytest
from primer_chat.clients import RetrievalClient, SearchRefused, SearchUnavailable
from primer_chat.config import Settings
from primer_contracts.identity import Principal
from primer_contracts.indexing import SearchRequest

REQUEST = SearchRequest(
    principal=Principal(subject="reader", user_id=uuid.uuid4()),
    library_id=uuid.uuid4(),
    generation_ids=(uuid.uuid4(),),
    query="What are the main themes here?",
    limit=8,
    breadth="library",
)


def answering(status: int, body: dict[str, object]) -> RetrievalClient:
    client = RetrievalClient(Settings())
    # The transport is the one thing replaced: everything above it, the
    # status handling under test included, runs as it would in production.
    client._client = httpx2.AsyncClient(
        base_url="http://retrieval",
        transport=httpx2.MockTransport(lambda request: httpx2.Response(status, json=body)),
    )
    return client


async def test_a_library_too_large_to_ask_across_is_a_refusal_in_retrievals_words() -> None:
    client = answering(422, {"code": "quota_exceeded", "detail": "Ask a focused question."})

    with pytest.raises(SearchRefused, match="Ask a focused question"):
        await client.search(REQUEST)


async def test_a_request_retrieval_could_not_parse_is_not_shown_as_a_refusal() -> None:
    client = answering(422, {"code": "validation_failed", "detail": "limit: too large"})

    with pytest.raises(httpx2.HTTPStatusError):
        await client.search(REQUEST)


async def test_an_outage_is_still_an_outage() -> None:
    client = answering(503, {"code": "dependency_unavailable", "detail": "Qdrant is down."})

    with pytest.raises(SearchUnavailable, match="Qdrant is down"):
        await client.search(REQUEST)
