"""Tool call persistence and the decision transition.

The decision is a conditional update rather than a read-then-write: two
browser tabs pressing approve and deny at the same moment must resolve to
one answer, and whichever lands second has to be told it lost.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from primer_contracts.chat import ToolCallSummary, ToolPhase
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from primer_chat.models import Conversation, ToolCall


class ToolRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def record_request(
        self,
        *,
        conversation_id: UUID,
        tool_name: str,
        server_name: str,
        arguments: dict[str, Any],
        ttl_seconds: int,
    ) -> ToolCall:
        """Write the audit row when the model asks, not when someone approves.

        A denied or expired request is recorded too: a tool that was refused
        is exactly the kind of thing someone will later want to know was
        refused.
        """
        now = datetime.now(UTC)
        call = ToolCall(
            id=uuid.uuid4(),
            conversation_id=conversation_id,
            tool_name=tool_name,
            server_name=server_name,
            arguments=arguments,
            phase=ToolPhase.REQUESTED.value,
            requested_at=now,
            expires_at=now + timedelta(seconds=ttl_seconds),
        )
        self._session.add(call)
        await self._session.flush()
        await self._session.refresh(call)
        return call

    async def get(self, request_id: UUID, *, owner_user_id: UUID) -> ToolCall | None:
        """Ownership travels in the query, joined through the conversation."""
        result = await self._session.execute(
            select(ToolCall)
            .join(Conversation, ToolCall.conversation_id == Conversation.id)
            .where(ToolCall.id == request_id, Conversation.owner_user_id == owner_user_id)
        )
        return result.scalar_one_or_none()

    async def pending_for(
        self, *, owner_user_id: UUID, conversation_id: UUID | None = None
    ) -> list[ToolCall]:
        statement = (
            select(ToolCall)
            .join(Conversation, ToolCall.conversation_id == Conversation.id)
            .where(
                Conversation.owner_user_id == owner_user_id,
                ToolCall.phase == ToolPhase.REQUESTED.value,
            )
        )
        if conversation_id is not None:
            statement = statement.where(ToolCall.conversation_id == conversation_id)
        result = await self._session.execute(statement.order_by(ToolCall.requested_at))
        return list(result.scalars())

    async def decide(self, call: ToolCall, *, actor: str, approved: bool) -> ToolCall | None:
        """Apply a decision if the request is still open.

        Returns None when nothing changed, which is how a second decision -
        from another tab, or after expiry - is distinguished from the first.
        """
        now = datetime.now(UTC)
        if call.expires_at <= now:
            await self.expire(call, now)
            return None

        target = ToolPhase.APPROVED if approved else ToolPhase.DENIED
        result = await self._session.execute(
            update(ToolCall)
            .where(ToolCall.id == call.id, ToolCall.phase == ToolPhase.REQUESTED.value)
            .values(phase=target.value, decided_at=now, decided_by=actor)
            .returning(ToolCall.id)
        )
        if result.scalar_one_or_none() is None:
            return None
        await self._session.refresh(call)
        return call

    async def phase_of(self, request_id: UUID) -> ToolPhase:
        """Where a call stands now, read fresh rather than from the session.

        A turn waiting for a decision holds the row it wrote, and the
        decision is made by another request on another connection: the
        session's copy is the one thing guaranteed not to have changed.
        """
        result = await self._session.execute(
            select(ToolCall.phase).where(ToolCall.id == request_id)
        )
        return ToolPhase(result.scalar_one())

    async def expire(self, call: ToolCall, now: datetime) -> None:
        """Record the expiry, so the audit row says why nothing ran.

        Committed here rather than left to the request's transaction. The
        route answers this with a 409, and an error response rolls the
        session back - which would discard the very record explaining why
        the user's approval did nothing.
        """
        await self._session.execute(
            update(ToolCall)
            .where(ToolCall.id == call.id, ToolCall.phase == ToolPhase.REQUESTED.value)
            .values(phase=ToolPhase.EXPIRED.value, decided_at=now)
        )
        await self._session.commit()
        await self._session.refresh(call)

    async def record_outcome(
        self,
        call: ToolCall,
        *,
        phase: ToolPhase,
        output: str | None = None,
        error_code: str | None = None,
        duration_ms: int | None = None,
    ) -> ToolCall:
        call.phase = phase.value
        call.output = output
        call.error_code = error_code
        call.duration_ms = duration_ms
        await self._session.flush()
        await self._session.refresh(call)
        return call


def summarize_call(call: ToolCall) -> ToolCallSummary:
    return ToolCallSummary(
        id=call.id,
        conversation_id=call.conversation_id,
        tool_name=call.tool_name,
        server_name=call.server_name,
        arguments=call.arguments,
        phase=ToolPhase(call.phase),
        requested_at=call.requested_at,
        expires_at=call.expires_at,
        decided_at=call.decided_at,
        decided_by=call.decided_by,
        output=call.output,
        error_code=call.error_code,
        duration_ms=call.duration_ms,
    )
