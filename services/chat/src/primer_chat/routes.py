"""Conversation and streaming routes."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Request, Response, status
from fastapi.responses import StreamingResponse
from primer_contracts.base import WireModel
from primer_contracts.chat import (
    ChatModelList,
    ConversationSummary,
    Message,
    MessageSummary,
)
from primer_contracts.errors import ErrorCode
from primer_contracts.indexing import Breadth
from primer_service.db import get_session
from primer_service.durable import DurableRoute
from primer_service.errors import ProblemError
from pydantic import Field
from sqlalchemy.ext.asyncio import AsyncSession

from primer_chat.config import Settings
from primer_chat.errors import not_found
from primer_chat.generation import Endpoint
from primer_chat.identity import CurrentPrincipal
from primer_chat.model_catalog import catalog
from primer_chat.providers_store import ProviderStore
from primer_chat.repository import ChatRepository, summarize_conversation
from primer_chat.sse import encode
from primer_chat.streaming import Answering, Responder

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1", tags=["chat"], route_class=DurableRoute)

Session = Annotated[AsyncSession, Depends(get_session)]


def get_responder(request: Request) -> Responder:
    responder: Responder = request.app.state.responder
    return responder


Responding = Annotated[Responder, Depends(get_responder)]


class AskRequest(WireModel):
    """A question, and the library it is asked of, if any.

    Without a library the question is answered by the model rather than from
    the user's documents, and the answer carries no citations.
    """

    library_id: UUID | None = None
    #: One of the models this deployment offers, or none for its default.
    model: str | None = Field(default=None, max_length=200)
    #: Which provider serves that model. Model names are not unique across
    #: providers, so a model alone can be ambiguous; without this the first
    #: provider serving the name answers.
    provider_id: UUID | None = None
    message: Message
    #: Whether to answer from the passages nearest the question or from
    #: passages across the whole library. Ignored without a library.
    breadth: Breadth = "focused"


class FollowUpRequest(WireModel):
    model: str | None = Field(default=None, max_length=200)
    provider_id: UUID | None = None
    message: Message
    #: Chosen per question, so a follow-up does not inherit the last one's.
    breadth: Breadth = "focused"


@router.get("/conversations", summary="List the caller's conversations")
async def list_conversations(
    principal: CurrentPrincipal, session: Session, library_id: UUID | None = None
) -> list[ConversationSummary]:
    conversations = await ChatRepository(session).list_conversations(
        owner_user_id=principal.user_id, library_id=library_id
    )
    return [summarize_conversation(conversation) for conversation in conversations]


@router.get("/conversations/{conversation_id}", summary="Read one conversation")
async def read_conversation(
    conversation_id: UUID, principal: CurrentPrincipal, session: Session
) -> ConversationSummary:
    conversation = await ChatRepository(session).get_conversation(
        conversation_id, owner_user_id=principal.user_id
    )
    if conversation is None:
        raise not_found("Conversation")
    return summarize_conversation(conversation)


@router.get("/conversations/{conversation_id}/messages", summary="Read a conversation's turns")
async def list_messages(
    conversation_id: UUID, principal: CurrentPrincipal, session: Session
) -> list[MessageSummary]:
    repository = ChatRepository(session)
    conversation = await repository.get_conversation(
        conversation_id, owner_user_id=principal.user_id
    )
    if conversation is None:
        raise not_found("Conversation")
    return await repository.messages_for(conversation.id)


@router.delete(
    "/conversations/{conversation_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a conversation",
)
async def delete_conversation(
    conversation_id: UUID, principal: CurrentPrincipal, session: Session
) -> Response:
    """Remove a conversation and everything written in it.

    Really removed, not hidden. A conversation is what someone asked and what
    they were told, and a user who deletes one is usually deleting it for a
    reason rather than tidying a list. The messages and their citations go
    with it, by the cascade the schema already declares.

    Ownership is in the query, so another user's conversation is not found
    rather than forbidden: whether it exists is not this caller's business.
    """
    repository = ChatRepository(session)
    conversation = await repository.get_conversation(
        conversation_id, owner_user_id=principal.user_id
    )
    if conversation is None:
        raise not_found("Conversation")
    await repository.delete_conversation(conversation)
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/models", summary="Models this deployment offers")
async def list_models(request: Request, session: Session) -> ChatModelList:
    """Everything every configured provider serves, each tagged with its own.

    A deployment may hold several providers at once, and model names are not
    unique across them, so a model is only fully named by the pair. Each is
    asked in parallel and allowed to fail alone: a hosted API plus a machine
    at home that is asleep still answers, with the sleeping one named rather
    than silently dropped.
    """
    settings: Settings = request.app.state.settings
    store = ProviderStore(session, settings, request.app.state.secret_box)
    return await catalog(await store.enabled(), preferred_model=settings.chat_model)


async def route_for(
    request: Request,
    session: AsyncSession,
    requested_model: str | None,
    provider_id: UUID | None,
) -> tuple[str | None, Endpoint | None, UUID | None]:
    """Which model answers this question, and where it is sent.

    Three cases, in order of how much the request said.

    A request that names a provider gets that provider - it picked from a
    list Primer gave it. An id naming nothing is refused rather than quietly
    answered by the default: the list has changed underneath the caller, and
    answering from somewhere else would attribute one provider's answer to
    another.

    A request that names only a model goes to the deployment's own endpoint,
    which is what every request did before a deployment could hold more than
    one, so older clients keep working.

    A request that names neither, on a deployment that configures no default
    model, is resolved from the catalog: the model that would be shown as
    the default, together with the provider serving it. That costs a listing
    round trip, and it is only paid by the deployments that need it.
    """
    settings: Settings = request.app.state.settings
    store = ProviderStore(session, settings, request.app.state.secret_box)

    if provider_id is not None:
        provider = await store.find(provider_id)
        if provider is None or not provider.enabled:
            raise ProblemError(
                code=ErrorCode.NOT_FOUND,
                title="Provider not found",
                status_code=status.HTTP_404_NOT_FOUND,
                detail="That provider is not available on this deployment.",
            )
        return (
            settings.resolve_model(requested_model),
            Endpoint(base_url=provider.base_url, api_key=provider.api_key),
            provider.id,
        )

    model = settings.resolve_model(requested_model)
    if model is not None:
        # The deployment's own endpoint, as before. None here means nothing
        # is configured, which the generator refuses rather than defaulting.
        # No provider either: that endpoint is configuration, not a row.
        return model, None, None

    listed = await catalog(await store.enabled(), preferred_model=None)
    default = next((entry for entry in listed.models if entry.default), None)
    if default is None:
        raise ProblemError(
            code=ErrorCode.DEPENDENCY_UNAVAILABLE,
            title="No model can answer",
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=listed.detail or "No provider is serving a model.",
        )
    serving = await store.find(default.provider_id) if default.provider_id else None
    return (
        default.id,
        Endpoint(base_url=serving.base_url, api_key=serving.api_key) if serving else None,
        serving.id if serving else None,
    )


@router.post(
    "/conversations",
    status_code=status.HTTP_200_OK,
    summary="Ask a question and stream the answer",
    response_class=StreamingResponse,
)
async def ask(
    payload: AskRequest,
    request: Request,
    principal: CurrentPrincipal,
    session: Session,
    responder: Responding,
) -> StreamingResponse:
    """Start a conversation and stream its first answer.

    The library is authorized inside the stream rather than before it, so a
    forbidden library ends as a terminal `error` event on an open stream. A
    404 here would be equally correct; the stream is chosen so a client has
    exactly one place to handle failures.
    """
    model, endpoint, served_by = await route_for(
        request, session, payload.model, payload.provider_id
    )
    conversation = await ChatRepository(session).create_conversation(
        library_id=payload.library_id,
        owner_user_id=principal.user_id,
        question=payload.message,
    )
    turn = Answering(
        principal=principal,
        conversation=conversation,
        question=payload.message,
        model=model,
        endpoint=endpoint,
        provider_id=served_by,
        breadth=payload.breadth,
    )
    return stream_response(responder, session, turn)


@router.post(
    "/conversations/{conversation_id}/messages",
    summary="Continue a conversation",
    response_class=StreamingResponse,
)
async def follow_up(
    conversation_id: UUID,
    payload: FollowUpRequest,
    request: Request,
    principal: CurrentPrincipal,
    session: Session,
    responder: Responding,
) -> StreamingResponse:
    """Ask another question in an existing conversation.

    Ownership is part of the lookup, so a stranger who knows or guesses the
    id gets the same 404 as for a conversation that does not exist - and
    gets it before Retrieval or the model is touched.
    """
    conversation = await ChatRepository(session).get_conversation(
        conversation_id, owner_user_id=principal.user_id
    )
    if conversation is None:
        raise not_found("Conversation")
    model, endpoint, served_by = await route_for(
        request, session, payload.model, payload.provider_id
    )
    turn = Answering(
        principal=principal,
        conversation=conversation,
        question=payload.message,
        model=model,
        endpoint=endpoint,
        provider_id=served_by,
        breadth=payload.breadth,
    )
    return stream_response(responder, session, turn)


def stream_response(
    responder: Responder, session: AsyncSession, turn: Answering
) -> StreamingResponse:
    async def events() -> AsyncIterator[str]:
        async for event in responder.respond(session, turn):
            yield encode(event)

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            # Nginx and several proxies buffer responses by default, which
            # would hold every token until the answer finished and defeat
            # the point of streaming.
            "X-Accel-Buffering": "no",
        },
    )
