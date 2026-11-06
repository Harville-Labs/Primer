"""Keeping an answer whose reader left before it finished.

A reader who closes the tab cancels the stream mid-answer. The turn's own
failure handling cannot see that - cancellation is not an `Exception`, and
must not be swallowed as one - so the answer used to stay STREAMING for good,
and reopening the conversation showed nothing of what had been written.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from uuid import UUID

from primer_contracts.chat import (
    Citation,
    CitationEvent,
    MessageCompleted,
    MessageDelta,
    MessageStarted,
    MessageState,
    ReasoningDelta,
    StreamError,
)
from sqlalchemy.ext.asyncio import AsyncSession

from primer_chat.models import Message
from primer_chat.repository import ChatRepository
from primer_chat.tool_turn import ToolEvent

logger = logging.getLogger(__name__)

#: Stored on an answer whose reader left before it finished.
INTERRUPTED = "interrupted"

Event = (
    MessageStarted
    | MessageDelta
    | ReasoningDelta
    | CitationEvent
    | MessageCompleted
    | StreamError
    | ToolEvent
)


@dataclass
class Relayed:
    """What a reader has been sent of one answer so far.

    Kept outside the turn rather than read from its locals, because the turn
    is what was cancelled: once it is, nothing it held can be trusted to have
    reached the database, and this is what the reader actually saw.
    """

    message_id: UUID | None = None
    text: str = ""
    thinking: str | None = None
    citations: list[Citation] = field(default_factory=list)

    def note(self, event: Event) -> None:
        if isinstance(event, MessageStarted):
            self.message_id = event.message_id
        elif isinstance(event, MessageDelta):
            self.text += event.text
        elif isinstance(event, ReasoningDelta):
            self.thinking = (self.thinking or "") + event.text
        elif isinstance(event, CitationEvent):
            self.citations.append(event.citation)


async def abandon(session: AsyncSession, relayed: Relayed) -> None:
    """Store an interrupted answer as failed, keeping what was sent.

    Reloaded first: the turn may have finished in the moment before the
    reader left, and a completed answer must not be marked failed. Run while
    a cancellation is unwinding, so a failure here is logged rather than
    raised - raising would replace the cancellation with an unrelated error.
    """
    if relayed.message_id is None:
        return
    try:
        await session.rollback()
        message = await session.get(Message, relayed.message_id, populate_existing=True)
        if message is None or message.state != MessageState.STREAMING.value:
            return
        await ChatRepository(session).finish_message(
            message,
            state=MessageState.FAILED,
            content=relayed.text,
            reasoning=relayed.thinking,
            citations=tuple(relayed.citations),
            error_code=INTERRUPTED,
        )
        await session.commit()
    except Exception:
        logger.exception("message %s: could not store the interrupted answer", relayed.message_id)
