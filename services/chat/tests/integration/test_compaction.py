"""Remembering a conversation that no longer fits in one.

Dropping the oldest turns is what keeps a long thread inside the window, and
what it costs is the beginning of the thread: the document someone named ten
messages ago, the constraint they gave once. These prove the turns that fall
out are summarized on their way out, that the summary is carried in their
place, and that it never costs the answer - a summarizer that fails is a
conversation with a shorter memory, not a question that goes unanswered.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator, Sequence
from typing import Any

import pytest
import pytest_asyncio
from chat_support import ChatUser, FakeGenerator, deployment
from haystack.dataclasses import ToolCall
from haystack.tools import Tool
from httpx2 import AsyncClient
from primer_chat.config import Settings
from primer_chat.generation import Endpoint, ToolExchange
from primer_chat.rag import SUMMARY_SYSTEM_PROMPT, HistoryTurn
from primer_chat.reasoning import Channel, Fragment
from primer_chat.repository import ChatRepository
from primer_contracts.chat import MessageRole, MessageState
from primer_service.db import Database
from sqlalchemy import text

#: Long enough that a few of them will not share a small window.
QUESTION = (
    "Working through the {subject} again: what did the report actually "
    "establish about it, how was that measured, and which of the earlier "
    "objections does it answer? Please be specific about the numbers, and "
    "say plainly where the evidence stops. "
) * 3


class Summarizer(FakeGenerator):
    """A model that writes something recognisable when asked to compact.

    Summary calls are recorded apart from answering calls. They go to the
    same endpoint, and a test that could not tell them apart would be
    asserting about whichever happened to come first.
    """

    def __init__(
        self,
        summary: str = "Earlier the user asked about the trial and its measurements.",
        *,
        fail_summary: bool = False,
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        self.summary = summary
        self.fail_summary = fail_summary
        #: The prompt each compaction was given, in order.
        self.summaries: list[str] = []

    async def stream(
        self,
        system_prompt: str,
        user_prompt: str,
        *,
        history: tuple[HistoryTurn, ...] = (),
        model: str | None = None,
        endpoint: Endpoint | None = None,
        tools: Sequence[Tool] = (),
        exchanges: tuple[ToolExchange, ...] = (),
    ) -> AsyncIterator[Fragment | ToolCall]:
        if system_prompt == SUMMARY_SYSTEM_PROMPT:
            self.summaries.append(user_prompt)
            if self.fail_summary:
                raise RuntimeError("the summarizer went away")
            yield Fragment(Channel.ANSWER, self.summary)
            return
        async for fragment in super().stream(
            system_prompt,
            user_prompt,
            history=history,
            model=model,
            endpoint=endpoint,
            tools=tools,
            exchanges=exchanges,
        ):
            yield fragment


async def conversation_row(database: Database, conversation_id: str) -> Any:
    """The stored summary, which no endpoint exposes: it is Primer's own note."""
    async with database.engine.connect() as connection:
        result = await connection.execute(
            text("SELECT summary, summary_through_ordinal FROM chat.conversations WHERE id = :id"),
            {"id": conversation_id},
        )
        return result.one()


async def talk(user: ChatUser, subjects: list[str]) -> str:
    """Hold a conversation of several turns, and return its id."""
    events = await user.ask(None, QUESTION.format(subject=subjects[0]))
    conversation_id = events[0]["conversation_id"]
    for subject in subjects[1:]:
        await user.follow_up(conversation_id, QUESTION.format(subject=subject))
    return conversation_id


