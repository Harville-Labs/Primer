"""Coming back to a conversation, and getting rid of one.

A conversation that can be resumed is also one that accumulates, so the two
belong together: what is listed can be reopened, and what is reopened can be
thrown away. Both are asked in one caller's name - the list is the caller's
own, and a stranger's conversation is not found rather than forbidden.
"""

from __future__ import annotations

import uuid

import pytest
from chat_support import LIBRARY_ID, ChatUser, FakeGenerator
from httpx2 import AsyncClient


@pytest.fixture
def owner(client: AsyncClient) -> ChatUser:
    return ChatUser(client, "owner")


@pytest.fixture
def stranger(client: AsyncClient) -> ChatUser:
    return ChatUser(client, "stranger")


async def opened(user: ChatUser, question: str, library: str | None = str(LIBRARY_ID)) -> str:
    events = await user.ask(library, question)
    return str(events[0]["conversation_id"])


async def test_a_conversation_is_listed_under_the_question_that_started_it(
    owner: ChatUser,
) -> None:
    """The title is what a person recognises a thread by."""
    await opened(owner, "What did the trial establish?")

    listed = (await owner.get("/api/v1/conversations")).json()

    assert [conversation["title"] for conversation in listed] == ["What did the trial establish?"]


async def test_the_list_is_one_caller_s_own(owner: ChatUser, stranger: ChatUser) -> None:
    """Ownership is in the query, so there is nothing to filter afterwards."""
    await opened(owner, "What did the trial establish?")

    assert (await stranger.get("/api/v1/conversations")).json() == []


async def test_reopening_a_conversation_returns_its_turns_in_order(
    owner: ChatUser, generator: FakeGenerator
) -> None:
    """What was asked and what was answered, as they were written."""
    conversation_id = await opened(owner, "What did the trial establish?")
    await owner.follow_up(conversation_id, "And the dosage?")

    messages = (await owner.get(f"/api/v1/conversations/{conversation_id}/messages")).json()

    assert [(message["role"], message["content"]) for message in messages] == [
        ("user", "What did the trial establish?"),
        ("assistant", generator.answer),
        ("user", "And the dosage?"),
        ("assistant", generator.answer),
    ]


async def test_a_reopened_answer_still_carries_what_it_cited(owner: ChatUser) -> None:
    """A citation is a fact about the answer, so it survives the tab closing."""
    conversation_id = await opened(owner, "What did the trial establish?")

    messages = (await owner.get(f"/api/v1/conversations/{conversation_id}/messages")).json()
    answer = next(message for message in messages if message["role"] == "assistant")

    assert answer["citations"], "the stored answer lost its sources"
    assert answer["citations"][0]["excerpt"]
    assert answer["citations"][0]["found_by"] == "graph"
    assert answer["citations"][0]["graph_entities"] == ["trial"]


async def test_a_conversation_with_no_library_says_so(owner: ChatUser) -> None:
    """Null is the answer, not an omission: it is how the thread was opened."""
    await opened(owner, "What is the capital of France?", library=None)

    listed = (await owner.get("/api/v1/conversations")).json()

    assert listed[0]["library_id"] is None


async def test_deleting_a_conversation_takes_its_messages_with_it(owner: ChatUser) -> None:
    """Really gone. Someone deleting a conversation is not tidying a list."""
    conversation_id = await opened(owner, "What did the trial establish?")

    response = await owner.delete(f"/api/v1/conversations/{conversation_id}")

    assert response.status_code == 204
    assert (await owner.get("/api/v1/conversations")).json() == []
    assert (await owner.get(f"/api/v1/conversations/{conversation_id}/messages")).status_code == 404


async def test_a_stranger_cannot_delete_someone_elses_conversation(
    owner: ChatUser, stranger: ChatUser
) -> None:
    """Not found rather than forbidden: whether it exists is not their business."""
    conversation_id = await opened(owner, "What did the trial establish?")

    response = await stranger.delete(f"/api/v1/conversations/{conversation_id}")

    assert response.status_code == 404
    assert len((await owner.get("/api/v1/conversations")).json()) == 1


async def test_deleting_a_conversation_that_is_not_there(owner: ChatUser) -> None:
    assert (await owner.delete(f"/api/v1/conversations/{uuid.uuid4()}")).status_code == 404
