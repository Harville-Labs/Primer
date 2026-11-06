"""A reader who leaves mid-answer still leaves the answer behind them."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import Any

from chat_support import LIBRARY_ID, ChatUser, FakeGenerator
from primer_chat.reasoning import Channel, Fragment


class StallingGenerator(FakeGenerator):
    """Writes one fragment, then waits for a reader who is no longer there."""

    def __init__(self) -> None:
        super().__init__(fragments=["Partial "])
        self.relayed = asyncio.Event()

    async def stream(self, *args: Any, **kwargs: Any) -> AsyncIterator[Fragment]:
        yield Fragment(Channel.ANSWER, "Partial ")
        # Resumed only once the fragment has gone out to the reader.
        self.relayed.set()
        await asyncio.Event().wait()


async def test_a_disconnected_reader_leaves_a_failed_answer_with_what_they_saw(
    make_client,
) -> None:
    """Closing the tab used to leave the answer STREAMING for good.

    Cancellation is not an `Exception`, so the turn's failure handling never
    ran: the message was neither finished nor failed, and reopening the
    conversation showed nothing of what had been written.
    """
    stalling = StallingGenerator()

    async with make_client(generator=stalling) as http:
        user = ChatUser(http, "asker")
        asking = asyncio.create_task(user.ask(str(LIBRARY_ID), "What is the conclusion?"))
        await asyncio.wait_for(stalling.relayed.wait(), timeout=10)
        asking.cancel()
        await asyncio.gather(asking, return_exceptions=True)

        conversations = (await user.get("/api/v1/conversations")).json()
        stored = (await user.get(f"/api/v1/conversations/{conversations[0]['id']}/messages")).json()

    assistant = next(m for m in stored if m["role"] == "assistant")
    assert assistant["state"] == "failed"
    assert assistant["error_code"] == "interrupted"
    assert assistant["content"] == "Partial "
    # The citations went out before the text, so they were seen too.
    assert assistant["citations"]
