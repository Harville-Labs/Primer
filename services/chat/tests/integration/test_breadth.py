"""Asking across a library: the choice reaches Retrieval, and a refusal reaches the user."""

from __future__ import annotations

import pytest
from chat_support import LIBRARY_ID, ChatUser, FakeRetrieval
from primer_chat.clients import SearchRefused

QUESTION = "What are the main themes here?"


async def test_the_breadth_a_question_asks_for_is_the_breadth_it_is_searched_with(
    make_client,
) -> None:
    """On the first question and on a follow-up, which is a separate route."""
    retrieval = FakeRetrieval()
    async with make_client(retrieval=retrieval) as client:
        user = ChatUser(client, "asker")
        events = await user.ask(str(LIBRARY_ID), QUESTION, breadth="library")
        conversation = events[-1]["message"]["conversation_id"]
        await user.follow_up(conversation, "And the methods?", breadth="library")

    assert [search.breadth for search in retrieval.searches] == ["library", "library"]


async def test_a_question_that_does_not_say_is_focused(make_client) -> None:
    """Including a follow-up to a library-wide one: the choice is per question."""
    retrieval = FakeRetrieval()
    async with make_client(retrieval=retrieval) as client:
        user = ChatUser(client, "asker")
        events = await user.ask(str(LIBRARY_ID), QUESTION, breadth="library")
        await user.follow_up(events[-1]["message"]["conversation_id"], "And the methods?")

    assert [search.breadth for search in retrieval.searches] == ["library", "focused"]


@pytest.mark.parametrize("breadth", ["everything", "global"])
async def test_a_breadth_that_is_not_one_of_the_two_is_rejected(make_client, breadth: str) -> None:
    async with make_client() as client:
        response = await ChatUser(client, "asker").post_ask(
            QUESTION, str(LIBRARY_ID), None, breadth
        )

    assert response.status_code == 422


async def test_a_library_too_large_to_ask_across_is_said_so(make_client) -> None:
    """In Retrieval's words, and not as an outage: asking again would not help."""
    refusal = SearchRefused("This library's passages mention entities more than 200,000 times.")
    async with make_client(retrieval=FakeRetrieval(failure=refusal)) as client:
        user = ChatUser(client, "asker")
        events = await user.ask(str(LIBRARY_ID), QUESTION, breadth="library")
        conversation = events[0]["conversation_id"]
        stored = (await user.get(f"/api/v1/conversations/{conversation}/messages")).json()

    error = events[-1]
    assert error["type"] == "error"
    assert error["code"] == "search_refused"
    assert "200,000" in error["detail"]
    assistant = next(m for m in stored if m["role"] == "assistant")
    assert assistant["state"] == "failed"
    assert assistant["error_code"] == "search_refused"
