"""A tool call inside a turn: offered, asked for, decided, run, answered.

The turn waits for a person. A model's request is written down and shown,
and nothing runs until the user who asked the question approves it from
another request - the approve route - while this one keeps the stream open.
Waiting inside the stream rather than ending the turn and resuming it later
keeps one answer in one message, written by one request that holds
everything it was built from: the passages, the history, the model.

Whatever happens to the call, the model is told. A refusal, an expiry or a
failure goes back as the result of its call, so the answer it writes next
can say it could not look something up rather than pretend it did.
"""

from __future__ import annotations

import time
from collections.abc import AsyncIterator, Callable
from datetime import UTC, datetime
from typing import Any

import anyio
import anyio.to_thread
from haystack.dataclasses import ToolCall as ModelToolCall
from haystack.tools import Tool
from primer_contracts.chat import (
    Heartbeat,
    ToolCompleted,
    ToolDecided,
    ToolOutput,
    ToolPhase,
    ToolRequested,
    ToolRunning,
)
from primer_contracts.identity import Principal
from sqlalchemy.ext.asyncio import AsyncSession

from primer_chat.generation import ToolExchange
from primer_chat.models import ToolCall
from primer_chat.tool_config import ToolConfiguration
from primer_chat.tool_policy import ApprovalState, ToolPolicy, ToolRequest
from primer_chat.tool_repository import ToolRepository, summarize_call
from primer_chat.tool_runner import ToolRunner, ToolSource, sanitize_arguments

#: How many rounds of calls one answer may make before it has to answer
#: with what it has. A model that keeps asking would otherwise hold the
#: stream, and the user's attention, indefinitely.
MAX_TOOL_ROUNDS = 4

#: What the model is told for each way a call can end without output.
#: Plain sentences, because the model relays them to the reader.
UNAVAILABLE = "That tool is not available here. Answer without it."
DENIED = "The user declined this tool call. Answer without it, and do not ask again."
EXPIRED = "Nobody approved this tool call in time, so it did not run. Answer without it."
FAILED = "The tool call failed and returned nothing usable. Answer without it."

ToolEvent = ToolRequested | ToolDecided | ToolRunning | ToolOutput | ToolCompleted | Heartbeat


_STATES = {
    ToolPhase.REQUESTED: ApprovalState.PENDING,
    ToolPhase.APPROVED: ApprovalState.APPROVED,
    ToolPhase.DENIED: ApprovalState.DENIED,
    ToolPhase.EXPIRED: ApprovalState.EXPIRED,
}


def _as_request(call: ToolCall) -> ToolRequest:
    """The stored row, in the shape the runner re-checks before invoking."""
    return ToolRequest(
        id=call.id,
        conversation_id=call.conversation_id,
        tool_name=call.tool_name,
        arguments=call.arguments,
        state=_STATES.get(ToolPhase(call.phase), ApprovalState.CANCELLED),
        requested_at=call.requested_at,
        ttl_seconds=int((call.expires_at - call.requested_at).total_seconds()),
        decided_at=call.decided_at,
        decided_by=call.decided_by,
    )


class ToolDesk:
    """Where a turn takes a model's tool calls to be decided and run."""

    def __init__(
        self,
        configuration: ToolConfiguration,
        source: ToolSource | None,
        *,
        poll_seconds: float = 0.5,
        heartbeat_seconds: float = 15.0,
    ) -> None:
        self._configuration = configuration
        # An operator who configures tool servers has declared the models
        # behind this deployment can call them. A model that cannot simply
        # never asks, which costs nothing.
        self._policy = ToolPolicy(configuration, model_supports_tools=True)
        self._runner = ToolRunner(configuration, self._policy, source)
        self._source = source
        self._poll_seconds = poll_seconds
        self._heartbeat_seconds = heartbeat_seconds

    async def offered(self, principal: Principal) -> tuple[Tool, ...]:
        """The tools this person's question may use, from servers that answer.

        Filtered through the same policy that decides each call, so a tool
        a principal could never be allowed to run is never shown to the
        model on their behalf.
        """
        if self._source is None or not self._configuration.enabled:
            return ()
        source = self._source
        tools = await anyio.to_thread.run_sync(source.tools)
        return tuple(tool for tool in tools if self._policy.evaluate(principal, tool.name).allowed)

    async def handle(
        self,
        session: AsyncSession,
        *,
        principal: Principal,
        conversation_id: Any,
        call: ModelToolCall,
        next_id: Callable[[], int],
    ) -> AsyncIterator[ToolEvent | ToolExchange]:
        """Take one call through approval and, if approved, run it.

        Yields the events the reader sees, then exactly one `ToolExchange`:
        what the model is told happened.
        """
        decision = self._policy.evaluate(principal, call.tool_name)
        if not decision.allowed or decision.server is None:
            # Not offered, so the model made the name up or kept one from
            # an earlier turn. Nothing to ask anyone about.
            yield ToolExchange(call, UNAVAILABLE, failed=True)
            return

        repository = ToolRepository(session)
        record = await repository.record_request(
            conversation_id=conversation_id,
            tool_name=call.tool_name,
            server_name=decision.server,
            arguments=sanitize_arguments(call.arguments),
            ttl_seconds=self._configuration.approval_ttl_seconds,
        )
        await session.commit()
        yield ToolRequested(id=next_id(), call=summarize_call(record))

        async for beat in self._wait(session, repository, record, next_id):
            yield beat
        await session.refresh(record)
        phase = ToolPhase(record.phase)
        yield ToolDecided(id=next_id(), call=summarize_call(record))

        if phase is ToolPhase.DENIED:
            yield ToolExchange(call, DENIED, failed=True)
            return
        if phase is not ToolPhase.APPROVED:
            yield ToolExchange(call, EXPIRED, failed=True)
            return

        yield ToolRunning(id=next_id(), call_id=record.id)
        request = _as_request(record)
        outcome = await anyio.to_thread.run_sync(lambda: self._runner.run(request))
        record = await repository.record_outcome(
            record,
            phase=outcome.phase,
            output=outcome.output,
            error_code=outcome.error_code,
            duration_ms=outcome.duration_ms,
        )
        await session.commit()
        if outcome.output is not None:
            yield ToolOutput(id=next_id(), call_id=record.id, text=outcome.output)
        yield ToolCompleted(id=next_id(), call=summarize_call(record))

        if outcome.phase is ToolPhase.COMPLETED and outcome.output is not None:
            yield ToolExchange(call, outcome.output)
        elif outcome.phase is ToolPhase.EXPIRED:
            yield ToolExchange(call, EXPIRED, failed=True)
        else:
            yield ToolExchange(call, FAILED, failed=True)

    async def _wait(
        self,
        session: AsyncSession,
        repository: ToolRepository,
        record: ToolCall,
        next_id: Callable[[], int],
    ) -> AsyncIterator[Heartbeat]:
        """Until someone decides or the request runs out of time.

        Each look is its own short transaction, committed straight away: a
        turn can wait minutes here, and a connection left idle inside an
        open transaction for that long holds locks and a pool slot for
        nothing. Heartbeats keep proxies from closing a stream that has
        gone quiet because a person is reading.
        """
        last_beat = time.monotonic()
        while True:
            phase = await repository.phase_of(record.id)
            await session.commit()
            if phase is not ToolPhase.REQUESTED:
                return
            if record.expires_at <= datetime.now(UTC):
                await repository.expire(record, datetime.now(UTC))
                return
            await anyio.sleep(self._poll_seconds)
            if time.monotonic() - last_beat >= self._heartbeat_seconds:
                last_beat = time.monotonic()
                yield Heartbeat(id=next_id())