class TestALongConversation:
    @pytest.fixture
    def settings(self) -> Settings:
        """A window a handful of these turns cannot all fit in."""
        return deployment(chat_context_tokens=1024, chat_reply_tokens=64)

    @pytest.fixture
    def generator(self) -> Summarizer:
        return Summarizer()

    @pytest_asyncio.fixture
    async def client(self, make_client: Any, generator: Summarizer) -> AsyncClient:
        return make_client(generator=generator)

    @pytest.fixture
    def user(self, client: AsyncClient) -> ChatUser:
        return ChatUser(client, "asker")

    async def test_the_turns_that_fall_out_are_summarized(
        self, user: ChatUser, generator: Summarizer, database: Database
    ) -> None:
        """The point of the whole thing: what is dropped is remembered first."""
        conversation_id = await talk(user, ["trial", "dosage", "controls", "follow-up"])

        assert generator.summaries, "nothing was compacted, so nothing was tested"
        summary, through = await conversation_row(database, conversation_id)
        assert summary == generator.summary
        assert through is not None

    async def test_the_summary_is_written_from_the_turns_that_left(
        self, user: ChatUser, generator: Summarizer
    ) -> None:
        """A summary of the turns still being replayed would say it all twice."""
        await talk(user, ["trial", "dosage", "controls", "follow-up"])

        first = generator.summaries[0]
        assert "trial" in first
        assert "follow-up" not in first

    async def test_the_summary_is_shown_to_the_model_afterwards(
        self, user: ChatUser, generator: Summarizer
    ) -> None:
        """Remembering it and not sending it would be an expensive no-op."""
        await talk(user, ["trial", "dosage", "controls", "follow-up"])

        system_prompt, _ = generator.prompts[-1]
        assert "<summary>" in system_prompt
        assert generator.summary in system_prompt

    async def test_a_compacted_turn_is_not_also_replayed(
        self, user: ChatUser, generator: Summarizer
    ) -> None:
        """The summary stands in for those turns rather than accompanying them."""
        await talk(user, ["trial", "dosage", "controls", "follow-up"])

        replayed = " ".join(content for _, content in generator.histories[-1])
        assert "trial" not in replayed
        assert "controls" in replayed

    async def test_compaction_folds_into_what_was_already_remembered(
        self, user: ChatUser, generator: Summarizer
    ) -> None:
        """Re-reading the whole conversation would cost more with every turn."""
        await talk(user, ["trial", "dosage", "controls", "follow-up", "cohort", "endpoints"])

        assert len(generator.summaries) >= 2, "only one compaction happened"
        assert "<summary-so-far>" in generator.summaries[-1]
        assert generator.summary in generator.summaries[-1]

    async def test_the_summary_is_held_to_the_room_reserved_for_it(
        self, make_client: Any, database: Database
    ) -> None:
        """The room was subtracted from the history's budget, so it is enforced."""
        verbose = Summarizer(summary="An account of every detail, at length. " * 200)
        user = ChatUser(make_client(generator=verbose), "asker")

        conversation_id = await talk(user, ["trial", "dosage", "controls", "follow-up"])

        summary, _ = await conversation_row(database, conversation_id)
        assert len(summary) < len(verbose.summary)
        assert summary.endswith("…")

    async def test_a_failed_summary_still_answers_the_question(
        self, make_client: Any, database: Database
    ) -> None:
        """Compaction is bookkeeping. A user waiting for an answer gets one."""
        broken = Summarizer(fail_summary=True)
        user = ChatUser(make_client(generator=broken), "asker")

        conversation_id = await talk(user, ["trial", "dosage", "controls", "follow-up"])

        summary, through = await conversation_row(database, conversation_id)
        assert (summary, through) == (None, None)
        assert broken.prompts, "the question was never asked"


class TestAConversationThatFits:
    @pytest.fixture
    def generator(self) -> Summarizer:
        return Summarizer()

    @pytest_asyncio.fixture
    async def client(self, make_client: Any, generator: Summarizer) -> AsyncClient:
        return make_client(generator=generator)

    @pytest.fixture
    def user(self, client: AsyncClient) -> ChatUser:
        return ChatUser(client, "asker")

    async def test_nothing_is_compacted_while_everything_still_fits(
        self, user: ChatUser, generator: Summarizer, database: Database
    ) -> None:
        """Compaction costs a model call, so it happens only when it must."""
        conversation_id = await talk(user, ["trial", "dosage"])

        assert generator.summaries == []
        assert await conversation_row(database, conversation_id) == (None, None)


class TestCompactionTurnedOff:
    @pytest.fixture
    def settings(self) -> Settings:
        return deployment(
            chat_context_tokens=1024,
            chat_reply_tokens=64,
            chat_compact_history=False,
        )

    @pytest.fixture
    def generator(self) -> Summarizer:
        return Summarizer()

    @pytest_asyncio.fixture
    async def client(self, make_client: Any, generator: Summarizer) -> AsyncClient:
        return make_client(generator=generator)

    @pytest.fixture
    def user(self, client: AsyncClient) -> ChatUser:
        return ChatUser(client, "asker")

    async def test_the_oldest_turns_are_dropped_and_not_summarized(
        self, user: ChatUser, generator: Summarizer, database: Database
    ) -> None:
        """The behaviour before this existed, for a deployment that wants it.

        The history gets more room this way, not less: nothing is reserved
        for a summary that will never be written. It buys that room by
        forgetting, which is the trade an operator is choosing here.
        """
        conversation_id = await talk(
            user, ["trial", "dosage", "controls", "follow-up", "cohort", "endpoints"]
        )

        assert generator.summaries == []
        assert await conversation_row(database, conversation_id) == (None, None)
        replayed = " ".join(content for _, content in generator.histories[-1])
        assert "trial" not in replayed


class TestReadingBackACompactedConversation:
    """The repository half, where the summary decides what is read at all.

    Held apart from the turns above because the interesting case cannot be
    reached through them: a message covered by a summary is usually one that
    would not have fitted anyway, so a conversation that has already been
    compacted proves nothing about whether the summary is what excluded it.
    Here the messages would fit, and are still not read.
    """

    async def test_messages_the_summary_stands_for_are_not_read_back(
        self, database: Database, clean_tables: Any
    ) -> None:
        """Replaying them beside their own summary would say it all twice."""
        async with database.session() as session:
            repository = ChatRepository(session)
            conversation = await repository.create_conversation(
                library_id=None, owner_user_id=uuid.uuid4(), question="First?"
            )
            for content in ("First?", "First answer.", "Second?", "Second answer."):
                await repository.add_message(
                    conversation,
                    role=MessageRole.USER if content.endswith("?") else MessageRole.ASSISTANT,
                    state=MessageState.COMPLETED,
                    content=content,
                )
            await session.commit()

            whole = await repository.history_for(conversation.id, limit=20)
            assert [turn.content for turn in whole] == [
                "First?",
                "First answer.",
                "Second?",
                "Second answer.",
            ]

            # Everything up to and including the first exchange is now the
            # summary's business.
            after = await repository.history_for(conversation.id, limit=20, after_ordinal=1)
            assert [turn.content for turn in after] == ["Second?", "Second answer."]
