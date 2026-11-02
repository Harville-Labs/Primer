"""A turn that stops for a tool call, against a real PostgreSQL.

Driven through the Responder rather than over HTTP: the test client buffers
a streamed response until it ends, and this stream does not end until
somebody decides - which, here, is the test, from a second session, exactly
as the approve route would.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from typing import Any

import pytest
from chat_support import LIBRARY_ID, FakeControl, FakeGenerator, FakeRetrieval, deployment
from haystack.dataclasses import ToolCall as ModelToolCall
from haystack.tools import Tool
from primer_chat.models import ToolCall
from primer_chat.rag import TOOL_GUIDANCE
from primer_chat.repository import ChatRepository
from primer_chat.streaming import Answering, Responder
from primer_chat.tool_config import ToolConfiguration, ToolServer
from primer_chat.tool_repository import ToolRepository
from primer_chat.tool_turn import DENIED, EXPIRED, ToolDesk
from primer_contracts.chat import (
    MessageCompleted,
    ToolCompleted,
    ToolDecided,
    ToolOutput,
    ToolPhase,
    ToolRequested,
    ToolRunning,
)
from primer_contracts.identity import Principal
from primer_service.db import Database
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine

SEARCH = "searxng_web_search"
PRINCIPAL = Principal(subject="owner", user_id=uuid.uuid5(uuid.NAMESPACE_URL, "owner"))


class RecordingSource:
    """A search server, recording every call so a test can prove one never ran."""

    def __init__(self, output: str = "Result: the heron is a wading bird.") -> None:
        self.output = output
        self.invoked: list[tuple[str, dict[str, Any]]] = []

    def tools(self) -> list[Tool]:
        return [
            Tool(
                name=SEARCH,
                description="Search the web.",
                parameters={"type": "object", "properties": {"query": {"type": "string"}}},
                function=lambda **_: "",
            )
        ]

    def invoke(self, tool_name: str, arguments: dict[str, Any]) -> str:
        self.invoked.append((tool_name, arguments))
        return self.output


def configuration(*, ttl: int = 300, groups: tuple[str, ...] = ()) -> ToolConfiguration:
    return ToolConfiguration(
        enabled=True,
        approval_ttl_seconds=ttl,
        servers=(
            ToolServer(
                name="search",
                url="http://search-mcp.invalid/mcp",
                allowed_tools=(SEARCH,),
                required_groups=groups,
            ),
        ),
    )


def searching() -> FakeGenerator:
    return FakeGenerator(
        calls=[ModelToolCall(tool_name=SEARCH, arguments={"query": "heron"}, id="call-1")]
    )


Decide = Callable[[ToolRequested], Any]


async def run_turn(
    database: Database,
    generator: FakeGenerator,
    desk: ToolDesk,
    decide: Decide | None = None,
) -> list[Any]:
    """Answer one question, deciding each tool request as it is shown."""
    responder = Responder(deployment(), FakeControl(), FakeRetrieval(), generator, desk)
    events: list[Any] = []
    async with database.session() as session:
        conversation = await ChatRepository(session).create_conversation(
            library_id=LIBRARY_ID, owner_user_id=PRINCIPAL.user_id, question="What is a heron?"
        )
        await session.commit()
        turn = Answering(
            principal=PRINCIPAL, conversation=conversation, question="What is a heron?"
        )
        async for event in responder.respond(session, turn):
            events.append(event)
            if isinstance(event, ToolRequested) and decide is not None:
                await decide(event)
    return events


def deciding(database: Database, *, approved: bool) -> Decide:
    async def decide(event: ToolRequested) -> None:
        async with database.session() as session:
            repository = ToolRepository(session)
            call = await repository.get(event.call.id, owner_user_id=PRINCIPAL.user_id)
            assert call is not None
            assert await repository.decide(call, actor="owner", approved=approved) is not None
            await session.commit()

    return decide


def desk(source: RecordingSource | None, **kwargs: Any) -> ToolDesk:
    return ToolDesk(configuration(**kwargs), source, poll_seconds=0.01)


async def stored(database: Database) -> ToolCall:
    async with database.session() as session:
        return (await session.execute(select(ToolCall))).scalar_one()


async def test_an_approved_call_runs_and_its_result_reaches_the_model(
    database: Database, clean_tables: AsyncEngine
) -> None:
    source = RecordingSource()
    generator = searching()

    events = await run_turn(database, generator, desk(source), deciding(database, approved=True))

    assert source.invoked == [(SEARCH, {"query": "heron"})]
    kinds = [type(event) for event in events if type(event).__name__.startswith("Tool")]
    assert kinds == [ToolRequested, ToolDecided, ToolRunning, ToolOutput, ToolCompleted]
    # The second round is the model reading what it asked for.
    assert len(generator.exchanges) == 2
    (exchange,) = generator.exchanges[1]
    assert exchange.result == source.output
    assert not exchange.failed
    assert isinstance(events[-1], MessageCompleted)
    assert (await stored(database)).phase == ToolPhase.COMPLETED.value


async def test_a_denied_call_never_runs_and_the_model_is_told(
    database: Database, clean_tables: AsyncEngine
) -> None:
    """The model has to know, or its answer pretends it looked something up."""
    source = RecordingSource()
    generator = searching()

    events = await run_turn(database, generator, desk(source), deciding(database, approved=False))

    assert source.invoked == []
    assert not any(isinstance(event, ToolRunning) for event in events)
    (exchange,) = generator.exchanges[1]
    assert exchange.result == DENIED
    assert exchange.failed
    assert isinstance(events[-1], MessageCompleted)


async def test_a_call_nobody_answers_expires_without_running(
    database: Database, clean_tables: AsyncEngine
) -> None:
    source = RecordingSource()
    generator = searching()

    events = await run_turn(database, generator, desk(source, ttl=1))

    assert source.invoked == []
    decided = next(event for event in events if isinstance(event, ToolDecided))
    assert decided.call.phase is ToolPhase.EXPIRED
    (exchange,) = generator.exchanges[1]
    assert exchange.result == EXPIRED
    assert (await stored(database)).phase == ToolPhase.EXPIRED.value


async def test_a_tool_the_person_may_not_use_is_never_offered(
    database: Database, clean_tables: AsyncEngine
) -> None:
    """Offering it would let the model ask for something no approval can allow."""
    generator = searching()

    events = await run_turn(database, generator, desk(RecordingSource(), groups=("staff",)))

    assert generator.offered == [()]
    assert not any(isinstance(event, ToolRequested) for event in events)
    assert isinstance(events[-1], MessageCompleted)


@pytest.mark.parametrize(("source", "mentioned"), [(None, False), (RecordingSource(), True)])
async def test_the_prompt_mentions_tools_only_when_some_are_offered(
    database: Database,
    clean_tables: AsyncEngine,
    source: RecordingSource | None,
    mentioned: bool,
) -> None:
    """A model told it can fetch sources when it cannot is how it learns to invent them."""
    generator = FakeGenerator()

    await run_turn(database, generator, desk(source))

    assert (TOOL_GUIDANCE in generator.prompts[0][0]) is mentioned
