"""Conversation and message persistence.

Every read takes the principal's user id. A conversation is addressed by a
UUID that a stranger could guess or be shown, so ownership is part of the
query rather than a check afterwards.
"""

from __future__ import annotations

import uuid
from uuid import UUID

from primer_contracts.chat import (
    Citation,
    ConversationSummary,
    MessageRole,
    MessageState,
    MessageSummary,
)
from primer_contracts.retrieval import SourceLocator
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from primer_chat.models import Conversation, Message, MessageCitation
from primer_chat.rag import HistoryTurn

#: A conversation is titled from its opening question, which is what a person
#: recognises it by. Long questions are cut rather than summarised: a
#: truncated real sentence is honest, an invented summary is not.
TITLE_LENGTH = 80


def title_for(question: str) -> str:
    text = " ".join(question.split())
    return text if len(text) <= TITLE_LENGTH else text[: TITLE_LENGTH - 1] + "…"


class ChatRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create_conversation(
        self, *, library_id: UUID | None, owner_user_id: UUID, question: str
    ) -> Conversation:
        conversation = Conversation(
            id=uuid.uuid4(),
            library_id=library_id,
            owner_user_id=owner_user_id,
            title=title_for(question),
        )
        self._session.add(conversation)
        await self._session.flush()
        await self._session.refresh(conversation)
        return conversation

    async def get_conversation(
        self, conversation_id: UUID, *, owner_user_id: UUID
    ) -> Conversation | None:
        """Ownership is in the query, so another user's id simply matches nothing."""
        result = await self._session.execute(
            select(Conversation).where(
                Conversation.id == conversation_id,
                Conversation.owner_user_id == owner_user_id,
            )
        )
        return result.scalar_one_or_none()

    async def list_conversations(
        self, *, owner_user_id: UUID, library_id: UUID | None = None
    ) -> list[Conversation]:
        statement = select(Conversation).where(Conversation.owner_user_id == owner_user_id)
        if library_id is not None:
            statement = statement.where(Conversation.library_id == library_id)
        result = await self._session.execute(statement.order_by(Conversation.updated_at.desc()))
        return list(result.scalars())

    async def delete_conversation(self, conversation: Conversation) -> None:
        """Remove it, with the messages and citations that hang off it.

        A real delete rather than a flag. Chat stores what a person asked and
        what a model said back; keeping a copy of that after they asked for
        it to go would be the wrong default for a deployment someone runs
        themselves.
        """
        await self._session.delete(conversation)
        await self._session.flush()

    async def add_message(
        self,
        conversation: Conversation,
        *,
        role: MessageRole,
        state: MessageState,
        content: str = "",
        provider_model: str | None = None,
        provider_id: UUID | None = None,
    ) -> Message:
        message = Message(
            id=uuid.uuid4(),
            conversation_id=conversation.id,
            ordinal=await self._next_ordinal(conversation.id),
            role=role.value,
            state=state.value,
            content=content,
            provider_model=provider_model,
            provider_id=provider_id,
        )
        self._session.add(message)
        await self._session.flush()
        await self._session.refresh(message)
        return message

    async def _next_ordinal(self, conversation_id: UUID) -> int:
        """The next position in a conversation.

        Read rather than counted from what this session has added, because a
        turn is written across two calls and a conversation can be resumed by
        a process that has never seen its earlier ones.
        """
        result = await self._session.execute(
            select(func.coalesce(func.max(Message.ordinal), -1)).where(
                Message.conversation_id == conversation_id
            )
        )
        return int(result.scalar_one()) + 1

    async def finish_message(
        self,
        message: Message,
        *,
        state: MessageState,
        content: str,
        reasoning: str | None = None,
        citations: tuple[Citation, ...] = (),
        error_code: str | None = None,
    ) -> Message:
        """Record the terminal state, with whatever text was produced.

        A failed stream keeps the text it managed to write. Discarding it
        would throw away the only evidence of what went wrong, and a reader
        can see for themselves that the answer stops mid-thought. The
        thinking is kept for the same reason, and for a failure it is often
        the more useful half: a model that never stopped deliberating
        produced no answer to look at.
        """
        message.state = state.value
        message.content = content
        message.reasoning = reasoning
        message.error_code = error_code
        for ordinal, citation in enumerate(citations, start=1):
            self._session.add(
                MessageCitation(
                    id=uuid.uuid4(),
                    message_id=message.id,
                    ordinal=ordinal,
                    document_id=citation.document_id,
                    document_version_id=citation.document_version_id,
                    chunk_id=citation.chunk_id,
                    page=citation.locator.page if citation.locator else None,
                    section=citation.locator.section if citation.locator else None,
                    excerpt=citation.excerpt,
                    found_by=citation.found_by,
                    graph_entities=list(citation.graph_entities),
                )
            )
        await self._session.flush()
        await self._session.refresh(message)
        return message

    async def history_for(
        self, conversation_id: UUID, *, limit: int, after_ordinal: int | None = None
    ) -> tuple[HistoryTurn, ...]:
        """The recent turns of a conversation, oldest first.

        Only completed messages with text. A message still streaming is the
        one being written right now, and a failed one is a fragment that
        stops mid-sentence - replaying either teaches the model that
        half-finished answers are what this conversation looks like.

        The bound is applied newest-first and the result reversed, so a long
        conversation keeps its recent turns rather than its opening ones.

        `after_ordinal` is how far a summary already reaches. Messages up to
        it are not read: they are what the summary stands in for, and
        replaying them beside it would say everything twice.
        """
        if limit <= 0:
            return ()
        statement = select(Message).where(
            Message.conversation_id == conversation_id,
            Message.state == MessageState.COMPLETED.value,
            Message.content != "",
        )
        if after_ordinal is not None:
            statement = statement.where(Message.ordinal > after_ordinal)
        result = await self._session.execute(
            statement.order_by(Message.ordinal.desc()).limit(limit)
        )
        recent = list(result.scalars())[::-1]
        return tuple(
            HistoryTurn(
                role=MessageRole(message.role),
                content=message.content,
                ordinal=message.ordinal,
            )
            for message in recent
        )

    async def set_summary(
        self, conversation: Conversation, *, summary: str, through_ordinal: int
    ) -> None:
        """Record what is remembered of the conversation up to a position.

        Both together, always. A summary without the position it reaches
        would be replayed alongside the very messages it replaces.
        """
        conversation.summary = summary
        conversation.summary_through_ordinal = through_ordinal
        await self._session.flush()

    async def messages_for(self, conversation_id: UUID) -> list[MessageSummary]:
        result = await self._session.execute(
            select(Message)
            .where(Message.conversation_id == conversation_id)
            .order_by(Message.ordinal)
        )
        messages = list(result.scalars())
        citations = await self._citations_for([message.id for message in messages])
        return [summarize_message(message, citations.get(message.id, ())) for message in messages]

    async def _citations_for(self, message_ids: list[UUID]) -> dict[UUID, tuple[Citation, ...]]:
        if not message_ids:
            return {}
        result = await self._session.execute(
            select(MessageCitation)
            .where(MessageCitation.message_id.in_(message_ids))
            .order_by(MessageCitation.message_id, MessageCitation.ordinal)
        )
        grouped: dict[UUID, list[Citation]] = {}
        for row in result.scalars():
            grouped.setdefault(row.message_id, []).append(
                Citation(
                    document_id=row.document_id,
                    document_version_id=row.document_version_id,
                    chunk_id=row.chunk_id,
                    locator=SourceLocator(page=row.page, section=row.section),
                    excerpt=row.excerpt,
                    found_by=row.found_by,
                    graph_entities=tuple(row.graph_entities or ()),
                )
            )
        return {key: tuple(value) for key, value in grouped.items()}


def summarize_conversation(conversation: Conversation) -> ConversationSummary:
    return ConversationSummary(
        id=conversation.id,
        library_id=conversation.library_id,
        owner_user_id=conversation.owner_user_id,
        title=conversation.title,
        created_at=conversation.created_at,
        updated_at=conversation.updated_at,
    )


def summarize_message(message: Message, citations: tuple[Citation, ...] = ()) -> MessageSummary:
    return MessageSummary(
        id=message.id,
        conversation_id=message.conversation_id,
        role=MessageRole(message.role),
        state=MessageState(message.state),
        content=message.content,
        reasoning=message.reasoning,
        citations=citations,
        provider_model=message.provider_model,
        provider_id=message.provider_id,
        error_code=message.error_code,
        created_at=message.created_at,
    )
