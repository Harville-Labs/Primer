"""Turning a question into a stream of events.

The order of operations is the security property. Authorization happens
first and the answer is never begun without it, so an unauthorized question
reaches neither Retrieval nor a model. Citations are emitted before any text,
because they are known before any text exists - they come from what was
retrieved, not from what is written.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from dataclasses import dataclass
from uuid import UUID

from primer_contracts.chat import (
    CitationEvent,
    MessageCompleted,
    MessageDelta,
    MessageRole,
    MessageStarted,
    MessageState,
    ReasoningDelta,
    StreamError,
)
from primer_contracts.identity import Principal
from primer_contracts.indexing import Breadth, SearchRequest
from sqlalchemy.ext.asyncio import AsyncSession

from primer_chat.budget import estimate_tokens, passages_that_fit, split_history
from primer_chat.clients import (
    LibraryAuthority,
    LibraryForbidden,
    PassageSource,
    SearchFailed,
)
from primer_chat.compaction import Compactor
from primer_chat.config import Settings
from primer_chat.failures import describe
from primer_chat.generation import ChatGenerator, Endpoint
from primer_chat.models import Conversation
from primer_chat.rag import (
    NO_CONTEXT_REPLY,
    SUMMARY_PREAMBLE,
    SYSTEM_PROMPT,
    UNGROUNDED_SYSTEM_PROMPT,
    build_context,
    build_prompt,
    with_summary,
    with_tools,
)
from primer_chat.reasoning import Channel
from primer_chat.repository import ChatRepository, summarize_message

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Answering:
    """Everything one turn needs, resolved before any of it begins."""

    principal: Principal
    conversation: Conversation
    question: str
    #: Already checked against what this deployment offers. None means its
    #: default, which is the same thing a request with no preference gets.
    model: str | None = None
    #: Which provider serves that model, resolved before the turn begins.
    #: None falls back to the endpoint configured for the deployment, which
    #: is what every request did before a deployment could hold several.
    endpoint: Endpoint | None = None
    #: Which provider row that endpoint came from, recorded on the answer so
    #: reopening the conversation can select the same model again. None for
    #: the deployment's own configured endpoint, which is not a provider.
    provider_id: UUID | None = None
    #: Whether the question is about the passages nearest it or about the
    #: library as a whole. Chosen by the user per question, never guessed
    #: from its wording. See ADR 0006.
    breadth: Breadth = "focused"


class Responder:
    """Runs one turn and yields the events it produces."""

    def __init__(
        self,
        settings: Settings,
        control: LibraryAuthority,
        retrieval: PassageSource,
        generator: ChatGenerator,
    ) -> None:
        self._settings = settings
        self._control = control
        self._retrieval = retrieval
        self._generator = generator
        self._compactor = Compactor(settings, generator)

    @property
    def _characters_per_token(self) -> float:
        return self._settings.chat_characters_per_token

    def _window(self, turn: Answering) -> int:
        """The model's context, less the room the answer itself needs."""
        return self._settings.context_tokens(turn.model) - self._settings.chat_reply_tokens

    def _summary_allowance(self, turn: Answering) -> int:
        """The most a summary may cost.

        Never more than a quarter of the window, however large the setting
        is. A summary is a convenience; the passages this question is
        answered from are the point, and a small model must not spend half
        its context remembering a conversation it can no longer ground.
        """
        return min(self._settings.chat_summary_tokens, max(0, self._window(turn) // 4))

    def _summary_room(self, turn: Answering, summary: str | None) -> int:
        """Room set aside for what is remembered of the compacted turns.

        A fixed reservation rather than the length of the summary that
        happens to exist, because compaction runs inside the turn and the
        summary it writes is longer than the one it replaces. Reserving what
        a summary is allowed to grow to keeps the arithmetic true whether or
        not this turn is the one that compacts.
        """
        if self._settings.chat_compact_history:
            reserved = self._summary_allowance(turn)
        elif summary:
            # Compaction was turned off after this conversation had already
            # been compacted. The summary is still carried - forgetting it
            # would lose the turns it stands for - so it is still paid for.
            reserved = estimate_tokens(summary, characters_per_token=self._characters_per_token)
        else:
            return 0
        return reserved + estimate_tokens(
            SUMMARY_PREAMBLE, characters_per_token=self._characters_per_token
        )

    def _room_for_passages(self, turn: Answering, system_prompt: str, summary_room: int) -> int:
        """What the passages may cost, before any history is considered.

        Measured against a prompt with no passages in it, so the question and
        the scaffolding around it are paid for first. History is fitted
        afterwards into whatever the passages leave.
        """
        empty = build_prompt(turn.question, build_context(()))
        return (
            self._window(turn)
            - summary_room
            - estimate_tokens(system_prompt, characters_per_token=self._characters_per_token)
            - estimate_tokens(empty, characters_per_token=self._characters_per_token)
        )

    async def respond(
        self, session: AsyncSession, turn: Answering
    ) -> AsyncIterator[
        MessageStarted
        | MessageDelta
        | ReasoningDelta
        | CitationEvent
        | MessageCompleted
        | StreamError
    ]:
        """Answer one question, emitting events as the answer takes shape."""
        repository = ChatRepository(session)
        conversation = turn.conversation
        event_id = 0

        # Read before this turn's own messages are written, so the question
        # being asked is not also in the history behind it.
        history = await repository.history_for(
            conversation.id,
            limit=self._settings.chat_history_messages,
            after_ordinal=conversation.summary_through_ordinal,
        )
        summary = conversation.summary
        summary_room = self._summary_room(turn, summary)

        def next_id() -> int:
            nonlocal event_id
            current = event_id
            event_id += 1
            return current

        await repository.add_message(
            conversation,
            role=MessageRole.USER,
            state=MessageState.COMPLETED,
            content=turn.question,
        )
        message = await repository.add_message(
            conversation,
            role=MessageRole.ASSISTANT,
            state=MessageState.STREAMING,
            # The model that will answer, recorded on the message. A
            # deployment can offer several and a user can switch between
            # turns, so which one wrote an answer is part of the answer.
            provider_model=turn.model or getattr(self._generator, "model", None),
            provider_id=turn.provider_id,
        )
        await session.commit()

        yield MessageStarted(id=next_id(), message_id=message.id, conversation_id=conversation.id)

        # A conversation with no library is answered by the model alone. There
        # is nothing to authorize and nothing to retrieve, and the prompt says
        # outright that the answer is unsourced.
        grounded = conversation.library_id is not None
        # Tools are off because nothing here can call one: generation never
        # invokes the tool runner. This is the line to change when it does -
        # and it has to be this signal rather than a deployment setting,
        # since telling a model to name its sources when it cannot fetch any
        # is what taught it to invent them.
        system_prompt = with_tools(
            SYSTEM_PROMPT if grounded else UNGROUNDED_SYSTEM_PROMPT, enabled=False
        )
        context = build_context(())

        if grounded:
            # Authorization before retrieval, and retrieval before
            # generation: a question the principal may not ask reaches
            # neither.
            try:
                scope = await self._control.library_scope(turn.principal, conversation.library_id)
            except LibraryForbidden:
                await repository.finish_message(
                    message,
                    state=MessageState.FAILED,
                    content="",
                    error_code="library_unavailable",
                )
                await session.commit()
                yield StreamError(
                    id=next_id(),
                    code="library_unavailable",
                    detail="That library is no longer available to you.",
                )
                return

            chunks = ()
            if scope.generation_ids:
                try:
                    result = await self._retrieval.search(
                        SearchRequest(
                            principal=turn.principal,
                            library_id=conversation.library_id,
                            generation_ids=scope.generation_ids,
                            query=turn.question,
                            limit=self._settings.retrieval_limit,
                            breadth=turn.breadth,
                        )
                    )
                except SearchFailed as failed:
                    # The question is fine; searching for it is not, and
                    # Retrieval has said why. Said plainly, and terminally -
                    # answering from the model alone would produce an
                    # uncited answer for a question asked of a library.
                    await repository.finish_message(
                        message,
                        state=MessageState.FAILED,
                        content="",
                        error_code=failed.code,
                    )
                    await session.commit()
                    yield StreamError(id=next_id(), code=failed.code, detail=str(failed))
                    return
                chunks = result.chunks

            retrieved = build_context(chunks)

            if retrieved.is_empty:
                # Nothing to ground an answer in. Saying so is the answer;
                # asking a model anyway would produce exactly the unsourced
                # prose a library was chosen to avoid. An ungrounded
                # conversation is the case where the user asked for that
                # instead, and it does not come through here.
                completed = await repository.finish_message(
                    message, state=MessageState.COMPLETED, content=NO_CONTEXT_REPLY
                )
                await session.commit()
                yield MessageDelta(id=next_id(), text=NO_CONTEXT_REPLY)
                yield MessageCompleted(id=next_id(), message=summarize_message(completed))
                return

            # Trimmed before the citations are sent, not after. A citation is
            # a claim about what the answer was written from, so emitting one
            # for a passage that then failed to fit would put a source in
            # front of the reader that the model never saw.
            context = retrieved.head(
                passages_that_fit(
                    retrieved,
                    budget=self._room_for_passages(turn, system_prompt, summary_room),
                    characters_per_token=self._settings.chat_characters_per_token,
                )
            )

            if context.is_empty:
                # Retrieval worked; the window is the problem. Said plainly,
                # because "I found nothing" would send the user looking for a
                # document that is there.
                await repository.finish_message(
                    message,
                    state=MessageState.FAILED,
                    content="",
                    error_code="context_exhausted",
                )
                await session.commit()
                yield StreamError(
                    id=next_id(),
                    code="context_exhausted",
                    detail=(
                        "This question leaves no room for the passages found for it. "
                        "Ask something shorter, or choose a model with a larger context."
                    ),
                )
                return

            for index, citation in enumerate(context.citations, start=1):
                yield CitationEvent(id=next_id(), index=index, citation=citation)

        prompt = build_prompt(turn.question, context) if grounded else turn.question

        # What is left of the window after this turn's own prompt. The
        # earlier turns are what gives way: the question being asked now, and
        # the passages it is answered from, are the answer's subject.
        dropped, history = split_history(
            history,
            budget=self._window(turn)
            - summary_room
            - estimate_tokens(system_prompt, characters_per_token=self._characters_per_token)
            - estimate_tokens(prompt, characters_per_token=self._characters_per_token),
            characters_per_token=self._characters_per_token,
        )

        if dropped and self._settings.chat_compact_history:
            # Before answering rather than after, because it is this turn
            # that would otherwise be answered without them. It costs a model
            # call and the user waits for it; a conversation that forgets
            # what it was told is the more expensive of the two.
            written = await self._compactor.compact(
                summary,
                dropped,
                budget=self._summary_allowance(turn),
                model=turn.model,
            )
            if written:
                summary = written
                await repository.set_summary(
                    conversation, summary=summary, through_ordinal=dropped[-1].ordinal
                )
                await session.commit()

        # Last, so everything measured above was measured against the
        # reservation rather than against whatever the summarizer wrote.
        system_prompt = with_summary(system_prompt, summary)

        text = ""
        #: None until the model actually reasons aloud, so a model that does
        #: not is stored as null rather than as an empty thought.
        thinking: str | None = None
        try:
            async for fragment in self._generator.stream(
                system_prompt, prompt, history=history, model=turn.model, endpoint=turn.endpoint
            ):
                if fragment.channel is Channel.REASONING:
                    thinking = (thinking or "") + fragment.text
                    yield ReasoningDelta(id=next_id(), text=fragment.text)
                    continue
                text += fragment.text
                yield MessageDelta(id=next_id(), text=fragment.text)
        except Exception as error:
            # Many different failures share this path and they send whoever
            # reads them to different places: a rejected key is fixed in
            # settings, an unreachable endpoint by starting something, a
            # missing model by choosing another. Reporting them all as a
            # model that stopped mid-answer sends every one of those readers
            # to look at the model, which is usually the one thing that is
            # fine.
            #
            # The generator runs inside a task group, so what arrives here is
            # an ExceptionGroup rather than the exception itself; `describe`
            # walks it.
            code, detail = describe(error)
            # Whatever was written is kept whichever failure this was: it is
            # the only evidence of what went wrong, and a reader can see the
            # answer stops mid-thought.
            logger.exception("message %s: generation failed (%s)", message.id, code)

            await repository.finish_message(
                message,
                state=MessageState.FAILED,
                content=text,
                reasoning=thinking,
                citations=context.citations,
                error_code=code,
            )
            await session.commit()
            yield StreamError(id=next_id(), code=code, detail=detail)
            return

        completed = await repository.finish_message(
            message,
            state=MessageState.COMPLETED,
            content=text,
            reasoning=thinking,
            citations=context.citations,
        )
        await session.commit()
        yield MessageCompleted(
            id=next_id(), message=summarize_message(completed, context.citations)
        )
