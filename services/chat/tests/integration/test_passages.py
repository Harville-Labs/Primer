"""Listing a library's passages, for Lab's scan.

The scan's one access rule lives here: Lab is handed text, and what text a
person may have is decided by Control before Retrieval is asked for any.
"""

from __future__ import annotations

from chat_support import LIBRARY_ID, OTHER_LIBRARY_ID, ChatUser, FakeControl, FakeRetrieval
from httpx2 import AsyncClient
from primer_chat.clients import SearchUnavailable


async def test_a_library_the_person_cannot_read_is_never_listed(
    client: AsyncClient, control: FakeControl, retrieval: FakeRetrieval
) -> None:
    """The issue's acceptance case: no scan of a library you cannot access.

    Answered as not found, the same as a library that does not exist, and
    Retrieval is never asked.
    """
    response = await ChatUser(client, "owner").get(f"/api/v1/libraries/{OTHER_LIBRARY_ID}/passages")

    assert response.status_code == 404
    assert response.json()["code"] == "not_found"
    assert control.asked == [("owner", OTHER_LIBRARY_ID)]
    assert retrieval.listings == []


async def test_a_readable_library_is_listed_within_its_active_generations(
    client: AsyncClient, control: FakeControl, retrieval: FakeRetrieval
) -> None:
    retrieval.contents = ["First.", "Second.", "Third."]

    response = await ChatUser(client, "owner").get(
        f"/api/v1/libraries/{LIBRARY_ID}/passages?limit=2"
    )

    assert response.status_code == 200
    body = response.json()
    assert [p["content"] for p in body["passages"]] == ["First.", "Second."]
    assert body["total"] == 3
    [listing] = retrieval.listings
    assert listing.generation_ids == control.generations
    assert listing.principal.subject == "owner"


async def test_a_library_with_nothing_indexed_lists_nothing_without_asking_retrieval(
    client: AsyncClient, control: FakeControl, retrieval: FakeRetrieval
) -> None:
    """Retrieval refuses an empty scope; asking would turn an empty library into an error."""
    control.generations = ()

    response = await ChatUser(client, "owner").get(f"/api/v1/libraries/{LIBRARY_ID}/passages")

    assert response.status_code == 200
    assert response.json() == {"passages": [], "total": 0}
    assert retrieval.listings == []


async def test_retrieval_being_down_is_said_to_be_worth_retrying(make_client) -> None:
    failing = FakeRetrieval(failure=SearchUnavailable("The vector store could not be reached."))
    async with make_client(retrieval=failing) as client:
        response = await ChatUser(client, "owner").get(f"/api/v1/libraries/{LIBRARY_ID}/passages")

    assert response.status_code == 503
    assert response.json()["code"] == "dependency_unavailable"
    assert "The vector store could not be reached." in response.json()["detail"]
